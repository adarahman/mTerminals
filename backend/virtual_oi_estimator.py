"""
virtual_oi_estimator.py
───────────────────────
Fills the NSE 3-minute OI update cooling gap using ML-predicted next-
snapshot OI deltas, computed from each poll cycle's own real deltas
(ce_vol_delta, pe_vol_delta, ce_oi_delta_lag, pe_oi_delta_lag,
ce_iv_delta, pe_iv_delta, minutes_since_last) — the exact schema
build_training_warehouse.py trains on. One CE and one PE HuberRegressor
pipeline are trained independently (production_oi_pipeline_ce.pkl /
_pe.pkl); the coordinator routes each strike/side to its own pipeline.

Surface modes (pick at render time):
  • virtual_oi_accumulator  – intrabar delta, resets on NSE refresh
  • virtual_oi_running      – running delta stacked on last confirmed OI
  • virtual_oi_absolute     – standalone field, independent of confirmed OI

Drift policy: Z-score breach → confidence_weight scaled down proportionally.
The estimate still emits; consumer decides whether to render or mute.
"""

import time
import numpy as np
import pandas as pd
import joblib
import os
from dataclasses import dataclass, field
from typing import Optional


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

NSE_COOLING_SECONDS   = 180       # 3-minute OI refresh cycle
TICK_INTERVAL_SECONDS = 5         # L1 WebSocket cadence
MAX_TICKS_PER_CYCLE   = NSE_COOLING_SECONDS // TICK_INTERVAL_SECONDS  # 36 ticks

DRIFT_Z_THRESHOLD     = 3.0       # Z-score boundary from MLOpsDriftMonitor
DRIFT_CONFIDENCE_FLOOR = 0.20     # Never let confidence drop below 20%

# Linear confidence decay: starts at 1.0, reaches DECAY_FLOOR by last tick
TICK_DECAY_FLOOR      = 0.75      # Time-decay floor within a single cycle


# ─────────────────────────────────────────────
# Output container — all three surfaces live here
# ─────────────────────────────────────────────

@dataclass
class VirtualOIFrame:
    """
    Emitted once per poll cycle. Consumer selects the surface it needs.

    Surface semantics:
      accumulator  – OI delta accumulated since last NSE confirmation (resets on refresh)
      running      – last_confirmed_oi + accumulated delta
      absolute     – standalone ML estimate, not anchored to confirmed OI

    confidence_weight ∈ [0.20, 1.00]:
      1.00  = clean signal, no drift, early in cycle
      <1.00 = drift detected OR late in cycle (time-decay)
      0.20  = floor — severely drifted or stale
    """
    # Tick metadata
    symbol:               str
    timestamp:            float
    tick_index:           int          # 0-based within current 3-min cycle
    seconds_since_refresh: float

    # Raw ML output
    predicted_oi_delta_next: float     # HuberRegressor output — predicted next-snapshot OI delta
    delta_volume_tick:    float        # volume delta on this tick

    # Three render surfaces
    virtual_oi_accumulator: float      # Σ estimated_change since last confirmation
    virtual_oi_running:     float      # last_confirmed_oi + accumulator
    virtual_oi_absolute:    float      # standalone ML estimate (not anchored)

    # Signal quality
    confidence_weight:    float        # [DRIFT_CONFIDENCE_FLOOR, 1.0]
    drift_detected:       bool
    drift_alerts:         list         # populated when drift_detected=True
    is_stale:             bool         # True if NSE hasn't refreshed in > NSE_COOLING_SECONDS


# ─────────────────────────────────────────────
# Core engine
# ─────────────────────────────────────────────

