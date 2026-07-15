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
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import requests

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
}

# Index spot tokens are fixed and well-known (NSE_CM / BSE_CM segment) —
# no need to search the master for these.
INDEX_SPOT_TOKENS = {
    "NIFTY":      {"exchange": "NSE", "symboltoken": "99926000", "tradingsymbol": "Nifty 50"},
    "BANKNIFTY":  {"exchange": "NSE", "symboltoken": "99926009", "tradingsymbol": "Nifty Bank"},
    "MIDCPNIFTY": {"exchange": "NSE", "symboltoken": "99926074", "tradingsymbol": "NIFTY MID SELECT"},
    "SENSEX":     {"exchange": "BSE", "symboltoken": "99919000", "tradingsymbol": "SENSEX"},
}


class InstrumentResolver:
    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._instruments: list = []
        # (underlying, expiry_str, strike, option_type) -> instrument dict
        self._option_index: dict = {}
        # underlying -> {expiry_str -> [instrument dicts]}
        self._chain_index: dict = {}

    def _today_cache_path(self) -> Path:
        return self.cache_dir / CACHE_FILE_TEMPLATE.format(date=date.today().isoformat())

    def load(self, force_refresh: bool = False):
        """Loads instrument master from today's local cache if present,
        otherwise downloads fresh from AngelOne and caches it. The master
        changes daily (new expiries roll in), so cache is date-scoped."""
        cache_path = self._today_cache_path()

        if not force_refresh and cache_path.exists():
            logger.info("Loading instrument master from cache: %s", cache_path)
            with open(cache_path, "r") as f:
                self._instruments = json.load(f)
        else:
            logger.info("Downloading instrument master from AngelOne...")
            resp = requests.get(INSTRUMENT_MASTER_URL, timeout=30)
            resp.raise_for_status()
            self._instruments = resp.json()
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
        """
        self._option_index.clear()
        self._chain_index.clear()

        for inst in self._instruments:
            if inst.get("instrumenttype") not in ("OPTIDX", "OPTSTK"):
                continue

            name = inst.get("name", "")
            if name not in UNDERLYING_MAP:
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
            "Indexed %d option contracts across %d underlyings",
            len(self._option_index),
            len(self._chain_index),
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
        Current lot size for `underlying`, read directly off the instrument
        master (every option record already carries its own `lotsize`) —
        no separate hardcoded table to go stale after an NSE revision.

        Picks the nearest available expiry and takes lotsize from the first
        contract found there. Raises KeyError if the underlying has no
        indexed contracts (e.g. load() wasn't called, or the name isn't in
        UNDERLYING_MAP), since silently defaulting to some arbitrary lot
        size would corrupt every OI/GEX/PnL figure derived from it.
        """
        underlying = underlying.upper()
        expiries = self.get_available_expiries(underlying)
        if not expiries:
            raise KeyError(
                f"No contracts indexed for '{underlying}' — call .load() "
                f"first, or check UNDERLYING_MAP / the underlying name."
            )
        nearest_expiry = expiries[0]
        contracts = self.get_option_chain_tokens(underlying, nearest_expiry)
        if not contracts:
            raise KeyError(f"No contracts found for '{underlying}' at {nearest_expiry}.")
        try:
            return int(float(contracts[0]["lotsize"]))
        except (KeyError, TypeError, ValueError) as e:
            raise KeyError(f"Malformed lotsize field for '{underlying}': {e}")

    def build_ws_subscription_list(self, underlying: str, expiry: str) -> list:
        """
        Builds the token list block for AngelOneTicker.subscribe_tokens(),
        e.g. for feeding straight into option_chain_json.py's WS setup.
        Returns: [{"exchangeType": <NFO/BFO int>, "tokens": [...]}]
        """
        contracts = self.get_option_chain_tokens(underlying, expiry)
        exch_seg = UNDERLYING_MAP[underlying.upper()]["exch_seg"]
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
    """Convenience wrapper: resolves lot size via the shared resolver."""
    global _default_resolver
    if refresh or _default_resolver is None:
        _default_resolver = InstrumentResolver().load(force_refresh=refresh)
    return _default_resolver.get_lot_size(underlying)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    resolver = InstrumentResolver().load()

    expiries = resolver.get_available_expiries("NIFTY")
    print(f"NIFTY expiries available: {expiries[:5]}")

    if expiries:
        nearest = expiries[0]
        chain = resolver.get_option_chain_tokens("NIFTY", nearest)
        print(f"NIFTY {nearest}: {len(chain)} contracts")

        atm_call = resolver.get_option_token("NIFTY", nearest, 25000, "CE")
        print("25000 CE ->", atm_call)
