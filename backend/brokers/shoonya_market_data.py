"""Shoonya (Finvasia Noren) market-data adapter — implements
brokers.market_data's MarketData Protocol as another provider alongside
SmartApiMarketData / UpstoxMarketData / BreezeMarketData.

Expiry format note: like breeze_market_data.py, this module works in
"%d-%b-%Y" (e.g. "28-Aug-2025") rather than SmartAPI's dash-less
DDMMMYYYY convention, because that's the format brokers/shoonya_client.py's
resolve_option_contract()/place_order() already expect (see that module's
docstring). This is safe: nothing in ws_server_live.py or
option_chain_json.py parses an expiry string returned by list_expiries()
directly — it's only ever displayed and round-tripped back into
get_atm_chain()/find_option_token(), so as long as this adapter is
self-consistent (which it is), the exact string format doesn't matter to
any existing caller. find_option_token() below delegates straight to
shoonya_client.resolve_option_contract() so there's exactly one
implementation of contract resolution, not two.

Trading-symbol convention this module relies on (confirmed against
Shoonya/Finvasia's own API docs and the official ShoonyaApi-py/-js/-dotnet
READMEs, not guessed): for exchange NFO/BFO,
    tradingsymbol = SYMBOL + DDMMMYY + ('C'|'P') + STRIKE   (options)
    tradingsymbol = SYMBOL + DDMMMYY + 'F'                  (futures)
e.g. "BANKNIFTY28AUG25C52000". DDMMMYY is upper-cased (e.g. "28AUG25").
_expiry_from_tsym()/_strike_from_tsym() below parse against this fixed
convention rather than any broker-computed field.

Three real gaps versus SmartApiMarketData, called out here rather than
papered over (same "honest approximation" posture as breeze_market_data.py
and option_chain_json's Unusual Volume Activity card):

1. No batch quoting. Noren's documented market-data surface (searchscrip /
   get_security_info / get_quotes / get_time_price_series /
   get_daily_price_series / get_option_chain) has no multi-token quote
   call — get_quotes() is one contract per request. get_batch_quotes()
   below loops one call per pair, same as BreezeMarketData's — keep
   `symbol_token_pairs` short. This is why get_atm_chain() below uses
   get_option_chain() to resolve the strike ladder (one call per option
   side covers the whole chain) and only loops per-contract for the
   quote fetch itself.

2. No expiry-list endpoint, and this module deliberately does NOT parse
   Shoonya's own downloadable instrument-master dump
   (https://api.shoonya.com/NFO_symbols.txt.zip — same file
   shoonya_client.py could use for a real ScripMaster-style cache).
   That file's exact column layout for OPTIDX/OPTSTK rows (which columns
   carry expiry / strike / option-type) was not confirmed against a live
   download in this environment, and guessing a CSV schema wrong for a
   live trading feed is worse than being explicit about the gap — see
   this same call in breeze_market_data.py's module docstring. Instead,
   list_expiries() enumerates live contracts via
   searchscrip(exchange, underlying) and extracts the embedded expiry
   from each returned tsym using the confirmed convention above.
   searchscrip is a text-search endpoint with an undocumented result
   cap, so an underlying with an unusually large number of live expiries
   could have some silently cut off. Swap this for a real master-file
   parser once the NFO CSV schema is confirmed against a live account —
   flagged here as a follow-up, not done speculatively.

3. No F&O stock universe endpoint short of that same unparsed master
   file. get_fno_underlyings() returns the known index list only, same
   as BreezeMarketData; `stocks` is deliberately left empty rather than
   guessed.

Field-name caveat: get_quotes()'s response field abbreviations used below
(lp/o/h/l/c/v/oi/...) follow the Noren API convention shared across every
broker built on this same OMS (Shoonya, and others using the identical
"NorenApi" base) — confirmed against Shoonya's public API documentation,
not independently verified against a live call in this environment.
_raw_quote() isolates all of that field-name knowledge in one place so a
mismatch is a one-function fix, not a scattered one.
"""
from __future__ import annotations

import logging
import re
import threading
import json
import os
from datetime import datetime

try:  # ws_server_live adds backend/ to sys.path; package-level tests do not.
    from config import settings  # noqa: F401  (imported for parity with sibling adapters; unused directly)
    from paths import RUNTIME_DIR
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from backend.config import settings  # noqa: F401
    from backend.paths import RUNTIME_DIR

from brokers.shoonya_client import _session, resolve_option_contract as _resolve_contract
from brokers.shoonya_client import BrokerError