class VirtualOIEstimator:
    """
    Stateful per-symbol estimator. One instance per strike/expiry.

    Usage:
        estimator = VirtualOIEstimator("NIFTY2460623500", "CE", ce_ml_model, drift_monitor)

        # On every poll cycle, with this snapshot's own real deltas:
        frame = estimator.on_tick({
            "ce_vol_delta": ..., "pe_vol_delta": ...,
            "ce_oi_delta_lag": ..., "pe_oi_delta_lag": ...,
            "ce_iv_delta": ..., "pe_iv_delta": ...,
        })

        # On NSE OI refresh:
        estimator.on_nse_refresh(confirmed_oi=new_oi_value)
    """

    def __init__(
        self,
        symbol: str,
        side: str,                       # "CE" or "PE" — which trained pipeline this instance uses
        ml_model,                        # loaded production pipeline (joblib) for that side
        drift_monitor: "MLOpsDriftMonitor",
        initial_confirmed_oi: float = 0.0,
    ):
        self.symbol               = symbol
        self.side                 = side.upper()
        self.ml_model             = ml_model
        self.drift_monitor        = drift_monitor

        # Confirmed OI anchor (updated on every NSE refresh)
        self.last_confirmed_oi    = initial_confirmed_oi
        self.last_refresh_ts      = time.time()

        # Intrabar accumulator — resets on NSE refresh
        self._accumulator         = 0.0

        # Standalone absolute tracker — does NOT reset; tracks independent ML path
        self._absolute_tracker    = initial_confirmed_oi

        # Tick counter within current cycle
        self._tick_index          = 0

        # Last valid predicted efficiency (carry-forward on model error)
        self._last_efficiency     = 0.0

    # ── public interface ──────────────────────────────────────────────────

    def on_tick(self, tick_features: dict) -> VirtualOIFrame:
        """
        Called once per poll cycle with the CURRENT snapshot's own
        real deltas — the same columns oi_analysis.build_oi_history()
        writes and build_training_warehouse.py trains on:

            ce_vol_delta, pe_vol_delta, ce_oi_delta_lag, pe_oi_delta_lag,
            ce_iv_delta, pe_iv_delta

        ("_lag" here means "this snapshot's own delta", used as a feature
        to predict the *next* snapshot's delta — matching the training
        target definition in build_training_warehouse.py.)

        minutes_since_last is NOT supplied by the caller — it's derived
        here from this estimator's own last_refresh_ts, which is exactly
        the same quantity (gap since the last real snapshot) that
        build_training_warehouse.py computed from consecutive log rows.
        """
        now = time.time()
        seconds_since_refresh = now - self.last_refresh_ts
        is_stale = seconds_since_refresh > NSE_COOLING_SECONDS
        minutes_since_last = seconds_since_refresh / 60.0

        # Full real feature set — caller-supplied deltas plus the
        # internally-tracked recency feature. Used for both the drift
        # check and inference so both see the exact same schema.
        full_features = dict(tick_features)
        full_features["minutes_since_last"] = minutes_since_last

        # ── 1. Drift check ────────────────────────────────────────────────
        drift_result = self.drift_monitor.verify_feature_drift(full_features)
        drift_detected = drift_result["status"] == "DRIFT_DETECTED"
        drift_alerts   = drift_result.get("drift_alerts", [])

        # ── 2. Confidence weight ──────────────────────────────────────────
        confidence_weight = self._compute_confidence(
            tick_index=self._tick_index,
            drift_detected=drift_detected,
            drift_alerts=drift_alerts,
            tick_features=full_features,
        )

        # ── 3. ML inference ───────────────────────────────────────────────
        # delta_volume_tick reported on the frame is whichever side's volume
        # delta this estimator is tracking (CE or PE — see self.side).
        delta_volume = full_features.get(
            "ce_vol_delta" if self.side == "CE" else "pe_vol_delta", 0.0
        )
        predicted_oi_delta_next, estimated_change = self._infer(full_features)

        # ── 4. Update all three surfaces ──────────────────────────────────
        # Confidence-scaled change — drifted/stale snapshots contribute less
        scaled_change = estimated_change * confidence_weight

        self._accumulator     += scaled_change
        self._absolute_tracker += scaled_change

        virtual_oi_running   = self.last_confirmed_oi + self._accumulator
        virtual_oi_absolute  = self._absolute_tracker

        # ── 5. Advance tick counter ───────────────────────────────────────
        self._tick_index = min(self._tick_index + 1, MAX_TICKS_PER_CYCLE)

        return VirtualOIFrame(
            symbol                 = self.symbol,
            timestamp              = now,
            tick_index             = self._tick_index,
            seconds_since_refresh  = seconds_since_refresh,
            predicted_oi_delta_next= predicted_oi_delta_next,
            delta_volume_tick      = delta_volume,
            virtual_oi_accumulator = self._accumulator,
            virtual_oi_running     = virtual_oi_running,
            virtual_oi_absolute    = virtual_oi_absolute,
            confidence_weight      = confidence_weight,
            drift_detected         = drift_detected,
            drift_alerts           = drift_alerts,
            is_stale               = is_stale,
        )

    def on_nse_refresh(self, confirmed_oi: float):
        """
        Call this when NSE emits a fresh OI value.
        Reconciles accumulator drift vs confirmed delta, then resets state.
        """
        confirmed_delta = confirmed_oi - self.last_confirmed_oi

        # Reconciliation: how far off was our virtual accumulator?
        if abs(self._accumulator) > 0:
            drift_ratio = abs(confirmed_delta - self._accumulator) / (abs(self._accumulator) + 1e-9)
        else:
            drift_ratio = 0.0

        # Feed reconciliation signal back as a log (hook into your alerting)
        if drift_ratio > 0.30:
            print(
                f"[VirtualOI] {self.symbol} | Reconciliation drift {drift_ratio*100:.1f}% "
                f"| Virtual: {self._accumulator:+.0f} | Confirmed: {confirmed_delta:+.0f}"
            )

        # Anchor absolute tracker to confirmed ground truth
        self._absolute_tracker = confirmed_oi

        # Reset cycle state
        self.last_confirmed_oi = confirmed_oi
        self.last_refresh_ts   = time.time()
        self._accumulator      = 0.0
        self._tick_index       = 0

    # ── private helpers ───────────────────────────────────────────────────

    def _infer(self, full_features: dict):
        """
        Run the trained CE/PE HuberRegressor pipeline (self.ml_model — the
        correct side's pipeline, selected by the coordinator at dispatch
        time). Feature vector is built in the exact FEATURES order the
        pipeline was trained on (see FEATURES near the bottom of this
        module, shared with build_training_warehouse.py's schema).

        The model already outputs a predicted OI-delta directly (that's
        the training target, e.g. ce_oi_delta_next) — no extra
        volume-multiplication step is needed or correct here.
        """
        try:
            # Built as a DataFrame (not a bare ndarray) with the same
            # column names/order the pipeline's StandardScaler was fit
            # on (FEATURES) — predict() on a raw array is functionally
            # fine but makes sklearn warn on every single call since the
            # scaler was fit with feature names.
            feature_row = pd.DataFrame(
                [[full_features.get(col, 0.0) for col in FEATURES]],
                columns=FEATURES,
            )
            predicted_oi_delta_next = self.ml_model.predict(feature_row)[0]
            self._last_efficiency = predicted_oi_delta_next
        except Exception:
            predicted_oi_delta_next = self._last_efficiency  # carry-forward on error

        estimated_change = int(round(predicted_oi_delta_next))
        return predicted_oi_delta_next, estimated_change

    def _compute_confidence(
        self,
        tick_index: int,
        drift_detected: bool,
        drift_alerts: list,
        tick_features: dict,
    ) -> float:
        """
        Confidence weight ∈ [DRIFT_CONFIDENCE_FLOOR, 1.0].

        Two independent discount factors — both apply multiplicatively:

        1. Time decay: linear decay from 1.0 → TICK_DECAY_FLOOR across the cycle.
           Later ticks within a 3-min window are less reliable (OI refresh imminent,
           volume thin, VWAP slope stale).

        2. Drift penalty: each breaching feature contributes a proportional penalty
           based on how far its Z-score exceeds the threshold. Features that are
           merely at the boundary (Z=3.0) get a small cut; severe outliers (Z>>3)
           approach the floor faster.
        """
        # ── Factor 1: time decay within cycle ────────────────────────────
        progress = tick_index / MAX_TICKS_PER_CYCLE          # 0.0 → 1.0
        time_weight = 1.0 - progress * (1.0 - TICK_DECAY_FLOOR)

        # ── Factor 2: drift penalty ───────────────────────────────────────
        drift_weight = 1.0
        if drift_detected and self.drift_monitor.baseline:
            for feature, metrics in self.drift_monitor.baseline.items():
                if feature in tick_features:
                    val  = tick_features[feature]
                    mean = metrics["mean"]
                    std  = metrics["std"] if metrics["std"] > 0 else 1.0
                    z    = abs(val - mean) / std

                    if z > DRIFT_Z_THRESHOLD:
                        # Penalty scales with excess Z beyond the threshold.
                        # At Z=3 → no extra penalty (just detected).
                        # At Z=6 → 50% penalty from this feature alone (capped).
                        excess          = (z - DRIFT_Z_THRESHOLD) / DRIFT_Z_THRESHOLD
                        feature_penalty = min(excess * 0.25, 0.50)   # cap per-feature
                        drift_weight   *= (1.0 - feature_penalty)

        # ── Combined ──────────────────────────────────────────────────────
        raw_confidence = time_weight * drift_weight
        return max(raw_confidence, DRIFT_CONFIDENCE_FLOOR)


