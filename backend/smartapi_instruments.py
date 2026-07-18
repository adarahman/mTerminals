"""
angelone_instruments.py
-----------------------
Resolves NSE/BSE F&O trading symbols (NIFTY, BANKNIFTY, MIDCPNIFTY, SENSEX
options/futures) to the numeric `symboltoken` AngelOne's SmartAPI needs for
quotes, WebSocket subscriptions, and order placement.

AngelOne publishes a daily instrument master (~ tens of MB, all exchanges,
all instruments) as a single JSON file. This module downloads it, caches it
locally, and builds fast lookup structures so option_chain_json.py / engine.py
can resolve symboltoken without re-parsing the master file per request.

Usage:
    resolver = InstrumentResolver()
    resolver.load()  # downloads fresh, or reads today's cache if present

    # Get a specific option contract token
    token = resolver.get_option_token(
        underlying="NIFTY", expiry="2026-07-16", strike=25000, option_type="CE"
    )

    # Get all strikes for an expiry (for building the option chain)
    chain = resolver.get_option_chain_tokens("NIFTY", "2026-07-16")

    # Get the index spot token
    spot_token = resolver.get_index_token("NIFTY")
"""

import os
import re
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None

logger = logging.getLogger("angelone.instruments")

INSTRUMENT_MASTER_URL = (
    "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
)

CACHE_DIR = Path(os.environ.get("ANGELONE_CACHE_DIR", "./.angelone_cache"))
CACHE_FILE_TEMPLATE = "instrument_master_{date}.json"

# Underlying -> exchange + name-in-master mapping.
# AngelOne's master lists the "name" field as the underlying symbol exactly
# as NSE/BSE define it — these are the values actually seen in the file.
UNDERLYING_MAP = {
    "NIFTY":       {"exch_seg": "NFO", "name": "NIFTY"},
    "BANKNIFTY":   {"exch_seg": "NFO", "name": "BANKNIFTY"},
    "MIDCPNIFTY":  {"exch_seg": "NFO", "name": "MIDCPNIFTY"},
    "SENSEX":      {"exch_seg": "BFO", "name": "SENSEX"},
    "BANKEX":      {"exch_seg": "BFO", "name": "BANKEX"},
    "SENSEX50":    {"exch_seg": "BFO", "name": "SENSEX50"},
}

# Index spot tokens are fixed and well-known (NSE_CM / BSE_CM segment) —
# no need to search the master for these.
#
# NOTE on SENSEX50's tradingsymbol: unlike SENSEX/BANKEX, AngelOne's own
# AMXIDX spot row calls this index "S&P BSE SENSEX 50" in its `name` field
# — but the `symbol` field (the actual tradingsymbol to quote/subscribe
# with) is "SNSX50", matching the same short-code pattern as the others.
# Verified directly against the local scrip master cache.
INDEX_SPOT_TOKENS = {
    "NIFTY":      {"exchange": "NSE", "symboltoken": "99926000", "tradingsymbol": "Nifty 50"},
    "BANKNIFTY":  {"exchange": "NSE", "symboltoken": "99926009", "tradingsymbol": "Nifty Bank"},
    "MIDCPNIFTY": {"exchange": "NSE", "symboltoken": "99926074", "tradingsymbol": "NIFTY MID SELECT"},
    "SENSEX":     {"exchange": "BSE", "symboltoken": "99919000", "tradingsymbol": "SENSEX"},
    "BANKEX":     {"exchange": "BSE", "symboltoken": "99919012", "tradingsymbol": "BANKEX"},
    "SENSEX50":   {"exchange": "BSE", "symboltoken": "99919082", "tradingsymbol": "SNSX50"},
}


# Equity F&O futures only — one FUT per (underlying, expiry) vs hundreds of
# CE/PE options. Lot size is identical for that underlying's futures and all
# option contracts in a given NSE lot-size revision.
_FNO_FUT_TYPES = frozenset({"FUTSTK", "FUTIDX"})
_FNO_EXCHANGES = frozenset({"NFO", "BFO"})
# AngelOne master mixes dummy/test underlyings (e.g. "011NSETEST") into the
# FUT segment — drop them so they never surface in /api/lot-sizes.
_TEST_SYMBOL_RE = re.compile(r"NSETEST", re.IGNORECASE)