logger = logging.getLogger(__name__)

# Same physical strike spacing SmartAPI's STRIKE_INTERVALS uses — kept as
# an independent copy rather than importing brokers.smartapi_client (that
# module imports the SmartApi SDK at top level, which would make a
# Shoonya-only deployment depend on smartapi-python being installed just
# to read a constants dict). Same category of duplication already tracked
# as a dedup TODO elsewhere in this codebase (two LOT_SIZES dicts, two
# Black-Scholes implementations) — flagged here rather than silently
# repeated a third time.
_STRIKE_INTERVALS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
}

# tsym suffix grammar: DDMMMYY (7 chars) followed by 'C'/'P'/'F'.
_EXPIRY_PREFIX_RE = re.compile(r"^(\d{2}[A-Z]{3}\d{2})([CPF])")

# Underlyings whose Shoonya index tsym is NOT simply the plain name —
# extend this map as mismatches are confirmed against a live account,
# same pattern breeze_market_data.py's _INDEX_STOCK_CODES uses for
# Breeze's own stock_code quirks. Left empty (falls through to
# resolve_index_token()'s searchscrip lookup) rather than guessed.
_INDEX_TSYM_OVERRIDES: dict = {}

_INDEX_CACHE_PATH = os.path.join(RUNTIME_DIR, "shoonya_cache", "index_tokens.json")
_index_cache_lock = threading.Lock()
_index_cache: dict | None = None


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _load_index_cache() -> dict:
    global _index_cache
    with _index_cache_lock:
        if _index_cache is not None:
            return _index_cache
        try:
            with open(_INDEX_CACHE_PATH) as f:
                _index_cache = json.load(f)
        except (OSError, ValueError):
            _index_cache = {}
        return _index_cache


def _save_index_cache():
    os.makedirs(os.path.dirname(_INDEX_CACHE_PATH), exist_ok=True)
    with _index_cache_lock:
        with open(_INDEX_CACHE_PATH, "w") as f:
            json.dump(_index_cache, f)


def resolve_index_token(underlying: str, exchange: str = "NSE") -> dict | None:
    """{'token', 'tsym'} for an index underlying, disk-cached. Best-effort:
    searchscrip has no documented "give me the index row" filter, so this
    prefers an EXACT tsym match (case-insensitive) against `underlying` or
    a known override, and falls back to the shortest returned tsym that
    starts with `underlying` and carries a purely numeric token. Logs a
    warning (not an error) on an ambiguous fallback so a wrong pick is
    visible in logs rather than silently wrong."""
    underlying = underlying.upper()
    override = _INDEX_TSYM_OVERRIDES.get(underlying)
    search_text = override or underlying

    cache = _load_index_cache()
    cache_key = f"{exchange}:{underlying}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        result = _session.api.searchscrip(exchange=exchange, searchtext=search_text)
    except Exception as exc:
        logger.warning("[shoonya_market_data] searchscrip(%s, %s) failed: %s", exchange, search_text, exc)
        return None
    values = (result or {}).get("values") or []
    if not values:
        logger.warning("[shoonya_market_data] no searchscrip results for %s:%s", exchange, underlying)
        return None

    exact = [row for row in values if str(row.get("tsym", "")).upper() == search_text.upper()]
    candidates = exact or sorted(
        (row for row in values if str(row.get("tsym", "")).upper().startswith(underlying)
         and str(row.get("token", "")).isdigit()),
        key=lambda row: len(str(row.get("tsym", ""))),
    )
    if not candidates:
        logger.warning("[shoonya_market_data] could not resolve index token for %s:%s", exchange, underlying)
        return None
    if not exact:
        logger.warning(
            "[shoonya_market_data] no exact tsym match for %s:%s, falling back to closest candidate %r",
            exchange, underlying, candidates[0].get("tsym"),
        )
    row = candidates[0]
    entry = {"token": str(row.get("token")), "tsym": row.get("tsym")}
    cache[cache_key] = entry
    _save_index_cache()
    return entry


def _round_to_strike(price, underlying):
    interval = _STRIKE_INTERVALS.get(underlying.upper(), 50)
    return int(round(price / interval) * interval)


def _raw_quote(exchange: str, token: str) -> dict | None:
    """One get_quotes() call, unwrapped. Returns Shoonya's raw row (not
    field-mapped) — see get_batch_quotes()'s docstring for why raw shape
    is preserved at that boundary. Callers that want normalized
    ltp/open/high/low/close should use _mapped_quote() instead."""
    try:
        result = _session.api.get_quotes(exchange=exchange, token=str(token))
    except Exception as exc:
        logger.warning("[shoonya_market_data] get_quotes(%s, %s) failed: %s", exchange, token, exc)
        return None
    if not isinstance(result, dict) or result.get("stat") == "Not_Ok":
        return None
    return result