# ─────────────────────────────────────────────
# Multi-symbol coordinator
# ─────────────────────────────────────────────

class VirtualOICoordinator:
    """
    Manages a pool of per-symbol, per-side (CE/PE) VirtualOIEstimator
    instances, routing each to the correctly trained pipeline.

    Example integration:

        coordinator = VirtualOICoordinator(
            models={"ce": ce_ml_model, "pe": pe_ml_model},
            drift_monitor=drift_monitor,
        )

        # On each poll cycle, per strike, per side:
        frame = coordinator.dispatch_tick(
            symbol="NIFTY2460623500",
            side="CE",
            tick_features={
                "ce_vol_delta": ..., "pe_vol_delta": ...,
                "ce_oi_delta_lag": ..., "pe_oi_delta_lag": ...,
                "ce_iv_delta": ..., "pe_iv_delta": ...,
            },
            confirmed_oi=last_known_ce_oi,
        )
        virtual_oi = frame.virtual_oi_running   # or .accumulator / .absolute

        # On NSE OI refresh (call from your existing refresh handler):
        coordinator.on_nse_refresh("NIFTY2460623500", "CE", new_confirmed_oi)
    """

    def __init__(self, models: dict, drift_monitor: "MLOpsDriftMonitor"):
        """
        models: {"ce": ce_pipeline, "pe": pe_pipeline} — at least one
        required, but a side with no trained model just returns None
        from dispatch_tick rather than silently predicting off the
        wrong side's pipeline (that was the original bug: only one
        "primary" model was ever held, so PE strikes were either
        estimated with the CE model or never reached at all).
        """
        self.models         = models
        self.drift_monitor  = drift_monitor
        self._estimators: dict[str, VirtualOIEstimator] = {}

    def dispatch_tick(
        self,
        symbol: str,
        side: str,
        tick_features: dict,
        confirmed_oi: float = 0.0,
    ) -> "VirtualOIFrame | None":
        side = side.upper()
        model = self.models.get(side.lower())
        if model is None:
            # No trained pipeline for this side yet — nothing to estimate.
            return None

        key = f"{symbol}::{side}"
        if key not in self._estimators:
            self._estimators[key] = VirtualOIEstimator(
                symbol=symbol,
                side=side,
                ml_model=model,
                drift_monitor=self.drift_monitor,
                initial_confirmed_oi=confirmed_oi,
            )
        return self._estimators[key].on_tick(tick_features)

    def on_nse_refresh(self, symbol: str, side: str, confirmed_oi: float):
        key = f"{symbol}::{side.upper()}"
        if key in self._estimators:
            self._estimators[key].on_nse_refresh(confirmed_oi)

    def on_nse_refresh_batch(self, refresh_map: dict):
        """Convenience: pass {(symbol, side): confirmed_oi} dict from your chain refresh."""
        for (symbol, side), oi in refresh_map.items():
            self.on_nse_refresh(symbol, side, oi)

    def get_all_frames_snapshot(self) -> dict[str, dict]:
        """
        Returns latest VirtualOIFrame values for all tracked symbols.
        Useful for emitting a single JSON payload to your dashboard WebSocket.
        """
        snapshot = {}
        for symbol, est in self._estimators.items():
            # Re-emit last frame values without a new tick (read-only)
            snapshot[symbol] = {
                "virtual_oi_accumulator": est._accumulator,
                "virtual_oi_running":     est.last_confirmed_oi + est._accumulator,
                "virtual_oi_absolute":    est._absolute_tracker,
                "last_confirmed_oi":      est.last_confirmed_oi,
                "tick_index":             est._tick_index,
                "seconds_since_refresh":  time.time() - est.last_refresh_ts,
            }
        return snapshot


