"""Unauthenticated first-load OHLC bootstrap with a persistent local cache.

Yahoo's public chart feed is used only for cash/index candles. Futures are
deliberately rejected: continuous or guessed futures symbols can silently mix
expiries, which is worse than showing an empty chart.
"""

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from infrastructure.paths import CACHE_DIR

logger = logging.getLogger("mterminals.public_history")

_CACHE_DIR = Path(CACHE_DIR) / "market_history"
_SAFE = re.compile(r"[^A-Z0-9_.-]+")
_INDEX_TICKERS = {
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "NIFTYNXT50": "^NSMIDCP",
    "SENSEX": "^BSESN",
    "FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "MIDCPNIFTY": "NIFTY_MID_SELECT.NS",
}
_INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600, "1d": 86400}


def _ticker(symbol: str) -> str:
    symbol = (symbol or "").strip().upper()
    return _INDEX_TICKERS.get(symbol, f"{symbol}.NS")


def _cache_path(symbol, instrument, expiry, interval, days, cache_dir=None):
    identity = "_".join((symbol, instrument, expiry or "CASH", interval, str(days)))
    name = _SAFE.sub("_", identity.upper()) + ".json"
    return Path(cache_dir or _CACHE_DIR) / name


def _read_cache(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError, TypeError):
        return []


def _write_cache(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def fetch_public_history(
    symbol: str,
    interval: str,
    days: int,
    *,
    instrument: str = "EQ",
    expiry: str = "",
    cache_dir=None,
) -> list:
    """Return cached+incremental OHLCV rows, oldest first.

    Cache identity includes instrument and exact expiry. Only EQ/INDEX is
    supported by this public source; FUT must come from an expiry-specific
    authorized feed or locally accumulated ticks.
    """
    symbol = (symbol or "").strip().upper()
    instrument = (instrument or "EQ").strip().upper()
    expiry = (expiry or "").strip().upper()
    if not symbol or instrument not in {"EQ", "INDEX"}:
        return []
    if interval not in _INTERVAL_SECONDS:
        raise ValueError(f"Unsupported public-history interval: {interval}")

    path = _cache_path(symbol, instrument, expiry, interval, days, cache_dir)
    cached = _read_cache(path)
    now = int(time.time())
    requested_start = now - max(1, int(days)) * 86400
    step = _INTERVAL_SECONDS[interval]
    # Incremental reload: ask only for candles after the last cached bucket.
    period1 = max(requested_start, int(cached[-1]["t"] / 1000) + step) if cached else requested_start
    if period1 >= now:
        return cached

    ticker = _ticker(symbol)
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(ticker, safe='')}?period1={period1}&period2={now}&interval={interval}"
        "&includePrePost=false&events=div%2Csplits"
    )
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 mTerminals/1.0", "Accept": "application/json"},
        )
        response.raise_for_status()
        result = ((response.json().get("chart") or {}).get("result") or [None])[0]
        timestamps = (result or {}).get("timestamp") or []
        quote_rows = ((((result or {}).get("indicators") or {}).get("quote") or [{}])[0])
        opens, highs = quote_rows.get("open") or [], quote_rows.get("high") or []
        lows, closes = quote_rows.get("low") or [], quote_rows.get("close") or []
        volumes = quote_rows.get("volume") or []
        fresh = []
        for i, ts in enumerate(timestamps):
            values = [a[i] if i < len(a) else None for a in (opens, highs, lows, closes)]
            if ts is None or any(v is None for v in values):
                continue
            fresh.append({
                "t": int(ts) * 1000,
                "o": float(values[0]), "h": float(values[1]),
                "l": float(values[2]), "c": float(values[3]),
                "v": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
            })
        merged = {int(row["t"]): row for row in cached if row.get("t") is not None}
        merged.update({row["t"]: row for row in fresh})
        rows = [merged[key] for key in sorted(merged)]
        if rows:
            _write_cache(path, rows)
        return rows
    except Exception as exc:
        logger.warning("Public history fetch failed for %s: %s", symbol, exc)
        return cached
