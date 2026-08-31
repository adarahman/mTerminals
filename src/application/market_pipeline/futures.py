import logging
from datetime import datetime
from collections.abc import Callable, Iterable

import pandas as pd

from brokers.market_data_registry import market_data
from brokers.smartapi.client import (
    safe_float,
    _load_scrip_master,
)

from market.expiry.instrument_expiries import (
    from_instrument_expiry as _from_smartapi_expiry,
    to_instrument_expiry as _to_smartapi_expiry,
)
from application.market_pipeline.utils import _canon_underlying

logger = logging.getLogger(__name__)


def _futures_candidates(
    rows: Iterable[dict],
    *,
    underlying: str,
    instrument_type_key: str,
    instrument_types: set[str] | frozenset[str],
    parse_expiry: Callable[[dict], datetime | None],
    exchange_key: str | None = None,
    exchange: str | None = None,
) -> list[tuple[dict, datetime]]:
    """Return matching futures instruments with valid expiries, oldest first."""
    candidates: list[tuple[dict, datetime]] = []
    underlying_u = underlying.upper()
    for row in rows:
        if exchange_key and row.get(exchange_key) != exchange:
            continue
        if (row.get("name") or "").upper() != underlying_u:
            continue
        if row.get(instrument_type_key) not in instrument_types:
            continue
        parsed_expiry = parse_expiry(row)
        if parsed_expiry is not None:
            candidates.append((row, parsed_expiry))
    candidates.sort(key=lambda pair: pair[1])
    return candidates

def fetch_futures_wide(
    underlying: str,
    expiry_dash: str | None = None,
    exchange: str = "NFO",
    which: str = "NEAR",
) -> pd.DataFrame:
    """Replacement for market_api.fetch_nifty_futures().

    IMPORTANT: expiry_dash means "this exact futures expiry date" — do
    NOT pass the options chain's EXPIRY here (previously done at both
    option_chain_json.py call sites). NIFTY/BANKNIFTY futures are listed
    monthly; options are often weekly. Passing a weekly options expiry
    as expiry_dash makes exact-date contract resolution empty on every
    week that isn't the monthly expiry week, silently
    returning an empty DataFrame the rest of the month. Leave expiry_dash
    None and use `which` (NEAR/NEXT/FAR) to pick a monthly slot by
    relative position instead — see option_chain_json.py's FUTURES_EXPIRY.

    Routing: the `which`-based path (the only one any real caller uses —
    grep-verified against option_chain_json.py/server/bridge.py) goes
    through MarketData.get_futures_quote(), the shared, provider-neutral
    FUT abstraction in brokers/market_data.py — see that module for the
    per-provider resolution/fallback rules (which providers have native
    FUTIDX resolution vs. fall back to the NSE/BSE public API, and how
    that fallback is flagged via the quote's "FutSource" field rather
    than being silent).

    The exact-date (expiry_dash) path only ever worked for SmartAPI/
    Upstox even before this refactor — Kotak/Shoonya/Kite/Breeze/NSE_BSE
    all silently ignored expiry_dash and used `which` instead. No current
    caller passes expiry_dash at all, so this is kept as a narrow legacy
    path (SmartAPI/Upstox only) rather than migrated into the shared
    abstraction, which doesn't take an exact-date parameter.
    """
    if expiry_dash:
        from brokers.market_data_registry import get_active_provider

        provider = get_active_provider()
        if provider == "SMARTAPI":
            return _fetch_futures_exact_date_smartapi(underlying, expiry_dash, exchange)
        if provider == "UPSTOX":
            return _fetch_futures_exact_date_upstox(underlying, expiry_dash, exchange)
        logger.warning(
            "[fetch_futures_wide] expiry_dash=%r requested but %s has no exact-date "
            "FUT resolution — falling back to which=%r instead of silently ignoring it",
            expiry_dash,
            provider,
            which,
        )

    quote = market_data.get_futures_quote(underlying, which=which)
    if not quote:
        return pd.DataFrame()
    return pd.DataFrame([quote])