# ─────────────────────────────────────────────
# MLOpsDriftMonitor (self-contained copy for this module)
# ─────────────────────────────────────────────

class MLOpsDriftMonitor:
    def __init__(self, model_registry_dir="model_registry"):
        # evaluate_and_deploy_pipeline() now saves separate baselines per
        # target (baseline_train_distributions_ce.pkl / _pe.pkl) rather
        # than one combined file. Prefer "ce" baseline if both exist;
        # this only matters once on_tick()/_infer() is reconciled with
        # the real feature schema (see load_virtual_oi_coordinator).
        ce_path = os.path.join(model_registry_dir, "baseline_train_distributions_ce.pkl")
        pe_path = os.path.join(model_registry_dir, "baseline_train_distributions_pe.pkl")
        legacy_path = os.path.join(model_registry_dir, "baseline_train_distributions.pkl")

        if os.path.exists(ce_path):
            self.baseline = joblib.load(ce_path)
        elif os.path.exists(pe_path):
            self.baseline = joblib.load(pe_path)
        elif os.path.exists(legacy_path):
            self.baseline = joblib.load(legacy_path)
        else:
            self.baseline = None

    def verify_feature_drift(self, live_window_features: dict) -> dict:
        if not self.baseline:
            return {"status": "HEALTHY", "drift_alerts": []}

        drift_alerts = []
        for feature, metrics in self.baseline.items():
            if feature in live_window_features:
                val  = live_window_features[feature]
                mean = metrics["mean"]
                std  = metrics["std"] if metrics["std"] > 0 else 1.0
                z    = abs(val - mean) / std

                if z > DRIFT_Z_THRESHOLD:
                    drift_alerts.append(
                        f"Feature Drift Alert: {feature} out-of-bounds (Z={z:.2f})"
                    )

        return {
            "status": "DRIFT_DETECTED" if drift_alerts else "HEALTHY",
            "drift_alerts": drift_alerts,
        }


