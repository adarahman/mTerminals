"""Virtual OI enrichment for dashboard option-chain rows."""

from application.payload_builders.common import integer, rounded_number

try:
    from ml.inference import load_virtual_oi_coordinator

    DEFAULT_COORDINATOR = load_virtual_oi_coordinator("model_registry")
except ImportError:
    DEFAULT_COORDINATOR = None


def _set_confirmed_fallback(row):
    row["ceVirtualOI"] = row["ceOI"]
    row["peVirtualOI"] = row["peOI"]
    row["ceVoiConf"] = 0.0
    row["peVoiConf"] = 0.0
    row["ceVoiDrift"] = False
    row["peVoiDrift"] = False


def enrich_virtual_oi(
    chain_rows,
    symbol,
    engine_result=None,
    enabled=True,
    coordinator=DEFAULT_COORDINATOR,
):
    """Mutate chain rows with virtual OI fields and return the same rows."""
    history_snapshot = (
        getattr(engine_result, "oi_history_snapshot", None)
        if engine_result is not None
        else None
    )
    history_by_strike = {}
    if history_snapshot is not None and not history_snapshot.empty:
        for history_row in history_snapshot.to_dict("records"):
            history_by_strike[integer(history_row.get("StrikePrice", 0))] = history_row

    if not enabled or coordinator is None or not history_by_strike:
        for row in chain_rows:
            _set_confirmed_fallback(row)
        return chain_rows

    for row in chain_rows:
        strike = row["strike"]
        history_row = history_by_strike.get(strike)
        if history_row is None:
            _set_confirmed_fallback(row)
            continue

        features = {
            "ce_vol_delta": history_row.get("CE_Volume_Delta", 0) or 0,
            "pe_vol_delta": history_row.get("PE_Volume_Delta", 0) or 0,
            "ce_oi_delta_lag": history_row.get("CE_OI_Delta", 0) or 0,
            "pe_oi_delta_lag": history_row.get("PE_OI_Delta", 0) or 0,
            "ce_iv_delta": history_row.get("CE_IV_Delta", 0) or 0,
            "pe_iv_delta": history_row.get("PE_IV_Delta", 0) or 0,
        }
        for side, oi_key, destination_key in (
            ("CE", "ceOI", "ceVirtualOI"),
            ("PE", "peOI", "peVirtualOI"),
        ):
            symbol_key = f"{symbol}_{strike}"
            confirmed_oi = row[oi_key]
            try:
                frame = coordinator.dispatch_tick(
                    symbol=symbol_key,
                    side=side,
                    tick_features=features,
                    confirmed_oi=confirmed_oi,
                )
                if frame is None:
                    row[destination_key] = confirmed_oi
                    row[f"{side.lower()}VoiConf"] = 0.0
                    row[f"{side.lower()}VoiDrift"] = False
                    continue

                estimator = coordinator._estimators.get(f"{symbol_key}::{side}")
                if estimator is not None and confirmed_oi != estimator.last_confirmed_oi:
                    coordinator.on_nse_refresh(symbol_key, side, confirmed_oi)

                row[destination_key] = frame.virtual_oi_running
                row[f"{side.lower()}VoiConf"] = rounded_number(
                    frame.confidence_weight, 2
                )
                row[f"{side.lower()}VoiDrift"] = frame.drift_detected
            except Exception:
                row[destination_key] = confirmed_oi
                row[f"{side.lower()}VoiConf"] = 0.0
                row[f"{side.lower()}VoiDrift"] = False
    return chain_rows
