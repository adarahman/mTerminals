"""Decision payload evaluation and replay-history recording."""

import logging

from decision.decision_engine import DecisionEngine

logger = logging.getLogger(__name__)


def build_decision(engine_result, ctx_dict, last_updated, symbol, expiry):
    state_version = f"{symbol}:{expiry}:{last_updated}"
    try:
        decision_context = (
            engine_result.to_ctx_dict()
            if hasattr(engine_result, "to_ctx_dict")
            else ctx_dict
        )
        decision_context = dict(decision_context or {})
        decision_context["_decision_timestamp"] = last_updated
        decision_context["_state_version"] = state_version
        decision = DecisionEngine().evaluate(engine_result, decision_context).to_dict()

        try:
            from backtest.snapshot_logger import log_decision_snapshot

            log_decision_snapshot(engine_result, decision)
        except Exception as snapshot_error:
            logger.warning(
                "[build_decision] decision snapshot logging failed (%s)",
                snapshot_error,
            )
        return decision
    except Exception as decision_error:
        logger.warning(
            "[build_decision] DecisionEngine failed (%s) — degraded decision returned",
            decision_error,
        )
        return {
            "decisionTimestamp": last_updated,
            "stateVersion": state_version,
            "stale": False,
            "degraded": True,
            "evidenceCoverage": 0,
            "missingInputs": ["decision_engine"],
            "contributors": [],
            "bias": "NEUTRAL",
            "biasStrength": "WEAK",
            "confidence": 0,
            "conflictFlag": False,
            "action": "Decision engine error",
            "actionType": "WAIT",
            "suggestedStrike": None,
            "suggestedStrategy": "",
            "executeRecommended": False,
            "strategyCaution": "Decision engine unavailable",
            "activeSignals": [{"text": str(decision_error), "severity": "warn"}],
            "verdicts": {},
            "oiAnnotations": {},
            "autoStrategy": {},
            "tradeGrade": "",
            "riskWarning": "Decision engine unavailable",
            "importantLevels": {},
            "_debug": {"error": str(decision_error)},
        }
