"""Kotak-backed NIFTY cash-auction indicative value."""

from __future__ import annotations

import csv
import io
import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta
from datetime import time as daytime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
_IST = ZoneInfo("Asia/Kolkata")
_CAS_START = daytime(15, 20)
_CAS_END = daytime(15, 30)
_IEP_WINDOW_SECONDS = 15
_COVERAGE_REQUIRED = 0.98


def parse_cash_scrip_master(text: str, symbols: set[str]) -> dict[str, str]:
    """Resolve NSE EQ symbols to Kotak cash tokens without guessing."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",|\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    resolved: dict[str, str] = {}
    duplicates: set[str] = set()
    for row in reader:
        fields = {
            str(key).lstrip("\ufeff").strip().rstrip(";").lower(): value
            for key, value in row.items()
            if key
        }
        series = str(fields.get("pseries") or fields.get("series") or "").strip().upper()
        if series and series != "EQ":
            continue
        symbol = str(
            fields.get("psymbolname")
            or fields.get("symbol_name")
            or fields.get("tradingsymbol")
            or fields.get("ptrdsymbol")
            or ""
        ).strip().upper()
        symbol = symbol.removesuffix("-EQ")
        token = str(
            fields.get("psymbol") or fields.get("token") or fields.get("instrument_token") or ""
        ).strip()
        if symbol not in symbols or not token or not token.isdigit():
            continue
        if symbol in resolved and resolved[symbol] != token:
            duplicates.add(symbol)
        else:
            resolved[symbol] = token
    for symbol in duplicates:
        resolved.pop(symbol, None)
    return resolved


def extract_indicative_closes(
    frame: bytes, dividers: dict[int, int] | None = None
) -> list[tuple[str, float, bool]]:
    """Extract Kotak native_batch IEP values and auction flags from raw frames."""
    from neo_api_client.websocket.feed.protocol import (
        _HEADER,
        _MP_BODY,
        DEFAULT_DIVIDER,
        HEADER_SIZE,
        MSG_MARKET_PICTURE,
        split_batch,
    )

    values: list[tuple[str, float, bool]] = []
    for packet in split_batch(frame):
        if len(packet) < HEADER_SIZE:
            continue
        _, message_code, exchange_id, level, auction_flag, _, _ = _HEADER.unpack_from(
            packet
        )
        if message_code != MSG_MARKET_PICTURE or level not in (2, 3, 4, 5):
            continue
        if len(packet) < HEADER_SIZE + _MP_BODY.size:
            continue
        fields = _MP_BODY.unpack_from(packet, HEADER_SIZE)
        token = str(fields[0])
        iep = fields[13]
        divider = (dividers or {}).get(exchange_id) or DEFAULT_DIVIDER
        if token.isdigit() and iep > 0 and divider > 0:
            values.append((token, iep / divider, auction_flag > 0))
    return values


def compute_nifty_cas(
    rows,
    indicative_prices: dict[str, tuple[float, float]],
    *,
    now: datetime | None = None,
) -> dict:
    """Estimate the NIFTY level from constituent IEPs and float market caps.

    Each row requires the NIFTY 50 index label, EQ series, a positive last
    price, previous close, and free-float market cap. The market cap is
    normalized to the previous close so the calculation weights prior
    free-float capitalization, not arbitrary equal weights.
    """
    now = now or datetime.now(_IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=_IST)
    constituents: dict[str, tuple[float, float, float]] = {}
    index_close = None
    for row in rows:
        index_name = str(row.get("Index", "")).strip().upper()
        symbol = str(row.get("Symbol", "")).strip().upper()
        series = str(row.get("Series", "")).strip().upper()
        if series in {"NONE", "NAN"}:
            series = ""
        if index_name not in {"NIFTY 50", "NIFTY50"}:
            continue
        if symbol in {"NIFTY", "NIFTY 50", "NIFTY50"} and not series:
            index_close = _positive_number(row.get("Prev Close"))
            continue
        if series and series != "EQ":
            continue
        last_price = _positive_number(row.get("Last Price"))
        previous_close = _positive_number(row.get("Prev Close"))
        ffmc = _positive_number(row.get("ffmc"))
        if symbol and last_price and previous_close and ffmc:
            constituents[symbol] = (last_price, previous_close, ffmc)

    if len(constituents) != 50 or not index_close or not indicative_prices:
        return _unavailable_cas(
            now, 0, len(constituents), reason="constituent_data_unavailable"
        )

    weighted_return = 0.0
    total_previous_ffmc = 0.0
    valid_count = 0
    for symbol, (last_price, previous_close, ffmc) in constituents.items():
        iep_record = indicative_prices.get(symbol)
        if iep_record is None:
            continue
        iep, received_at = iep_record
        age = now.timestamp() - received_at
        if age < 0 or age > _IEP_WINDOW_SECONDS or iep <= 0:
            continue
        previous_ffmc = ffmc * previous_close / last_price
        if not math.isfinite(previous_ffmc) or previous_ffmc <= 0:
            continue
        weighted_return += previous_ffmc * (iep / previous_close - 1.0)
        total_previous_ffmc += previous_ffmc
        valid_count += 1

    coverage = valid_count / len(constituents)
    if coverage < _COVERAGE_REQUIRED or total_previous_ffmc <= 0:
        return _unavailable_cas(
            now,
            valid_count,
            len(constituents),
            coverage,
            reason="insufficient_iep_coverage",
        )

    value = index_close * (1.0 + weighted_return / total_previous_ffmc)
    if not math.isfinite(value) or value <= 0:
        return _unavailable_cas(
            now,
            valid_count,
            len(constituents),
            coverage,
            reason="invalid_calculation",
        )
    return {
        "status": "live",
        "value": round(value, 2),
        "asOf": now.isoformat(timespec="seconds"),
        "source": "Kotak Neo CAS IEP",
        "coverage": {"available": valid_count, "total": len(constituents)},
        "coveragePct": round(coverage * 100, 1),
    }


def _positive_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _unavailable_cas(
    now: datetime,
    available: int = 0,
    total: int = 0,
    coverage: float = 0.0,
    *,
    reason: str = "insufficient_iep_coverage",
) -> dict:
    return {
        "status": "unavailable",
        "reason": reason,
        "value": None,
        "asOf": now.isoformat(timespec="seconds"),
        "source": "Kotak Neo CAS IEP",
        "coverage": {"available": available, "total": total},
        "coveragePct": round(coverage * 100, 1),
    }


def _cash_scrips(symbols: set[str]) -> dict[str, str]:
    from infrastructure.paths import RUNTIME_DIR

    cache_dir = os.path.join(RUNTIME_DIR, "kotak_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, "nse_cm.csv")
    if os.path.isfile(cache_path) and time.time() - os.path.getmtime(cache_path) < 6 * 3600:
        with open(cache_path, encoding="utf-8", newline="") as cached:
            result = parse_cash_scrip_master(cached.read(), symbols)
        if symbols <= result.keys():
            return result

    from brokers.kotak.client import _session

    result = _session.client.scrip_master(exchange_segment="nse_cm")
    urls = []
    if isinstance(result, str) and result.startswith("https://"):
        urls.append(result)
    elif isinstance(result, dict):
        paths = result.get("filesPaths") or result.get("filePaths") or []
        urls.extend(path for path in ([paths] if isinstance(paths, str) else paths)
                    if isinstance(path, str) and path.startswith("https://"))
    url = next((item for item in urls if "nse_cm" in item.lower()), None)
    if url is None and len(urls) == 1:
        url = urls[0]
    if not url:
        raise RuntimeError("Kotak NSE cash scrip master URL was not returned")
    import requests

    response = requests.get(url, timeout=60)
    response.raise_for_status()
    text = response.text
    resolved = parse_cash_scrip_master(text, symbols)
    if not resolved:
        raise RuntimeError("Kotak NSE cash scrip master had no matching EQ tokens")
    with open(cache_path, "w", encoding="utf-8", newline="") as cached:
        cached.write(text)
    return resolved


class NiftyCASFeed:
    """Own an independent Kotak socket, active only during the CAS window."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._prices: dict[str, tuple[float, float]] = {}
        self._token_to_symbol: dict[str, str] = {}
        self._thread: threading.Thread | None = None
        self._stream = None
        self._stop = threading.Event()

    def indicative(self, rows) -> dict:
        now = datetime.now(_IST)
        if not (_CAS_START <= now.time() < _CAS_END):
            return _unavailable_cas(now, reason="outside_cas_window")
        with self._lock:
            prices = dict(self._prices)
        return compute_nifty_cas(rows, prices, now=now)

    def start(self) -> None:
        self._start_if_configured()

    def _start_if_configured(self) -> None:
        from infrastructure.config import settings

        required = (
            settings.kotak_consumer_key,
            settings.kotak_mobile,
            settings.kotak_ucc,
            settings.kotak_totp_secret,
            settings.kotak_mpin,
        )
        if not all(required):
            names = (
                "KOTAK_CONSUMER_KEY",
                "KOTAK_MOBILE",
                "KOTAK_UCC",
                "KOTAK_TOTP_SECRET",
                "KOTAK_MPIN",
            )
            missing = [name for name, value in zip(names, required) if not value]
            logger.info(
                "[nifty_cas] Kotak CAS feed not started; missing settings: %s",
                ", ".join(missing),
            )
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(
                target=self._run, name="nifty-cas-kotak", daemon=True
            )
            self._thread.start()

    def _run(self) -> None:

        while not self._stop.is_set():
            now = datetime.now(_IST)
            opening = datetime.combine(now.date(), _CAS_START, _IST)
            closing = datetime.combine(now.date(), _CAS_END, _IST)
            if now.weekday() >= 5 or now >= closing:
                opening += timedelta(days=1)
                while opening.weekday() >= 5:
                    opening += timedelta(days=1)
            if now < opening:
                self._stop.wait(min((opening - now).total_seconds(), 60))
                continue
            if self._stop.is_set():
                break
            try:
                self._connect_and_stream()
            except Exception:
                logger.exception("[nifty_cas] Kotak CAS feed failed")
            self._stop.wait(5)

    def _connect_and_stream(self) -> None:
        from brokers.kotak.websocket import KotakTickStream

        with self._lock:
            self._prices.clear()
        symbols = self._constituent_symbols()
        token_map = _cash_scrips(symbols)
        missing = symbols - token_map.keys()
        if missing:
            raise RuntimeError(
                f"Kotak cash tokens missing for {len(missing)} NIFTY constituents"
            )
        with self._lock:
            self._token_to_symbol = {token: symbol for symbol, token in token_map.items()}
        stream = KotakTickStream(
            on_tick=lambda _tick: None,
            mode="full",
            on_raw=self._on_raw,
        )
        self._stream = stream
        instruments = [
            {"instrument_token": token, "exchange_segment": "nse_cm"}
            for token in token_map.values()
        ]
        stream.connect()
        stream.subscribe(instruments)
        close_delay = max(
            0.0,
            (
                datetime.combine(datetime.now(_IST).date(), _CAS_END, _IST)
                - datetime.now(_IST)
            ).total_seconds(),
        )
        close_timer = threading.Timer(close_delay, stream.stop)
        close_timer.daemon = True
        close_timer.start()
        try:
            stream.run_forever_with_reconnect()
        finally:
            close_timer.cancel()
            self._stream = None

    @staticmethod
    def _constituent_symbols() -> set[str]:
        from market.providers.nse_bse_client import fetch_fno_index, parse_index_records

        records = parse_index_records(fetch_fno_index("NIFTY 50"), "NIFTY 50")
        symbols = {
            str(row.get("Symbol", "")).strip().upper()
            for row in records
            if str(row.get("Series", "")).strip().upper() == "EQ"
        }
        if len(symbols) < 45:
            raise RuntimeError(
                f"NSE NIFTY 50 constituent list incomplete ({len(symbols)} symbols)"
            )
        return symbols

    def _on_raw(self, frame) -> None:
        if not isinstance(frame, (bytes, bytearray)):
            return
        ws = self._stream._ws if self._stream else None
        dividers = getattr(ws, "_dividers", {})
        now = time.time()
        parsed = extract_indicative_closes(bytes(frame), dividers)
        with self._lock:
            for token, iep, auction in parsed:
                symbol = self._token_to_symbol.get(token)
                if symbol and auction:
                    self._prices[symbol] = (iep, now)

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            self._stream.stop()


_service = NiftyCASFeed()


def get_nifty_cas_indicative(rows) -> dict:
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    return _service.indicative(rows)


def start_nifty_cas_feed() -> None:
    _service.start()
