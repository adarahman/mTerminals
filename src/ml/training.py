"""
ml/training.py
──────────────
Offline training/MLOps half of the former virtual_oi_estimator.py.
Trains CE and PE HuberRegressor pipelines from the warehouse
build_training_warehouse.py produces, evaluates them, and promotes
candidates to production_oi_pipeline_{ce,pe}.pkl for ml.inference to load.
Run standalone (not imported by the live serving path).
"""

import os
import glob
import numpy as np
import pandas as pd
import joblib
import logging
from datetime import datetime
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import HuberRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error

from ml.features import FEATURES, TARGETS

logger = logging.getLogger(__name__)


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
        logger.warning(f"[{label}] Only {len(sub)} usable rows — too few to train "
              f"a meaningful split. Skipping.")
        return

    # Time-aware split (no shuffling — avoids temporal leakage)
    split_idx = int(len(sub) * 0.80)
    X_train, X_test = sub[FEATURES].iloc[:split_idx], sub[FEATURES].iloc[split_idx:]
    y_train, y_test = sub[target_col].iloc[:split_idx], sub[target_col].iloc[split_idx:]

    if len(X_test) == 0:
        logger.warning(f"[{label}] Not enough rows after split to form a test set. Skipping.")
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

    logger.info(f"--- Candidate Model Verification Report [{label.upper()}] ---")
    logger.info(f"Rows: train={len(X_train)} test={len(X_test)} (nonzero-target test rows: {dir_n})")
    logger.info(f"RMSE: {rmse:.5f} | MAE: {mae:.5f} | Bias: {prediction_bias:.5f}")
    logger.info(f"Dir Accuracy (model, nonzero rows only): {dir_acc * 100:.2f}%")
    if naive_n:
        logger.info(f"Dir Accuracy (naive — persist last delta's sign): {naive_acc * 100:.2f}% "
              f"({'model beats naive' if dir_acc > naive_acc else 'naive beats model — do not deploy'})")

    if naive_n and dir_acc <= naive_acc:
        logger.info(f"[{label}] Candidate does not beat the naive persistence baseline on direction. "
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
                logger.info(f"[{label}] Candidate model improved RMSE ({rmse:.5f} < {prod_rmse:.5f}). Deployed.")
            else:
                logger.error(f"[{label}] Candidate rejected ({rmse:.5f} >= {prod_rmse:.5f}). Production baseline retained.")
        except Exception:
            joblib.dump(candidate_pipeline, prod_model_path)
            logger.info(f"[{label}] Could not evaluate existing production model — deployed candidate anyway.")
    else:
        joblib.dump(candidate_pipeline, prod_model_path)
        logger.info(f"[{label}] No production model existed. Deployed candidate as initial baseline.")


def evaluate_and_deploy_pipeline(warehouse_dir="quant_warehouse", model_registry_dir="model_registry"):
    if not os.path.exists(model_registry_dir):
        os.makedirs(model_registry_dir)

    # 1. Ingest all warehouse partitions built by build_training_warehouse.py
    files = sorted(glob.glob(os.path.join(warehouse_dir, "*.parquet")))
    if not files:
        logger.info(f"Mating Loop Interrupted: No features found in warehouse "
              f"('{warehouse_dir}'). Run build_training_warehouse.py first.")
        return

    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["snapshot_time"] = pd.to_datetime(df["snapshot_time"])

    missing = [c for c in FEATURES + list(TARGETS.values()) if c not in df.columns]
    if missing:
        logger.info(f"Mating Loop Interrupted: warehouse is missing expected columns {missing}. "
              f"Check build_training_warehouse.py output schema.")
        return

    logger.info(f"[Warehouse] Loaded {len(df)} rows from {len(files)} file(s).")

    # 2. Train/evaluate/promote CE and PE targets independently
    for label, target_col in TARGETS.items():
        _train_one_target(df, target_col, model_registry_dir, label)