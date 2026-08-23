"""
smartapi_history.py
====================
Historical OHLC candle data and historical Open Interest (OI) data via
SmartAPI — fills a gap market_api.py doesn't cover (it only pulls live
snapshots, no persisted historical series).

Uses the same session/token infrastructure as smartapi_client.py, so
login/re-login is handled automatically the same way.

Endpoints wrapped:
    getCandleData()  -> OHLCV bars for an instrument over a date range
    getOIData()      -> historical Open Interest series for a derivative

Both require a resolved (exchange, symboltoken) pair — reuse
find_option_token() / INDEX_TOKENS from smartapi_client.py for that,
exactly like get_atm_chain() already does.

Known constraint (Angel One docs): intraday intervals (ONE_MINUTE,
FIVE_MINUTE, etc.) are capped at roughly 30 days of data per single
request — request a longer range and the API will reject or truncate it.
fetch_candles_chunked() below handles this automatically by splitting
the range into <=30-day windows and stitching results together.
"""

import time
from datetime import datetime, timedelta

from logzero import logger

try:
    from .smartapi_client import _session, INDEX_TOKENS, find_option_token
except ImportError:
    from smartapi_client import _session, INDEX_TOKENS, find_option_token

# Valid interval strings per Angel One's historical API
INTERVALS = [
    "ONE_MINUTE", "THREE_MINUTE", "FIVE_MINUTE", "TEN_MINUTE",
    "FIFTEEN_MINUTE", "THIRTY_MINUTE", "ONE_HOUR", "ONE_DAY",
]

# Angel One's per-request lookback cap for intraday intervals (docs say
# ~30 days; we use 25 to leave headroom rather than hit the exact edge).
_MAX_INTRADAY_DAYS = 25
_MAX_DAILY_DAYS = 2000  # ONE_DAY interval allows much longer ranges


def _parse_candle_rows(raw_data):
    """SmartAPI returns candles as [timestamp, open, high, low, close, volume]
    arrays. Convert to a list of clean dicts."""
    rows = []
    for c in raw_data or []:
        rows.append({
            "timestamp": c[0],
            "open": c[1],
            "high": c[2],
            "low": c[3],
            "close": c[4],
            "volume": c[5],
        })
    return rows


def get_candle_data(exchange, symboltoken, interval, fromdate, todate):
    """
    Single raw call to SmartAPI's getCandleData.

    exchange:    'NSE' | 'NFO' | 'BSE' | 'BFO' | ...
    symboltoken: instrument token (string)
    interval:    one of INTERVALS
    fromdate/todate: 'YYYY-MM-DD HH:MM' strings (Angel One's required format)

    Returns list of {timestamp, open, high, low, close, volume} dicts,
    or [] on failure.
    """
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")

    params = {
        "exchange": exchange,
        "symboltoken": symboltoken,
        "interval": interval,
        "fromdate": fromdate,
        "todate": todate,
    }
    result = _session.call("getCandleData", params)

    if not result.get("status"):
        logger.warning(f"[smartapi_history] getCandleData failed: {result}")
        return []

    return _parse_candle_rows(result.get("data"))


def get_oi_data(exchange, symboltoken, interval, fromdate, todate):
    """
    Historical Open Interest series for a derivative instrument (option
    or future). Same param shape as get_candle_data but hits the OI
    endpoint and returns [{timestamp, oi}, ...] instead of OHLCV.
    """
    if interval not in INTERVALS:
        raise ValueError(f"interval must be one of {INTERVALS}")

    params = {
        "exchange": exchange,
        "symboltoken": symboltoken,
        "interval": interval,
        "fromdate": fromdate,
        "todate": todate,
    }
    result = _session.call("getOIData", params)

    if not result.get("status"):
        logger.warning(f"[smartapi_history] getOIData failed: {result}")
        return []

    rows = []
    for row in result.get("data", []):
        # OI endpoint's raw shape varies by SDK version; handle both
        # dict-style and [timestamp, oi] array-style responses.
        if isinstance(row, dict):
            rows.append({"timestamp": row.get("time") or row.get("timestamp"),
                          "oi": row.get("oi")})
        else:
            rows.append({"timestamp": row[0], "oi": row[1]})
    return rows


