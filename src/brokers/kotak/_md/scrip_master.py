"""Kotak Neo NFO/BFO scrip-master download, disk cache and CSV parsing.

The scrip master is the contract-resolution source for ``list_expiries()``
and ``find_option_token()``. It is re-downloaded at most every 6h and cached
under ``paths.RUNTIME_DIR`` so it survives restarts without living inside the
package (same approach as brokers/breeze_market_data.py's stock-code map).
"""
from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import datetime
from io import StringIO

try:
    from infrastructure.config import settings  # noqa: F401
    from infrastructure.paths import RUNTIME_DIR
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from infrastructure.config import settings  # noqa: F401
    from infrastructure.paths import RUNTIME_DIR

from brokers.kotak.client import _session
from .constants import _BSE_INDEX_NAMES

logger = logging.getLogger(__name__)

_FO_CSV_TTL_S = 6 * 3600  # re-download each F&O scrip master at most every 6h
_FO_CACHE_DIR = os.path.join(RUNTIME_DIR, "kotak_cache")
_fo_cache_lock = threading.Lock()


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


def _unix_to_iso(epoch_seconds: int) -> str:
    """Kotak's scrip-master CSV stores expiry as a unix timestamp plus a
    ~10-year offset (their DB lapse workaround, see the SDK's own
    search_scrip conversion). Reversing it with the same 315511200s
    offset the SDK adds, formatted as this codebase's '%d-%b-%Y'."""
    return datetime.fromtimestamp(epoch_seconds + 315511200).strftime("%d-%b-%Y")