class InstrumentResolver:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._instruments: list = []
        # (underlying, expiry_str, strike, option_type) -> instrument dict
        self._option_index: dict = {}
        # underlying -> {expiry_str -> [instrument dicts]}
        self._chain_index: dict = {}
        # underlying (upper) -> lot size, sourced from FUTSTK/FUTIDX only
        self._lot_size_map: dict = {}

    def _today_cache_path(self) -> Path:
        return self.cache_dir / CACHE_FILE_TEMPLATE.format(date=date.today().isoformat())

    def load(self, force_refresh: bool = False):
        """Loads instrument master from today's local cache if present,
        otherwise downloads fresh from AngelOne and caches it. The master
        changes daily (new expiries roll in), so cache is date-scoped."""
        cache_path = self._today_cache_path()

        if not force_refresh and cache_path.exists():
            logger.info("Loading instrument master from cache: %s", cache_path)
            with open(cache_path, "rb") as f:
                raw = f.read()
            self._instruments = (
                _orjson.loads(raw) if _orjson is not None else json.loads(raw)
            )
        else:
            logger.info("Downloading instrument master from AngelOne...")
            resp = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
            resp.raise_for_status()
            self._instruments = resp.json()
            if _orjson is not None:
                with open(cache_path, "wb") as f:
                    f.write(_orjson.dumps(self._instruments))
            else:
                with open(cache_path, "w") as f:
                    json.dump(self._instruments, f)
            logger.info(
                "Cached %d instruments to %s", len(self._instruments), cache_path
            )

        self._build_indexes()
        return self

    def _build_indexes(self):
        """
        Master JSON records look like:
        {
          "token": "58662",
          "symbol": "NIFTY16JUL2625000CE",
          "name": "NIFTY",
          "expiry": "16JUL2026",
          "strike": "2500000.000000",   # NOTE: strike is in paise (x100)
          "lotsize": "75",
          "instrumenttype": "OPTIDX",
          "exch_seg": "NFO",
          "tick_size": "5.000000"
        }

        Lot sizes are taken from FUTSTK/FUTIDX only (not CE/PE). Every F&O
        underlying shares one lot size across its futures and all option
        contracts for a given revision — scanning thousands of OPT rows is
        wasted work and memory. First-seen FUT wins when multiple expiries
        exist (lot size is identical across expiries).

        Option indexes still cover whatever OPTIDX/OPTSTK names appear in
        the master (token/chain resolution), using each record's own
        exch_seg rather than UNDERLYING_MAP (which only lists indices for
        spot-token convenience).
        """
        self._option_index.clear()
        self._chain_index.clear()
        self._lot_size_map.clear()

        for inst in self._instruments:
            itype = inst.get("instrumenttype") or ""
            exch = inst.get("exch_seg") or ""
            name = (inst.get("name") or "").strip().upper()

            # ── Lot size from futures alone ────────────────────────────
            # Prefer NFO/BFO equity-index futures; skip currency/commodity
            # FUT* rows that would pollute the F&O map. First expiry wins —
            # lot size is identical across near/next/far for a given revision.
            if (
                itype in _FNO_FUT_TYPES
                and exch in _FNO_EXCHANGES
                and name
                and name not in self._lot_size_map
                and not _TEST_SYMBOL_RE.search(name)
            ):
                try:
                    lot = int(float(inst.get("lotsize") or 0))
                except (TypeError, ValueError):
                    lot = 0
                if lot > 0:
                    self._lot_size_map[name] = lot

            if itype not in ("OPTIDX", "OPTSTK"):
                continue

            if not name:
                continue

            symbol = inst.get("symbol", "")
            option_type = symbol[-2:] if symbol.endswith(("CE", "PE")) else None
            if option_type not in ("CE", "PE"):
                continue

            raw_expiry = inst.get("expiry", "")  # e.g. "16JUL2026"
            try:
                expiry_norm = datetime.strptime(raw_expiry, "%d%b%Y").date().isoformat()
            except ValueError:
                continue

            try:
                strike = round(float(inst.get("strike", "0")) / 100.0, 2)
            except ValueError:
                continue

            key = (name, expiry_norm, strike, option_type)
            self._option_index[key] = inst

            self._chain_index.setdefault(name, {}).setdefault(expiry_norm, []).append(inst)

        logger.info(
            "Indexed %d option contracts across %d underlyings; "
            "%d lot sizes from FUTSTK/FUTIDX",
            len(self._option_index),
            len(self._chain_index),
            len(self._lot_size_map),
        )

    def get_option_token(
        self, underlying: str, expiry: str, strike: float, option_type: str
    ) -> Optional[dict]:
        """
        expiry: 'YYYY-MM-DD' (normalized ISO format)
        strike: e.g. 25000 or 25000.0
        option_type: 'CE' | 'PE'
        Returns dict with token, symbol, lotsize, tick_size, exch_seg — or None.
        """
        key = (underlying.upper(), expiry, round(float(strike), 2), option_type.upper())
        return self._option_index.get(key)

    def get_option_chain_tokens(self, underlying: str, expiry: str) -> list:
        """All strikes (both CE and PE) for a given underlying + expiry."""
        return self._chain_index.get(underlying.upper(), {}).get(expiry, [])

    def get_available_expiries(self, underlying: str) -> list:
        return sorted(self._chain_index.get(underlying.upper(), {}).keys())

    def get_index_token(self, underlying: str) -> dict:
        """Fixed spot index token — no master lookup needed."""
        return INDEX_SPOT_TOKENS[underlying.upper()]

    def get_lot_size(self, underlying: str) -> int:
        """
        Current lot size for `underlying`, from the FUTSTK/FUTIDX row in
        the AngelOne master (not from CE/PE options).

        One futures contract per expiry is enough: lot size is shared by
        that underlying's futures and every option contract for the same
        NSE lot-size revision. Raises KeyError if no FUT row was indexed
        (call .load() first, or the name isn't an F&O underlying) —
        silently guessing would corrupt OI/GEX/PnL math.
        """
        underlying = (underlying or "").upper()
        lot = self._lot_size_map.get(underlying)
        if lot is None:
            raise KeyError(
                f"No FUT-derived lot size for '{underlying}' — call .load() "
                f"first, or check the name is a live NFO/BFO F&O underlying."
            )
        return lot

    def get_all_lot_sizes(self) -> dict:
        """Full underlying -> lot size map (copy) for /api/lot-sizes etc."""
        return dict(self._lot_size_map)

    def build_ws_subscription_list(self, underlying: str, expiry: str) -> list:
        """
        Builds the token list block for AngelOneTicker.subscribe_tokens(),
        e.g. for feeding straight into option_chain_json.py's WS setup.
        Returns: [{"exchangeType": <NFO/BFO int>, "tokens": [...]}]
        """
        contracts = self.get_option_chain_tokens(underlying, expiry)
        if not contracts:
            raise KeyError(f"No contracts found for '{underlying}' at {expiry}.")
        # exch_seg comes off the contracts themselves (every record already
        # carries it) rather than UNDERLYING_MAP, which only covers indices
        # and would KeyError for any stock underlying.
        exch_seg = contracts[0]["exch_seg"]
        exchange_key = "NSE_FO" if exch_seg == "NFO" else "BSE_FO"
        tokens = [c["token"] for c in contracts]
        return {exchange_key: tokens}