def _mapped_quote(exchange: str, token: str) -> dict | None:
    """Same call as _raw_quote(), normalized to this codebase's common
    {'ltp','open','high','low','close','oi','volume','net_change',
    'pct_change'} row shape — see module docstring's field-name caveat
    for the Noren abbreviations this assumes (lp/o/h/l/c/v/oi/nc/pc)."""
    row = _raw_quote(exchange, token)
    if not row:
        return None
    return {
        "ltp": safe_float(row.get("lp")),
        "open": safe_float(row.get("o")),
        "high": safe_float(row.get("h")),
        "low": safe_float(row.get("l")),
        "close": safe_float(row.get("c")),
        "oi": safe_float(row.get("oi")),
        "volume": safe_float(row.get("v")),
        "net_change": safe_float(row.get("nc")),
        "pct_change": safe_float(row.get("pc")),
        # Noren reports OI in quantity (shares), not lots — `ls` (lot size)
        # is carried in the same quote response so the pipeline can convert
        # to the lots/contracts convention _chg_oi() and build_master_table_nse()
        # expect (same fix as Upstox/Kite in fetch_option_chain_wide()).
        "lot_size": int(safe_float(row.get("ls"))) or None,
    }


def list_expiries(underlying: str, exchange: str = "NFO") -> list:
    """See module docstring caveat 2 — enumerated via searchscrip + tsym
    parsing, not a real broker expiry-list endpoint or the instrument
    master. Returns 'DD-Mon-YYYY' strings, nearest first."""
    underlying = underlying.upper()
    try:
        result = _session.api.searchscrip(exchange=exchange, searchtext=underlying)
    except Exception as exc:
        logger.warning("[shoonya_market_data] searchscrip(%s, %s) failed: %s", exchange, underlying, exc)
        return []
    values = (result or {}).get("values") or []

    dates = set()
    for row in values:
        tsym = str(row.get("tsym") or "").upper()
        if not tsym.startswith(underlying):
            continue
        m = _EXPIRY_PREFIX_RE.match(tsym[len(underlying):])
        if not m:
            continue
        try:
            dates.add(datetime.strptime(m.group(1), "%d%b%y").date())
        except ValueError:
            continue

    return [d.strftime("%d-%b-%Y") for d in sorted(dates)]


def _strike_from_tsym(tsym: str, underlying: str, expiry_compact: str) -> int | None:
    prefix = f"{underlying.upper()}{expiry_compact}"
    tsym_u = str(tsym or "").upper()
    if not tsym_u.startswith(prefix):
        return None
    rest = tsym_u[len(prefix):]
    if not rest or rest[0] not in "CP":
        return None
    try:
        return int(round(float(rest[1:])))
    except ValueError:
        return None


def find_option_token(underlying, expiry, strike, opt_type, exchange="NFO"):
    """Delegates to shoonya_client.resolve_option_contract() — see module
    docstring: contract resolution has exactly one implementation."""
    contract = _resolve_contract(underlying, expiry, strike, opt_type, exchange=exchange)
    if not contract:
        return None
    _, tradingsymbol, token = contract
    return {"tradingsymbol": tradingsymbol, "token": token}