def _fetch_futures_exact_date_upstox(
    underlying: str, expiry_dash: str, exchange: str = "NFO"
) -> pd.DataFrame:
    """Legacy exact-date FUT lookup for Upstox. No real caller today
    (see fetch_futures_wide's docstring) — kept only so a future exact-
    date caller doesn't silently regress to which-based resolution."""
    from brokers.upstox.client import _load_instrument_dump

    scope = "BSE" if exchange.upper() in ("BFO", "BSE") else "NSE"
    data = _load_instrument_dump(scope)
    underlying_u = underlying.upper()
    from brokers.upstox.client import _canonical_name as _up_canonical

    name_u = _up_canonical(underlying, data) or underlying_u

    def _parse_expiry(row):
        raw = row.get("expiry")
        if raw in (None, "", 0):
            return None
        if isinstance(raw, (int, float)):
            try:
                return datetime.utcfromtimestamp(raw / 1000)
            except (OverflowError, OSError, ValueError):
                return None
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d")
        except ValueError:
            try:
                return datetime.strptime(str(raw), "%d-%b-%Y")
            except ValueError:
                try:
                    return datetime.strptime(str(raw), "%d%b%Y")
                except ValueError:
                    return None

    cands = _futures_candidates(
        data,
        underlying=name_u,
        instrument_type_key="instrument_type",
        instrument_types={"FUT"},
        parse_expiry=_parse_expiry,
    )
    if not cands:
        return pd.DataFrame()
    try:
        target = datetime.strptime(expiry_dash, "%d-%b-%Y")
    except ValueError:
        try:
            target = datetime.strptime(expiry_dash, "%d%b%Y")
        except ValueError:
            try:
                target = datetime.strptime(expiry_dash, "%Y-%m-%d")
            except ValueError:
                return pd.DataFrame()
    matches = [row for row, exp in cands if exp.date() == target.date()]
    if not matches:
        return pd.DataFrame()
    fut = matches[0]

    quotes = market_data.get_batch_quotes(
        exchange,
        [(fut.get("trading_symbol"), fut.get("instrument_key"))],
        mode="FULL",
    )
    q = quotes.get(fut.get("trading_symbol")) if quotes else None
    if not q:
        return pd.DataFrame()

    spot_quote = market_data.get_spot_quote(underlying)
    spot = spot_quote["ltp"] if spot_quote else 0.0
    ltp = safe_float(q.get("last_price"))
    prev_close = safe_float(q.get("close"))
    change = q.get("net_change")
    pct = q.get("percent_change")
    if pct is None and prev_close:
        pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)

    exp_raw = fut.get("expiry")
    if isinstance(exp_raw, (int, float)):
        exp_str = datetime.utcfromtimestamp(exp_raw / 1000).strftime("%d-%b-%Y")
    else:
        exp_str = str(exp_raw)

    return pd.DataFrame(
        [
            {
                "Contract": fut.get("trading_symbol"),
                "Underlying": underlying,
                "Expiry": exp_str,
                "LTP": ltp,
                "Change": change,
                "PctChange": pct,
                "Open": q.get("open"),
                "High": q.get("high"),
                "Low": q.get("low"),
                "PrevClose": prev_close,
                "Volume": q.get("volume"),
                "Turnover": None,
                "OI": q.get("oi"),
                "Spot": spot,
                "Basis": round(ltp - spot, 2) if spot else None,
            }
        ]
    )


def _fetch_futures_exact_date_smartapi(
    underlying: str, expiry_dash: str, exchange: str = "NFO"
) -> pd.DataFrame:
    """Legacy exact-date FUT lookup for SmartAPI. No real caller today
    (see fetch_futures_wide's docstring) — kept only so a future exact-
    date caller doesn't silently regress to which-based resolution."""
    from brokers.smartapi.instruments import _FNO_FUT_TYPES

    def _parse_expiry(row):
        try:
            return datetime.strptime(row["expiry"], "%d%b%Y")
        except (KeyError, ValueError, TypeError):
            return None

    data = _load_scrip_master()
    name_u = _canon_underlying(underlying)
    cands = _futures_candidates(
        data,
        underlying=name_u,
        instrument_type_key="instrumenttype",
        instrument_types=_FNO_FUT_TYPES,
        parse_expiry=_parse_expiry,
        exchange_key="exch_seg",
        exchange=exchange,
    )
    if not cands:
        return pd.DataFrame()
    target = _to_smartapi_expiry(expiry_dash)
    matches = [row for row, _exp in cands if row["expiry"] == target]
    if not matches:
        return pd.DataFrame()
    fut = matches[0]

    quotes = market_data.get_batch_quotes(
        exchange, [(fut.get("symbol"), fut.get("token"))], mode="FULL"
    )
    q = quotes.get(fut.get("symbol")) if quotes else None
    if not q:
        return pd.DataFrame()

    spot_quote = market_data.get_spot_quote(underlying)
    spot = spot_quote["ltp"] if spot_quote else 0.0
    ltp = safe_float(q.get("ltp"))
    prev_close = safe_float(q.get("close"))
    change = safe_float(q.get("netChange"))
    pct = safe_float(q.get("percentChange"))
    if not pct and prev_close and ltp:
        pct = round(((ltp - prev_close) / prev_close) * 100.0, 2)

    return pd.DataFrame(
        [
            {
                "Contract": fut.get("symbol"),
                "Underlying": underlying,
                "Expiry": _from_smartapi_expiry(fut["expiry"]),
                "LTP": ltp,
                "Change": change,
                "PctChange": pct,
                "Open": safe_float(q.get("open")),
                "High": safe_float(q.get("high")),
                "Low": safe_float(q.get("low")),
                "PrevClose": prev_close,
                "Volume": safe_float(q.get("tradeVolume")),
                "Turnover": None,
                "OI": safe_float(q.get("opnInterest")),
                "Spot": spot,
                "Basis": round(ltp - spot, 2) if spot else None,
            }
        ]
    )


# ── VIX ──────────────────────────────────────────────────────────────────
# Gap #2 continued: not in smartapi_client.py's INDEX_TOKENS. Verified
# directly against the live scrip master (2026-07-14):
#   token=99926017, tradingsymbol="India VIX", exch_seg=NSE
