"""Normalize gathered provider inputs into one analytics market context."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from application.pipeline_config import RuntimeConfig
from market.option_chain.gatherer import GatheredMarketInputs
from market.option_chain.requests import MarketDataRequestPlan


def merge_index_volume_value(all_indices: list[dict], df_idx) -> None:
    """Enrich index quotes with session volume and value already in df_idx."""
    if df_idx is None or df_idx.empty or "Symbol" not in df_idx.columns:
        return
    volume_by_symbol = (
        df_idx.dropna(subset=["Volume"])
        .drop_duplicates(subset=["Symbol"], keep="first")
        .set_index("Symbol")[["Volume", "Value"]]
        .to_dict("index")
    )
    for entry in all_indices:
        row = volume_by_symbol.get(entry.get("Symbol"))
        if row:
            entry["Volume"] = row["Volume"]
            entry["Value"] = row["Value"]


def assemble_market_context(
    *,
    gathered: GatheredMarketInputs,
    request: MarketDataRequestPlan,
    runtime_config: RuntimeConfig,
    unified_public_market_data: Callable[[Any], tuple],
    select_spot: Callable[..., tuple],
) -> dict:
    """Convert provider-neutral gathered inputs to the engine input shape."""
    if request.option_exchange == "BSE":
        df, spot, expiry_dates = gathered.chain
        resolved = request.option_expiry
    else:
        df, spot, resolved, expiry_dates = gathered.chain

    df_fut = gathered.futures
    if isinstance(df_fut, dict):
        df_fut = pd.DataFrame([df_fut])
    elif df_fut is None:
        df_fut = pd.DataFrame()
    df_idx = gathered.indices

    if request.broker_enabled:
        live_vix, live_vix_change = gathered.vix
        sensex_quote = gathered.sensex_quote
        ticker_payload = gathered.ticker_payload
        bse_quotes = []
    else:
        live_vix, live_vix_change, ticker_payload = (
            unified_public_market_data(df_idx)
        )
        bse_quotes = [quote for quote in gathered.public_bse_quotes if quote]
        sensex_quote = next(
            (quote for quote in bse_quotes if quote.get("Symbol") == "SENSEX"),
            None,
        )

    all_indices = list(ticker_payload)
    if sensex_quote:
        all_indices.append(sensex_quote)
    if not request.broker_enabled:
        all_indices.extend(
            quote for quote in bse_quotes if quote.get("Symbol") != "SENSEX"
        )

    merge_index_volume_value(all_indices, df_idx)
    df, spot, price_source_used = select_spot(
        df, spot, df_fut, all_indices, runtime_config
    )

    return {
        "df": df,
        "spot": spot,
        "price_source_used": price_source_used,
        "resolved": resolved,
        "expiry_dates": expiry_dates,
        "df_fut": df_fut,
        "df_idx": df_idx,
        "india_vix": live_vix or 0.0,
        "india_vix_chg_pct": live_vix_change,
        "all_indices": all_indices,
    }
