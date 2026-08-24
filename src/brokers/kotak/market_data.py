"""Kotak Neo market-data adapter — implements brokers.market_data's
MarketData Protocol as a runtime-selectable provider alongside SmartAPI/
Upstox/Shoonya/Kite/Breeze/NSE_BSE.

Two real gaps versus the SmartAPI implementation, called out here
rather than papered over (same "honest approximation" posture as
option_chain_json's Unusual Volume Activity card):

1. No live option-chain endpoint. Kotak's NEO API has no "whole expiry
   chain" REST call (Kotak's own support page: "Option Chain API is
   unavailable at the moment" — see the kotakneo.com FAQ). The adapter
   therefore builds the ATM window the same way Kite does: resolve the
   contract set from the NFO scrip master (search_scrip / scrip_master
   CSV), then pull live ltp/oi/volume per token with quotes(). This is
   a real architectural difference, not a data gap — get_atm_chain()
   below returns rows WITH live quotes (like Breeze/Upstox), so the
   pipeline's fetch_option_chain_wide() treats it like the other
   quote-carrying providers rather than the Kite metadata-only path.

2. Two-step TOTP+MPIN login, no long-lived token. See
   brokers.kotak.client's module docstring for the full reasoning.
   The session auto-logs-in from KOTAK_TOTP_SECRET + KOTAK_MPIN on
   first use, exactly like shoonya_client.py does for Shoonya.

Scrip-master caching: the NFO scrip master CSV (a few thousand rows for
the index options + all F&O stocks) is the resolution source for both
list_expiries() and find_option_token(). It is re-downloaded at most
every 6 hours (Kotak's file-paths endpoint is the authoritative listing,
and contracts roll over at the start of each expiry cycle) and cached
under paths.RUNTIME_DIR the same way brokers/breeze_market_data.py caches
its stock-code map — so it survives restarts without living inside the
package.

Strike-price encoding: the CSV stores dStrikePrice in PAISE (1755000
for strike 17550.00), matching the SDK's own search_scrip filter math
(dStrikePrice * 100). The adapter normalizes to the pipeline's rupee
convention.
"""
from __future__ import annotations

import csv
import logging
import os
import time
from datetime import datetime, timedelta
from io import StringIO

try:
    from infrastructure.config import settings
    from infrastructure.paths import RUNTIME_DIR
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from infrastructure.config import settings
    from infrastructure.paths import RUNTIME_DIR

from brokers.kotak.client import _session

logger = logging.getLogger(__name__)

# Same physical strike spacing SmartAPI's STRIKE_INTERVALS uses — kept as
# an independent copy rather than importing brokers.smartapi.client (that
# module imports the SmartApi SDK at module top level, which would make a
# Kotak-only deployment depend on smartapi-python being installed just to
# read a constants dict). Same category of duplication as the two LOT_SIZES
# dicts already tracked as a dedup TODO elsewhere in this codebase.
_STRIKE_INTERVALS = {
    "NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50,
    "MIDCPNIFTY": 25, "SENSEX": 100, "BANKEX": 100,
}

# Index names Kotak's quotes API expects for spot/index tokens (the docs
# list these literal display names — NSE indices do NOT take a numeric
# instrument token). Keyed by the codebase's underlying symbol.

_INDEX_NAMES = {
    "NIFTY": "Nifty 50",
    "BANKNIFTY": "Nifty Bank",
    "FINNIFTY": "Nifty Fin Service",
    "MIDCPNIFTY": "Nifty Midcap Select",
    "INDIA VIX": "India VIX",
}

_BSE_INDEX_NAMES = {
    "SENSEX": "SENSEX",
    "BANKEX": "BANKEX",
}
# Indices with exchange-segment mapping; everything else is NFO stock
# derivatives.
_INDEX_EXCHANGE = {
    "NSE": "nse_cm",
    "BSE": "bse_cm",
    "NFO": "nse_fo",
    "BFO": "bse_fo",
}

_FO_CSV_TTL_S = 6 * 3600  # re-download each F&O scrip master at most every 6h
_FO_CACHE_DIR = os.path.join(RUNTIME_DIR, "kotak_cache")
_fo_cache_lock = None  # lazy-initialized below (RUNTIME_DIR may not exist yet)