def get_atm_chain(underlying, expiry, strikes_around_atm=10, exchange="NFO"):
    """expiry must be a 'DD-Mon-YYYY' string, e.g. one returned by
    list_expiries() above (or matching shoonya_client's own convention)."""
    underlying = underlying.upper()
    quote = get_spot_quote(underlying)
    if not quote or not quote.get("ltp"):
        logger.warning("[shoonya_market_data] could not fetch spot for %s", underlying)
        return None
    spot = quote["ltp"]
    atm = _round_to_strike(spot, underlying)
    interval = _STRIKE_INTERVALS.get(underlying, 50)

    try:
        expiry_dt = datetime.strptime(expiry, "%d-%b-%Y")
    except (TypeError, ValueError):
        logger.warning("[shoonya_market_data] bad expiry %r (expected 'DD-Mon-YYYY')", expiry)
        return None
    expiry_compact = expiry_dt.strftime("%d%b%y").upper()

    strike_set = {atm + i * interval for i in range(-strikes_around_atm, strikes_around_atm + 1)}

    contracts = {}  # (strike, "CE"|"PE") -> {"tradingsymbol", "token"}
    for opt_type, marker in (("CE", "C"), ("PE", "P")):
        search_tsym = f"{underlying}{expiry_compact}{marker}{atm}"
        try:
            result = _session.api.get_option_chain(exchange, search_tsym, str(atm), strikes_around_atm)
        except Exception as exc:
            logger.warning(
                "[shoonya_market_data] get_option_chain(%s, %s) failed: %s", exchange, search_tsym, exc
            )
            continue
        if not isinstance(result, dict) or result.get("stat") == "Not_Ok":
            continue
        for row in result.get("values") or []:
            tsym, token = row.get("tsym"), row.get("token")
            if not tsym or not token:
                continue
            strike_val = _strike_from_tsym(tsym, underlying, expiry_compact)
            if strike_val is None or strike_val not in strike_set:
                continue
            contracts[(strike_val, opt_type)] = {"tradingsymbol": tsym, "token": str(token)}

    if not contracts:
        logger.warning(
            "[shoonya_market_data] no contracts resolved for %s %s around ATM %s",
            underlying, expiry, atm,
        )
        return None

    rows = []
    for (strike_val, opt_type), info in contracts.items():
        q = _mapped_quote(exchange, info["token"])
        if not q:
            continue
        rows.append({
            "strike": strike_val,
            "type": opt_type,
            "tradingsymbol": info["tradingsymbol"],
            "token": info["token"],
            **q,
        })
    if not rows:
        return None
    rows.sort(key=lambda r: (r["strike"], r["type"]))

    return {
        "underlying": underlying,
        "spot": spot,
        "atm_strike": atm,
        "expiry": expiry,
        "rows": rows,
    }


def get_batch_quotes(exchange, symbol_token_pairs, mode="FULL"):
    """One get_quotes() REST call per pair — see module docstring caveat
    1. Returns Shoonya's RAW per-contract row (not normalized), same
    boundary contract as SmartApiMarketData's get_batch_quotes() and
    BreezeMarketData's equivalent — callers that need normalized fields
    should map them at the call site, not rely on this function's shape."""
    del mode
    out = {}
    for tradingsymbol, token in symbol_token_pairs:
        if not token:
            continue
        row = _raw_quote(exchange, token)
        if row:
            out[tradingsymbol] = row
    return out


def get_batch_quotes_by_token(exchange, symbol_token_pairs, mode="FULL"):
    """Same request as get_batch_quotes(), keyed by token instead of
    tradingsymbol — matches SmartApiMarketData's distinction."""
    by_symbol = get_batch_quotes(exchange, symbol_token_pairs, mode=mode)
    return {token: by_symbol[symbol] for symbol, token in symbol_token_pairs if symbol in by_symbol}


def get_spot_quote(underlying: str):
    exchange = "BSE" if underlying.upper() == "SENSEX" else "NSE"
    index = resolve_index_token(underlying, exchange)
    if not index:
        return None
    return _mapped_quote(exchange, index["token"])


def get_fno_underlyings(force_refresh=False):
    """Index list only — see module docstring caveat 3. `stocks` is
    deliberately empty rather than guessed; wire in a parsed NFO master
    dump here once its column layout is confirmed."""
    del force_refresh
    return {"indices": ["BANKEX", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX"], "stocks": []}


def index_tokens():
    """Shaped like SmartApiMarketData.index_tokens()."""
    out = {}
    for symbol, exch in (
        ("NIFTY", "NSE"), ("BANKNIFTY", "NSE"), ("FINNIFTY", "NSE"),
        ("MIDCPNIFTY", "NSE"), ("SENSEX", "BSE"),
    ):
        index = resolve_index_token(symbol, exch)
        out[symbol] = {"token": index["token"] if index else None, "exchange": exch}
    return out


class ShoonyaMarketData:
    """Adapter satisfying brokers.market_data.MarketData — see that
    module's Protocol for the interface contract, and this module's
    docstring for the three documented gaps versus SmartApiMarketData."""

    def list_expiries(self, underlying, exchange="NFO"):
        return list_expiries(underlying, exchange=exchange)

    def get_atm_chain(self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
        return get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm, exchange=exchange)

    def find_option_token(self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
        return find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange=exchange)

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        return get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        return get_batch_quotes_by_token(exchange, symbol_token_pairs, mode=mode)

    def get_spot_quote(self, underlying):
        return get_spot_quote(underlying)

    def get_fno_underlyings(self, force_refresh=False):
        return get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        return index_tokens()