def _daterange_chunks(fromdate, todate, max_days):
    """Split a date range into <= max_days windows, since Angel One's
    intraday endpoints reject overly long single requests."""
    fmt = "%Y-%m-%d %H:%M"
    start = datetime.strptime(fromdate, fmt)
    end = datetime.strptime(todate, fmt)

    chunks = []
    cur = start
    while cur < end:
        chunk_end = min(cur + timedelta(days=max_days), end)
        chunks.append((cur.strftime(fmt), chunk_end.strftime(fmt)))
        cur = chunk_end
    return chunks


def fetch_candles_chunked(exchange, symboltoken, interval, fromdate, todate,
                           pause_between_calls=0.35):
    """
    Same as get_candle_data() but auto-splits long date ranges into
    Angel One's allowed window size, stitches results together, and
    sleeps briefly between calls to stay well under rate limits (their
    combined market-data quota is roughly 9-10 req/sec, but historical
    endpoints have their own separate — and generally tighter — limits,
    so we pace conservatively here rather than firing chunks back-to-back).
    """
    max_days = _MAX_DAILY_DAYS if interval == "ONE_DAY" else _MAX_INTRADAY_DAYS
    chunks = _daterange_chunks(fromdate, todate, max_days)

    all_rows = []
    for i, (start, end) in enumerate(chunks):
        rows = get_candle_data(exchange, symboltoken, interval, start, end)
        all_rows.extend(rows)
        if i < len(chunks) - 1:
            time.sleep(pause_between_calls)

    # de-dupe on timestamp in case chunk boundaries overlap
    seen = set()
    deduped = []
    for r in all_rows:
        if r["timestamp"] not in seen:
            seen.add(r["timestamp"])
            deduped.append(r)
    deduped.sort(key=lambda r: r["timestamp"])
    return deduped


# ── Convenience wrappers matching get_atm_chain()'s ergonomics ─────────────
def get_index_candles(underlying, interval, fromdate, todate):
    """e.g. get_index_candles('NIFTY', 'FIFTEEN_MINUTE', '2026-06-01 09:15', '2026-07-01 15:30')"""
    info = INDEX_TOKENS.get(underlying.upper())
    if not info:
        logger.warning(f"[smartapi_history] Unknown index: {underlying}")
        return []
    return fetch_candles_chunked(info["exchange"], info["token"], interval, fromdate, todate)


def get_option_candles(underlying, expiry_ddmmmyyyy, strike, opt_type, interval,
                        fromdate, todate, exchange="NFO"):
    """e.g. get_option_candles('NIFTY', '31JUL2026', 24200, 'CE', 'FIVE_MINUTE', ...)"""
    resolved = find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange)
    if not resolved:
        return []
    return fetch_candles_chunked(exchange, resolved["token"], interval, fromdate, todate)


def get_option_oi_history(underlying, expiry_ddmmmyyyy, strike, opt_type, interval,
                           fromdate, todate, exchange="NFO"):
    """Historical OI series for a single strike/leg — feeds directly into
    OI-velocity / buildup-signal classification (Long Buildup, Short
    Covering, etc.) with real historical continuity."""
    resolved = find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange)
    if not resolved:
        return []
    return get_oi_data(exchange, resolved["token"], interval, fromdate, todate)


# ── __main__ smoke-test ─────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        from .smartapi_client import list_expiries, get_atm_chain
    except ImportError:
        from smartapi_client import list_expiries, get_atm_chain

    print("=" * 60)
    print("smartapi_history.py — smoke test")
    print("=" * 60)

    # Last 3 days of 15-min NIFTY index candles
    todate = datetime.now().strftime("%Y-%m-%d %H:%M")
    fromdate = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M")

    print(f"\nFetching NIFTY 15-min candles from {fromdate} to {todate}...")
    candles = get_index_candles("NIFTY", "FIFTEEN_MINUTE", fromdate, todate)
    print(f"Got {len(candles)} candles")
    for c in candles[:3]:
        print(c)

    expiries = list_expiries("NIFTY", exchange="NFO")
    nearest = expiries[0]
    print(f"\nFetching OI history for nearest ATM-ish NIFTY {nearest} strike...")
    chain = get_atm_chain("NIFTY", nearest, strikes_around_atm=0, exchange="NFO")
    if chain and chain["rows"]:
        row = chain["rows"][0]
        oi_hist = get_option_oi_history(
            "NIFTY", nearest, row["strike"], row["type"],
            "FIFTEEN_MINUTE", fromdate, todate
        )
        print(f"Got {len(oi_hist)} OI data points for {row['strike']}{row['type']}")
        for o in oi_hist[:3]:
            print(o)