def _fo_segment(underlying: str) -> str:
    return "bse_fo" if underlying.upper() in _BSE_INDEX_NAMES else "nse_fo"


def _fo_cache_path(segment: str) -> str:
    return os.path.join(_FO_CACHE_DIR, f"{segment}.csv")


def _load_fo_scrips(segment: str = "nse_fo") -> list[dict]:
    """The requested NFO/BFO scrip-master CSV, disk-cached for
    _FO_CSV_TTL_S. Returns [] on any failure — callers must treat an
    empty result as "cannot resolve contracts right now", not retry the
    download in a tight loop (the cache TTL already paces refetch)."""
    segment = segment.lower()
    cache_path = _fo_cache_path(segment)
    global _fo_cache_lock
    if _fo_cache_lock is None:
        _fo_cache_lock = __import__("threading").Lock()
    with _fo_cache_lock:
        if (
            os.path.isfile(cache_path)
            and (time.time() - os.path.getmtime(cache_path)) < _FO_CSV_TTL_S
        ):
            try:
                return _parse_fo_csv_file(cache_path)
            except Exception as exc:
                logger.warning("[kotak_market_data] cached scrip-master parse failed: %s", exc)

        rows = _download_fo_scrips(segment)
        if rows:
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                _write_fo_csv(cache_path, rows)
            except OSError as exc:
                logger.warning("[kotak_market_data] could not cache scrip master: %s", exc)
        return rows


def _download_fo_scrips(segment: str = "nse_fo") -> list[dict]:
    """Download and parse the requested Kotak NFO/BFO scrip master."""
    try:
        result = _session.client.scrip_master(exchange_segment=segment)
    except Exception as exc:
        logger.warning(
            "[kotak_market_data] scrip_master() failed: %s", exc
        )
        return []

    url = None

    # Older/alternate SDK behaviour: direct URL.
    if isinstance(result, str):
        if result.startswith("http"):
            url = result

    # Kotak Neo v2: {"filesPaths": [...], "baseFolder": "..."}
    elif isinstance(result, dict):
        paths = (
            result.get("filesPaths")
            or result.get("filePaths")
            or []
        )

        if isinstance(paths, str):
            paths = [paths]

        if isinstance(paths, list):
            # Prefer NSE F&O master specifically.
            for path in paths:
                if (
                    isinstance(path, str)
                    and path.startswith("http")
                    and segment in path.lower()
                ):
                    url = path
                    break

            # If API was already scoped to nse_fo and returned only
            # one URL, accept it.
            if url is None and len(paths) == 1:
                candidate = paths[0]
                if isinstance(candidate, str) and candidate.startswith("http"):
                    url = candidate

    if not url:
        logger.warning(
            "[kotak_market_data] scrip_master() returned "
            "unexpected response: %r",
            result,
        )
        return []

    try:
        import requests

        resp = requests.get(url, timeout=60)
        resp.raise_for_status()

        rows = _parse_fo_csv_text(resp.text)

        logger.info(
            "[kotak_market_data] parsed %d %s option contracts", len(rows), segment,
        )

        return rows

    except Exception as exc:
        logger.warning(
            "[kotak_market_data] scrip-master download failed: %s",
            exc,
        )
        return []

def _write_fo_csv(path: str, rows: list[dict]) -> None:
    headers = sorted({k for row in rows for k in row.keys()})
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def _parse_fo_csv_file(path: str) -> list[dict]:
    """Read our normalized on-disk cache.

    The downloaded Kotak CSV is parsed by _parse_fo_csv_text().
    _write_fo_csv() stores the already-normalized representation,
    therefore cached files must NOT be passed through the raw Kotak
    parser again.
    """
    with open(path, newline="") as f:
        reader = csv.DictReader(f)

        fields = set(reader.fieldnames or [])

        normalized_fields = {
            "name",
            "tradingsymbol",
            "token",
            "option_type",
            "strike",
            "expiry",
            "lot_size",
        }

        # Our normalized disk cache.
        if normalized_fields.issubset(fields):
            rows = []

            for raw in reader:
                try:
                    rows.append(
                        {
                            "name": str(raw["name"]).strip().upper(),
                            "tradingsymbol": str(
                                raw["tradingsymbol"]
                            ).strip().upper(),
                            "token": str(raw["token"]).strip(),
                            "option_type": str(
                                raw["option_type"]
                            ).strip().upper(),
                            "instrument_type": str(
                                raw.get("instrument_type") or ""
                            ).strip().upper(),
                            "strike": int(
                                round(float(raw["strike"]))
                            ),
                            "expiry": str(raw["expiry"]).strip(),
                            "lot_size": int(
                                float(raw.get("lot_size") or 0)
                            ),
                        }
                    )
                except (TypeError, ValueError, KeyError):
                    continue

            return rows

    # Compatibility with an old/raw cache if one exists.
    with open(path, newline="") as f:
        return _parse_fo_csv_text(f.read())


