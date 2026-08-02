"""
ml/features.py
──────────────
Feature/target schema shared by ml.inference (VirtualOIEstimator._infer)
and ml.training (evaluate_and_deploy_pipeline). Split out of the former
virtual_oi_estimator.py so both sides import the same schema instead of
one module reaching into the other's internals.
"""

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
