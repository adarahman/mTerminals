"""Select the underlying price used by option-chain analytics."""
from __future__ import annotations

import logging
from datetime import datetime, time as dtime


logger = logging.getLogger(__name__)


def _live_index_quote(all_indices, symbol):
    for row in all_indices or []:
        if str(row.get("Symbol", "")).strip().upper() != symbol.upper():
            continue
        for key in ("Last Price", "ltp", "LTP", "last_price"):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return 0.0


def _futures_ltp(frame):
    if frame is None or getattr(frame, "empty", True):
        return 0.0
    row = frame.iloc[0]
    for key in ("LTP", "ltp", "Last Price", "last_price", "lastPrice"):
        try:
            value = float(row.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0.0


def select_runtime_spot(df, spot, df_fut, all_indices, runtime_config):
    """Choose EQ, live cash, or futures without leaking closed-market prices."""
    from nse_eod_fetch import is_trading_day

    source = runtime_config.price_source.strip().upper()
    eq = float(spot or 0.0)
    live_cash = (
        _live_index_quote(all_indices, runtime_config.symbol)
        if runtime_config.broker_enabled
        else 0.0
    )
    fut_ltp = _futures_ltp(df_fut)
    selected, used = eq, "EQ"

    if source == "FUT" and fut_ltp > 0:
        selected, used = fut_ltp, "FUT"
    elif source == "AUTO":
        live_differs = live_cash > 0 and (
            eq <= 0 or abs(live_cash - eq) / max(eq, 1.0) > 0.0005
        )
        if live_differs:
            selected, used = live_cash, "LIVE_EQ"
        elif (
            dtime(15, 15) <= datetime.now().time() <= dtime(15, 30)
            and is_trading_day(datetime.now())
            and fut_ltp > 0
        ):
            selected, used = fut_ltp, "FUT"

    if selected <= 0:
        raise RuntimeError(
            f"No usable spot price for {runtime_config.symbol}: "
            f"EQ={eq}, FUT={fut_ltp}"
        )

    if used != "EQ":
        df = df.copy()
        df["Spot"] = selected
        logger.warning(
            "[price-source] %s -> %s for %s (EQ=%s, live=%s, FUT=%s)",
            source,
            used,
            runtime_config.symbol,
            eq,
            live_cash or None,
            fut_ltp or None,
        )
    return df, selected, used