# ─────────────────────────────────────────────
# Bootstrap helper — wire into your engine.py
# ─────────────────────────────────────────────

def load_virtual_oi_coordinator(model_registry_dir="model_registry") -> "VirtualOICoordinator | None":
    """
    Drop-in loader. Call once at startup in engine.py / option_chain.py.

    evaluate_and_deploy_pipeline() trains and saves two separate models —
    production_oi_pipeline_ce.pkl and production_oi_pipeline_pe.pkl —
    rather than a single combined model. This loader checks for both and
    is considered "ready" if at least one exists; the coordinator routes
    each side's dispatch_tick() calls to its own model (a side with no
    trained model yet just returns None from dispatch_tick instead of
    being estimated off the wrong pipeline).

    VirtualOIEstimator.on_tick()/_infer() consume the same per-strike
    snapshot-delta schema these models were trained on (ce_vol_delta,
    pe_vol_delta, ce_oi_delta_lag, pe_oi_delta_lag, ce_iv_delta,
    pe_iv_delta, minutes_since_last) — see FEATURES below.

    Returns a ready VirtualOICoordinator (holding whichever of the CE/PE
    pipelines were found) or None if neither model has been trained yet.
    """
    ce_path = os.path.join(model_registry_dir, "production_oi_pipeline_ce.pkl")
    pe_path = os.path.join(model_registry_dir, "production_oi_pipeline_pe.pkl")

    models = {}
    if os.path.exists(ce_path):
        models["ce"] = joblib.load(ce_path)
    if os.path.exists(pe_path):
        models["pe"] = joblib.load(pe_path)

    if not models:
        print("[VirtualOI] No production model found (looked for "
              f"'{ce_path}' and '{pe_path}'). Run evaluate_and_deploy_pipeline() first.")
        return None

    found = ", ".join(sorted(models.keys()))
    missing_side = ({"ce", "pe"} - models.keys())
    if missing_side:
        print(f"[VirtualOI] Loaded production model(s): {found}. "
              f"{missing_side.pop().upper()} side has no trained model yet — "
              f"that side's dispatch_tick() calls will return None until it's trained.")
    else:
        print(f"[VirtualOI] Loaded production model(s): {found}.")

    drift_monitor = MLOpsDriftMonitor(model_registry_dir)
    return VirtualOICoordinator(models, drift_monitor)
