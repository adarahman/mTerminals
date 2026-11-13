"""Shared weighted-evidence aggregation for equity and commodity decisions."""

from __future__ import annotations

from collections.abc import Mapping


DECISION_EVIDENCE_WEIGHTS = {
    "pcr": 0.25,
    "engine_bias": 0.25,
    "futures": 0.18,
    "max_pain": 0.05,
    "oi_velocity": 0.17,
    "capital_flow": 0.10,
}

DECISION_EVIDENCE_LABELS = {
    "pcr": "PCR positioning",
    "engine_bias": "Combined market bias",
    "futures": "Futures positioning",
    "max_pain": "Max-pain gravity",
    "oi_velocity": "OI velocity",
    "capital_flow": "Capital flow",
}


def aggregate_evidence(
    *,
    weights: Mapping[str, float],
    scores: Mapping[str, float],
    availability: Mapping[str, bool],
    labels: Mapping[str, str],
    conflict_weight_threshold: float = 0.20,
) -> dict:
    """Apply the shared weighted composite, coverage, and conflict rules."""
    contributors = []
    composite = 0.0
    available_weight = 0.0
    positive = 0
    negative = 0
    positive_weight = 0.0
    negative_weight = 0.0
    missing = []

    for key, weight in weights.items():
        available = bool(availability.get(key, False))
        score = float(scores.get(key, 0.0))
        if available:
            available_weight += weight
            composite += score * weight
            if score > 0.15:
                positive += 1
                positive_weight += weight
            elif score < -0.15:
                negative += 1
                negative_weight += weight
        else:
            missing.append(key)

        contributors.append({
            "key": key,
            "label": labels.get(key, key),
            "available": available,
            "weight": round(weight * 100),
            "score": round(score, 3) if available else None,
            "weightedContribution": round(score * weight, 3) if available else None,
        })

    return {
        "composite": composite,
        "conflict": (
            positive_weight >= conflict_weight_threshold
            and negative_weight >= conflict_weight_threshold
        ),
        "evidenceCoverage": round(available_weight * 100),
        "missingInputs": missing,
        "positiveContributors": positive,
        "negativeContributors": negative,
        "contributors": contributors,
    }
