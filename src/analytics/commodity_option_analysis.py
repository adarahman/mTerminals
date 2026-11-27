"""Compatibility import for the shared option-chain evidence analyzer."""

from __future__ import annotations

from analytics.option_chain_analysis import analyze_option_chain


def analyze_commodity_option_chain(
    chain: dict, *, previous_rows=None, elapsed_seconds=None, price_closes=None
) -> dict:
    """Backward-compatible wrapper; instrument type only selects the chain."""
    return analyze_option_chain(
        chain,
        previous_rows=previous_rows,
        elapsed_seconds=elapsed_seconds,
        price_closes=price_closes,
        required_evidence={"pcr", "futures"},
        evidence_labels={"futures": "Futures day direction"},
    )