# ──────────────────────────────────────────────────────────────────────────────
#  Training function – uses oi_snapshots.json to fit a HuberRegressor
# ──────────────────────────────────────────────────────────────────────────────

import os
import glob
import numpy as np
import pandas as pd
import joblib
from datetime import datetime
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error

# Real feature/target schema, matching what build_training_warehouse.py
# actually writes to quant_warehouse/training_rows.parquet. CE and PE OI
# deltas are driven by different flow (call writers vs put writers), so
# we train two independent HuberRegressor pipelines rather than one
# combined model — consistent with how PCR/OI asymmetry is already
# treated elsewhere in decision_engine.py.
FEATURES = [
    "ce_vol_delta", "pe_vol_delta",
    "ce_oi_delta_lag", "pe_oi_delta_lag",
    "ce_iv_delta", "pe_iv_delta",
    "minutes_since_last",
]
TARGETS = {
    "ce": "ce_oi_delta_next",
    "pe": "pe_oi_delta_next",
}


LAG_COL_FOR_TARGET = {
    "ce_oi_delta_next": "ce_oi_delta_lag",
    "pe_oi_delta_next": "pe_oi_delta_lag",
}


def _directional_accuracy(y_true, y_pred):
    """
    Directional accuracy, restricted to rows where the actual delta is
    nonzero. A large share of strikes show zero OI change between
    snapshots (illiquid/no-flow rows); since a continuous regressor's
    output essentially never lands on exactly 0.0, including those rows
    forces an automatic miss regardless of model quality and drags the
    metric below chance. Comparing direction only where there's an
    actual move to call gives an honest read on the model.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    mask = y_true != 0
    if mask.sum() == 0:
        return np.nan, 0
    return np.mean(np.sign(y_pred[mask]) == np.sign(y_true[mask])), int(mask.sum())


def _train_one_target(df, target_col, model_registry_dir, label):
    """Fits, evaluates, and conditionally promotes a single-target pipeline."""
    sub = df.dropna(subset=FEATURES + [target_col]).sort_values("snapshot_time")

    if len(sub) < 20:
        print(f"[{label}] Only {len(sub)} usable rows — too few to train "
              f"a meaningful split. Skipping.")
        return

    # Time-aware split (no shuffling — avoids temporal leakage)
    split_idx = int(len(sub) * 0.80)
    X_train, X_test = sub[FEATURES].iloc[:split_idx], sub[FEATURES].iloc[split_idx:]
    y_train, y_test = sub[target_col].iloc[:split_idx], sub[target_col].iloc[split_idx:]

    if len(X_test) == 0:
        print(f"[{label}] Not enough rows after split to form a test set. Skipping.")
        return

    # Save training distributions for drift monitoring reference
    train_stats = X_train.describe().to_dict()
    joblib.dump(
        train_stats,
        os.path.join(model_registry_dir, f"baseline_train_distributions_{label}.pkl"),
    )

    candidate_pipeline = Pipeline([
        ('scaler', StandardScaler()),
        ('regressor', HuberRegressor(epsilon=1.35, max_iter=2000))
    ])
    candidate_pipeline.fit(X_train, y_train)

    preds = candidate_pipeline.predict(X_test)
    rmse = np.sqrt(mean_squared_error(y_test, preds))
    mae = mean_absolute_error(y_test, preds)
    prediction_bias = np.mean(preds - y_test)
    dir_acc, dir_n = _directional_accuracy(y_test, preds)

    # Naive baseline: predict next delta has the same sign as this
    # snapshot's own delta. Any deployed model should beat this, or it's
    # not adding value over a one-line heuristic.
    lag_col = LAG_COL_FOR_TARGET.get(target_col)
    naive_acc, naive_n = (np.nan, 0)
    if lag_col is not None:
        naive_acc, naive_n = _directional_accuracy(y_test, X_test[lag_col])

    print(f"--- Candidate Model Verification Report [{label.upper()}] ---")
    print(f"Rows: train={len(X_train)} test={len(X_test)} (nonzero-target test rows: {dir_n})")
    print(f"RMSE: {rmse:.5f} | MAE: {mae:.5f} | Bias: {prediction_bias:.5f}")
    print(f"Dir Accuracy (model, nonzero rows only): {dir_acc * 100:.2f}%")
    if naive_n:
        print(f"Dir Accuracy (naive — persist last delta's sign): {naive_acc * 100:.2f}% "
              f"({'model beats naive' if dir_acc > naive_acc else 'naive beats model — do not deploy'})")

    if naive_n and dir_acc <= naive_acc:
        print(f"[{label}] Candidate does not beat the naive persistence baseline on direction. "
              f"Not deploying.")
        return

    prod_model_path = os.path.join(model_registry_dir, f"production_oi_pipeline_{label}.pkl")
    if os.path.exists(prod_model_path):
        try:
            prod_pipeline = joblib.load(prod_model_path)
            prod_preds = prod_pipeline.predict(X_test)
            prod_rmse = np.sqrt(mean_squared_error(y_test, prod_preds))

            if rmse < prod_rmse:
                joblib.dump(candidate_pipeline, prod_model_path)
                print(f"[{label}] Candidate model improved RMSE ({rmse:.5f} < {prod_rmse:.5f}). Deployed.")
            else:
                print(f"[{label}] Candidate rejected ({rmse:.5f} >= {prod_rmse:.5f}). Production baseline retained.")
        except Exception:
            joblib.dump(candidate_pipeline, prod_model_path)
            print(f"[{label}] Could not evaluate existing production model — deployed candidate anyway.")
    else:
        joblib.dump(candidate_pipeline, prod_model_path)
        print(f"[{label}] No production model existed. Deployed candidate as initial baseline.")


def evaluate_and_deploy_pipeline(warehouse_dir="quant_warehouse", model_registry_dir="model_registry"):
    if not os.path.exists(model_registry_dir):
        os.makedirs(model_registry_dir)

    # 1. Ingest all warehouse partitions built by build_training_warehouse.py
    files = sorted(glob.glob(os.path.join(warehouse_dir, "*.parquet")))
    if not files:
        print(f"Mating Loop Interrupted: No features found in warehouse "
              f"('{warehouse_dir}'). Run build_training_warehouse.py first.")
        return

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])

    missing = [c for c in FEATURES + list(TARGETS.values()) if c not in df.columns]
    if missing:
        print(f"Mating Loop Interrupted: warehouse is missing expected columns {missing}. "
              f"Check build_training_warehouse.py output schema.")
        return

    print(f"[Warehouse] Loaded {len(df)} rows from {len(files)} file(s).")

    # 2. Train/evaluate/promote CE and PE targets independently
    for label, target_col in TARGETS.items():
        _train_one_target(df, target_col, model_registry_dir, label)