def _parse_fo_csv_text(text: str) -> list[dict]:
    # Kotak currently serves NFO as comma-separated CSV, but BFO masters
    # have also been observed as pipe/tab-delimited files. Do not let a
    # one-column DictReader silently turn a healthy BFO download into zero
    # contracts. Semicolon is intentionally excluded: it is part of
    # Kotak's literal `dStrikePrice;` field name, not a record delimiter.
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",|\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(StringIO(text), dialect=dialect)
    rows = []
    raw_count = 0
    option_type_counts: dict[str, int] = {}

    def field(raw, *names):
        # Kotak's BFO master does not always use the byte-for-byte header
        # spelling of its NFO master (notably dStrikePrice vs
        # dStrikePrice;). Normalize headers before selecting an alias.
        normalized = {
            str(key).lstrip("\ufeff").strip().rstrip(";").lower(): value
            for key, value in raw.items()
            if key
        }
        for name in names:
            value = normalized.get(name.rstrip(";").lower())
            if value not in (None, ""):
                return value
        return None

    for raw in reader:
        if not raw:
            continue
        raw_count += 1
        inst_type = str(field(raw, "pInstType", "instrument_type") or "").strip().upper()
        if inst_type not in ("OPTIDX", "OPTSTK", "FUTIDX", "FUTSTK"):
            # Kotak's BFO master leaves pInstType blank for many otherwise
            # valid derivative rows. Its trading symbol remains authoritative
            # (e.g. SENSEX26AUG76800CE / SENSEX26AUGFUT).
            symbol_hint = str(field(raw, "pTrdSymbol", "trading_symbol") or "").strip().upper()
            if symbol_hint.endswith(("CE", "PE")):
                inst_type = "OPTIDX"
            elif symbol_hint.endswith("FUT"):
                inst_type = "FUTIDX"
            else:
                continue
        opt_type = str(field(raw, "pOptionType", "option_type") or "").strip().upper()
        if not opt_type:
            symbol_hint = str(field(raw, "pTrdSymbol", "trading_symbol") or "").strip().upper()
            if symbol_hint.endswith(("CE", "PE")):
                opt_type = symbol_hint[-2:]
            elif symbol_hint.endswith("FUT"):
                opt_type = "FUT"
        option_type_counts[opt_type] = option_type_counts.get(opt_type, 0) + 1
        is_future = inst_type in ("FUTIDX", "FUTSTK")
        if not is_future and opt_type not in ("CE", "PE"):
            continue
        try:
            strike_paise = float(
                str(field(raw, "dStrikePrice", "strike_price", "strike") or "0").replace(",", "")
            )
        except (TypeError, ValueError):
            continue
        try:
            lot_size = int(float(field(raw, "lLotSize", "lot_size") or 0))
        except (TypeError, ValueError):
            lot_size = 0
        try:
            exp_raw = int(float(field(raw, "pExpiryDate", "expiry_date") or 0))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "name": str(field(raw, "pSymbolName", "symbol_name", "underlying") or "").strip().upper(),
                "tradingsymbol": str(field(raw, "pTrdSymbol", "trading_symbol") or "").strip().upper(),
                "token": str(field(raw, "pSymbol", "symbol", "token") or "").strip(),
                "option_type": "FUT" if is_future else opt_type,
                "instrument_type": inst_type,
                "strike": round(strike_paise / 100.0),
                "expiry": _unix_to_iso(exp_raw),
                "lot_size": lot_size,
            }
        )
    if raw_count and not rows:
        logger.warning(
            "[kotak_market_data] no option contracts parsed from %d rows; "
            "instrument types/options seen=%s; headers=%s",
            raw_count,
            option_type_counts,
            list(reader.fieldnames or []),
        )
    return rows