# ── Module-level singleton ────────────────────────────────────────────────
# Lets other modules (e.g. engine.py) do `get_lot_size("NIFTY")` without
# owning/loading a resolver instance themselves. Loads (or reads today's
# cache) once, lazily, on first use.
_default_resolver: Optional["InstrumentResolver"] = None


def _resolver() -> "InstrumentResolver":
    global _default_resolver
    if _default_resolver is None:
        _default_resolver = InstrumentResolver().load()
    return _default_resolver


def get_lot_size(underlying: str, refresh: bool = False) -> int:
    """Convenience wrapper: resolves lot size via the shared resolver.
    Source of truth is FUTSTK/FUTIDX rows in the AngelOne master."""
    global _default_resolver
    if refresh or _default_resolver is None:
        _default_resolver = InstrumentResolver().load(force_refresh=refresh)
    return _default_resolver.get_lot_size(underlying)


def get_all_lot_sizes(refresh: bool = False) -> dict:
    """All F&O underlying -> lot size mappings from futures rows."""
    global _default_resolver
    if refresh or _default_resolver is None:
        _default_resolver = InstrumentResolver().load(force_refresh=refresh)
    return _default_resolver.get_all_lot_sizes()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    resolver = InstrumentResolver().load()

    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
                "SBIN", "RELIANCE", "TCS"):
        try:
            print(f"get_lot_size({sym!r}) -> {resolver.get_lot_size(sym)}")
        except KeyError as e:
            print(f"get_lot_size({sym!r}) -> MISSING ({e})")

    expiries = resolver.get_available_expiries("NIFTY")
    print(f"NIFTY expiries available: {expiries[:5]}")

    if expiries:
        nearest = expiries[0]
        chain = resolver.get_option_chain_tokens("NIFTY", nearest)
        print(f"NIFTY {nearest}: {len(chain)} contracts")

        atm_call = resolver.get_option_token("NIFTY", nearest, 25000, "CE")
        print("25000 CE ->", atm_call)