def _normalize_scrip_row(raw: dict) -> dict | None:
    """Normalize one official SDK search_scrip() result."""
    if not isinstance(raw, dict):
        return None
    def field(*names):
        normalized = {
            str(key).lstrip("\ufeff").strip().rstrip(";").lower(): value
            for key, value in raw.items() if key
        }
        return next((normalized.get(name.rstrip(";").lower()) for name in names
                     if normalized.get(name.rstrip(";").lower()) not in (None, "")), None)
    inst_type = str(field("pInstType", "instrument_type") or "").upper()
    option_type = str(field("pOptionType", "option_type") or "").upper()
    symbol_hint = str(field("pTrdSymbol", "trading_symbol") or "").upper()
    if not inst_type:
        if symbol_hint.endswith(("CE", "PE")):
            inst_type = "OPTIDX"
        elif symbol_hint.endswith("FUT"):
            inst_type = "FUTIDX"
    if not option_type:
        if symbol_hint.endswith(("CE", "PE")):
            option_type = symbol_hint[-2:]
        elif symbol_hint.endswith("FUT"):
            option_type = "FUT"
    is_future = inst_type in {"FUTIDX", "FUTSTK"}
    if not is_future and option_type not in {"CE", "PE"}:
        return None
    try:
        expiry = _unix_to_iso(int(float(field("pExpiryDate", "expiry_date") or 0)))
        strike = round(float(field("dStrikePrice", "strike_price", "strike") or 0) / 100.0)
    except (TypeError, ValueError):
        return None
    return {
        "name": str(field("pSymbolName", "symbol_name", "underlying") or "").upper(),
        "tradingsymbol": str(field("pTrdSymbol", "trading_symbol") or "").upper(),
        "token": str(field("pSymbol", "symbol", "token") or ""),
        "option_type": "FUT" if is_future else option_type,
        "instrument_type": inst_type,
        "strike": strike,
        "expiry": expiry,
        "lot_size": int(float(field("lLotSize", "lot_size") or 0)),
    }


def _contracts_for(underlying: str) -> list[dict]:
    """Use Neo's own segment-aware search/cache before manual CSV parsing."""
    segment = _fo_segment(underlying)
    try:
        result = _session.client.search_scrip(
            exchange_segment=segment,
            symbol=underlying.upper(),
            expiry="",
            option_type="CE,PE,FUT",
            strike_price="",
            ignore_50multiple=False,
        )
        raw_rows = result if isinstance(result, list) else (result or {}).get("data", [])
        rows = [row for item in raw_rows if (row := _normalize_scrip_row(item))]
        if rows:
            return rows
    except Exception as exc:
        logger.info("[kotak_market_data] search_scrip(%s, %s) unavailable: %s", segment, underlying, exc)
    return [row for row in _load_fo_scrips(segment) if row.get("name") == underlying.upper()]


def _unix_to_iso(epoch_seconds: int) -> str:
    """Kotak's scrip-master CSV stores expiry as a unix timestamp plus a
    ~10-year offset (their DB lapse workaround, see the SDK's own
    search_scrip conversion). Reversing it with the same 315511200s
    offset the SDK adds, formatted as this codebase's '%d-%b-%Y'."""
    return datetime.fromtimestamp(epoch_seconds + 315511200).strftime("%d-%b-%Y")


def _round_to_strike(price, underlying):
    interval = _STRIKE_INTERVALS.get(underlying.upper(), 50)
    return int(round(price / interval) * interval)


def _filters_for(underlying, expiry_iso=None, option_type=None, strikes=None):
    """Predicate matching a scrip-master row against an underlying, expiry
    ('DD-Mon-YYYY'), optional CE/PE, and optional strike set."""
    underlying_u = underlying.upper()
    exp_dt = datetime.strptime(expiry_iso, "%d-%b-%Y").date() if expiry_iso else None

    def matches(row):
        if row["name"] != underlying_u:
            return False
        if exp_dt is not None:
            try:
                row_exp = datetime.strptime(row["expiry"], "%d-%b-%Y").date()
            except (TypeError, ValueError):
                return False
            if row_exp != exp_dt:
                return False
        if option_type is not None and row["option_type"] != option_type:
            return False
        if strikes is not None and row["strike"] not in strikes:
            return False
        return True

    return matches


def list_expiries(underlying: str, exchange: str = "NFO") -> list:
    """Sorted 'DD-Mon-YYYY' expiry strings for `underlying`, nearest first,
    derived from the NFO scrip master. Returns [] if the master can't be
    fetched (callers already treat that as "no expiries right now")."""
    del exchange  # Kotak resolves the segment from the underlying itself
    rows = _contracts_for(underlying)
    if not rows:
        return []
    matches = [r for r in rows if r["name"] == underlying.upper() and r["option_type"] == "CE"]
    dates = set()
    for r in matches:
        try:
            dates.add(datetime.strptime(r["expiry"], "%d-%b-%Y").date())
        except (TypeError, ValueError):
            continue
    return [d.strftime("%d-%b-%Y") for d in sorted(dates)]


def find_option_token(underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"):
    """{'tradingsymbol', 'token'} for one contract via the scrip master, or
    None if unresolved."""
    del exchange
    rows = _contracts_for(underlying)
    if not rows:
        return None
    target_exp = _parse_expiry_date(expiry_ddmmmyyyy)
    matches = [
        r
        for r in rows
        if r["name"] == underlying.upper()
        and _parse_expiry_date(r["expiry"]) == target_exp
        and r["strike"] == int(round(float(strike)))
        and r["option_type"] == opt_type.upper()
    ]
    if not matches:
        return None
    return {"tradingsymbol": matches[0]["tradingsymbol"], "token": matches[0]["token"]}


def _parse_expiry_date(expiry_iso: str):
    try:
        return datetime.strptime(expiry_iso, "%d-%b-%Y").date()
    except (TypeError, ValueError):
        return None


def _ohlc_val(row, key, alt_key="open"):
    """Extract one OHLC component from Kotak's quote row, which nests OHLC
    inside 'ohlc' when quote_type='all' but may also carry flat fields."""
    ohlc = row.get("ohlc")
    if isinstance(ohlc, dict):
        val = ohlc.get(key)
        if val is not None:
            return float(val)
    flat = row.get(key) if key != "close" else row.get("previous_close")
    if flat is not None:
        return float(flat)
    return float(alt_key and row.get(alt_key) or 0)


def _spot_quote(underlying: str) -> dict | None:
    symbol = underlying.upper()

    if symbol in _BSE_INDEX_NAMES:
        index_token = _BSE_INDEX_NAMES[symbol]
        segment = "bse_cm"
    else:
        index_token = _INDEX_NAMES.get(symbol)
        segment = "nse_cm"

    if not index_token:
        return None

    try:
        result = _session.client.quotes(
            instrument_tokens=[
                {
                    "instrument_token": index_token,
                    "exchange_segment": segment,
                }
            ],
            quote_type="all",
        )
    except Exception as exc:
        logger.warning(
            "[kotak_market_data] index quote %s failed: %s",
            underlying,
            exc,
        )
        return None

    rows = _unwrap_quotes(result)
    if not rows:
        return None

    row = rows[0]

    return {
        "ltp": float(
            row.get("ltp")
            or row.get("last_traded_price")
            or 0
        ),
        "open": _ohlc_val(row, "open"),
        "high": _ohlc_val(row, "high"),
        "low": _ohlc_val(row, "low"),
        "close": _ohlc_val(row, "close", alt_key=None),
    }

def _quote_token(row) -> str:
    """Token identifier returned by Kotak quote responses.

    Neo quote responses use exchange_token, while some SDK/API
    versions may expose instrument_token. Support both.
    """
    return str(
        row.get("exchange_token")
        or row.get("instrument_token")
        or row.get("token")
        or ""
    ).strip()

def _unwrap_quotes(result):
    """Kotak's quotes() returns the raw JSON body; the payload can be a
    bare list (v2) or wrapped in 'data'/'message' (v1-era). Normalize to a
    list of row dicts."""
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        for key in ("data", "message", "quotes"):
            val = result.get(key)
            if isinstance(val, list):
                return val
            if isinstance(val, dict):
                # Single row wrapped in a dict.
                return [val]
    return []


def get_atm_chain(underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"):
    """Resolve the ATM window from the scrip master, then overlay live
    ltp/oi/volume per token via quotes(). Returns the chain dict shape
    from the MarketData Protocol ('underlying', 'spot', 'atm_strike',
    'expiry', 'rows') or None."""
    del exchange
    quote = _spot_quote(underlying)
    if not quote:
        logger.warning("[kotak_market_data] could not fetch spot for %s", underlying)
        return None
    spot = quote["ltp"]
    atm = _round_to_strike(spot, underlying)
    interval = _STRIKE_INTERVALS.get(underlying.upper(), 50)
    strikes = {atm + i * interval for i in range(-strikes_around_atm, strikes_around_atm + 1)}

    segment = _fo_segment(underlying)
    rows = _contracts_for(underlying)
    if not rows:
        return None
    target_exp = _parse_expiry_date(expiry_ddmmmyyyy)
    matches = [
        r for r in rows
        if r["name"] == underlying.upper()
        and _parse_expiry_date(r["expiry"]) == target_exp
        and r["strike"] in strikes
    ]
    if not matches:
        return None

    # Batch-quote all CE+PE tokens in one quotes() call.
    tokens = [{"instrument_token": m["token"], "exchange_segment": segment} for m in matches]
    try:
        qresult = _session.client.quotes(instrument_tokens=tokens, quote_type="all")
        qrows = _unwrap_quotes(qresult)
    except Exception as exc:
        logger.warning("[kotak_market_data] batch quote failed: %s", exc)
        qrows = []
    quote_by_token = {}

    for q in qrows:
        tok = _quote_token(q)
        if tok:
            quote_by_token[tok] = q

    out = []
    for m in matches:
        q = quote_by_token.get(m["token"]) or {}
        out.append(
            {
                "strike": m["strike"],
                "type": m["option_type"],
                "tradingsymbol": m["tradingsymbol"],
                "token": m["token"],
                "ltp": float(
                    q.get("ltp")
                    or q.get("last_traded_price")
                    or 0
                ),
                "open": _ohlc_val(q, "open"),
                "high": _ohlc_val(q, "high"),
                "low": _ohlc_val(q, "low"),
                "close": _ohlc_val(q, "close", alt_key=None),
                "oi": float(
                    q.get("open_int")
                    or q.get("open_interest")
                    or 0
                ),
                "volume": float(
                    q.get("last_volume")
                    or q.get("volume")
                    or 0
                ),
                # Neo response shapes vary by SDK version: v2 commonly
                # uses `change`, while others expose `net_change`. Preserve
                # an explicit zero rather than treating it as missing.
                "net_change": (
                    q.get("net_change")
                    if q.get("net_change") is not None
                    else q.get("change")
                ),
                "pct_change": (
                    q.get("per_change")
                    if q.get("per_change") is not None
                    else q.get("net_change_percentage")
                ),
                "lot_size": m["lot_size"],
            }
        )

    out.sort(key=lambda r: (r["strike"], r["type"]))
    return {
        "underlying": underlying.upper(),
        "spot": spot,
        "atm_strike": atm,
        "expiry": expiry_ddmmmyyyy,
        "rows": out,
    }


def get_batch_quotes(exchange, symbol_token_pairs, mode="FULL"):
    """One quotes() call for up to all pairs. `symbol_token_pairs` is
    (tradingsymbol, token) as returned by find_option_token(); the response
    is keyed by the numeric instrument token, so it's re-keyed by the
    caller's tradingsymbol here to match the Protocol contract."""
    del mode
    seg = _INDEX_EXCHANGE.get(str(exchange).upper(), "nse_fo")
    tokens = [
        {"instrument_token": str(token), "exchange_segment": seg}
        for _symbol, token in symbol_token_pairs
        if token
    ]
    if not tokens:
        return {}
    try:
        result = _session.client.quotes(instrument_tokens=tokens, quote_type="all")
        qrows = _unwrap_quotes(result)
    except Exception as exc:
        logger.warning("[kotak_market_data] batch quotes failed: %s", exc)
        return {}
    by_token = {
        _quote_token(q): q
        for q in qrows
        if _quote_token(q)
    }
    out = {}
    for symbol, token in symbol_token_pairs:
        q = by_token.get(str(token))
        if q:
            out[symbol] = q
    return out


def get_batch_quotes_by_token(exchange, symbol_token_pairs, mode="FULL"):
    """Same request as get_batch_quotes(), keyed by str(token) instead of
    tradingsymbol — matches the SmartAPI/Breeze convention."""
    by_symbol = get_batch_quotes(exchange, symbol_token_pairs, mode=mode)
    return {str(token): by_symbol[symbol] for symbol, token in symbol_token_pairs if symbol in by_symbol}


def get_spot_quote(underlying):
    return _spot_quote(underlying)


def get_futures_quote(underlying: str, which: str = "NEAR") -> dict | None:
    """Resolve and quote one Kotak F&O future for an underlying.

    This is especially important for SENSEX/BANKEX: BSE's public futures
    table can omit the last-traded price even though Kotak's BFO quote feed
    has it. Returns the standard futures row shape used by broker_pipeline.
    """
    segment = _fo_segment(underlying)
    today = datetime.now().date()
    contracts = []
    for row in _contracts_for(underlying):
        if row.get("name") != underlying.upper() or row.get("option_type") != "FUT":
            continue
        expiry = _parse_expiry_date(row.get("expiry"))
        if expiry:
            contracts.append((expiry, row))
    contracts = [(expiry, row) for expiry, row in contracts if expiry >= today] or contracts
    if not contracts:
        return None
    contracts.sort(key=lambda item: item[0])
    slot = {"NEAR": 0, "NEXT": 1, "FAR": 2}.get((which or "NEAR").upper(), 0)
    expiry, contract = contracts[min(slot, len(contracts) - 1)]
    try:
        result = _session.client.quotes(
            instrument_tokens=[
                {"instrument_token": contract["token"], "exchange_segment": segment}
            ],
            quote_type="all",
        )
        qrows = _unwrap_quotes(result)
    except Exception as exc:
        logger.warning("[kotak_market_data] future quote failed for %s: %s", underlying, exc)
        return None
    quote = next((q for q in qrows if _quote_token(q) == str(contract["token"])), None)
    if not quote:
        return None
    ltp = quote.get("ltp") if quote.get("ltp") is not None else quote.get("last_traded_price")
    try:
        ltp = float(ltp)
    except (TypeError, ValueError):
        return None
    if ltp <= 0:
        return None
    spot_quote = _spot_quote(underlying)
    spot = spot_quote.get("ltp") if spot_quote else None
    return {
        "Contract": contract["tradingsymbol"],
        "Underlying": underlying.upper(),
        "Expiry": expiry.strftime("%d-%b-%Y"),
        "LTP": ltp,
        "Change": quote.get("net_change", quote.get("change")),
        "PctChange": quote.get("per_change", quote.get("net_change_percentage")),
        "Open": _ohlc_val(quote, "open"),
        "High": _ohlc_val(quote, "high"),
        "Low": _ohlc_val(quote, "low"),
        "PrevClose": _ohlc_val(quote, "close", alt_key=None),
        "Volume": quote.get("last_volume", quote.get("volume")),
        "OI": quote.get("open_int", quote.get("open_interest")),
        "Spot": spot,
        "Basis": round(ltp - spot, 2) if spot else None,
    }

def get_fno_underlyings(force_refresh=False):
    """Indices + F&O stocks derived from the NFO scrip master."""
    del force_refresh

    rows = _load_fo_scrips()

    if not rows:
        return {
            "indices": list(_INDEX_NAMES),
            "stocks": [],
        }

    names = sorted({r["name"] for r in rows})

    indices = sorted(
        n for n in names
        if n in _INDEX_NAMES
    )

    stocks = [
        n for n in names
        if n not in _INDEX_NAMES
    ]

    return {
        "indices": indices or list(_INDEX_NAMES),
        "stocks": stocks,
    }


def index_tokens():
    out = {}

    for name, token in _INDEX_NAMES.items():
        out[name] = {
            "token": token,
            "exchange": "NSE",
        }

    for name, token in _BSE_INDEX_NAMES.items():
        out[name] = {
            "token": token,
            "exchange": "BSE",
        }

    return out


class KotakMarketData:
    """Adapter satisfying brokers.market_data.MarketData — see that
    module's Protocol for the interface contract."""

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

    def get_futures_quote(self, underlying, which="NEAR"):
        return get_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        return get_fno_underlyings(force_refresh=force_refresh)

    def index_tokens(self):
        return index_tokens()
