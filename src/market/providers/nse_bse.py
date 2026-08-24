"""Public NSE/BSE market-data provider."""

from datetime import datetime
from typing import Optional

_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}


def resolve_exchange_for_symbol(symbol: str) -> str:
    """Return the public exchange serving an underlying symbol."""
    return "BSE" if symbol.strip().upper() in _BSE_SYMBOLS else "NSE"

def _public_futures_quote(underlying: str, which: str = "NEAR") -> Optional[dict]:
    """Shared NSE/BSE public-API futures fallback.

    Used directly by providers with no broker-native FUTIDX resolution in
    this codebase (Shoonya/Kite/Breeze/NSE_BSE), and as an explicit
    fallback by providers whose own resolution can come back empty
    (Kotak — see KotakMarketData.get_futures_quote). Always stamps
    FutSource="NSE_BSE" so this is never a silent EQ/FUT provider split —
    see MarketData.get_futures_quote's docstring."""
    from market.providers.nse_bse_client import fetch_public_futures

    frame = fetch_public_futures(underlying, which=which)
    if frame is None or frame.empty:
        return None
    row = frame.iloc[0].to_dict()
    row["FutSource"] = "NSE_BSE"
    return row



def _normalize_expiry_dash(expiry) -> Optional[str]:
    """'31JUL2026' | '31-Jul-2026' | '2026-07-31' -> '31-Jul-2026'
    (market_api.py's native option-chain expiry format)."""
    if not expiry:
        return expiry
    for fmt in ("%d-%b-%Y", "%d%b%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(expiry), fmt).strftime("%d-%b-%Y")
        except ValueError:
            continue
    raise ValueError(f"Unsupported expiry format: {expiry!r}")



def _atm_from_rows(rows, spot) -> float:
    if not rows or not spot:
        return 0.0
    strikes = sorted({float(r.get("strike")) for r in rows if r.get("strike") is not None})
    if not strikes:
        return 0.0
    return float(min(strikes, key=lambda s: abs(s - spot)))



def _safe_float(val) -> Optional[float]:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
    
# ---------------------------------------------------------------------------

class NseBseMarketData:
    """Market-data provider backed by the PUBLIC NSE/BSE REST endpoints in
    market_api.py — zero broker credentials required.

    Exchange routing is symbol-driven (see resolve_exchange_for_symbol):
    NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY -> NSE option-chain-v3 + allIndices;
    SENSEX/BANKEX/SENSEX50 -> BSE JSON options + BSE index quote.

    Snapshot/polling only: there is no WebSocket client for the public
    NSE/BSE APIs, so PROVIDER_CAPABILITIES marks this provider
    snapshot=True / websocket=False / execution=False and the dashboard
    labels its status POLLING — the expected mode for this provider, not
    an error condition.

    NEVER an execution broker: NSE/BSE is a read-only market-data source.
    config.py rejects EXCHANGE_BROKER=NSE_BSE at startup so it can't be
    wired into order routing.

    Failures RAISE (rather than returning None) so that when this provider
    is wrapped by FallbackMarketData — either as primary or as
    MARKET_DATA_FALLBACK_PROVIDER — the circuit breaker can route to the
    other provider instead of the caller silently receiving partial data.
    """

    def list_expiries(self, underlying, exchange="NFO"):
        underlying = underlying.upper()
        if underlying in _BSE_SYMBOLS:
            # BSE expiry calendar is computed (no public BSE expiry-list
            # endpoint) — same helper the BSE chain path already uses.
            from market.expiry.service import _generate_bse_expiry_series

            return _generate_bse_expiry_series(underlying)
        # NSE: public daily instrument master (cached, no broker login needed).
        from brokers.smartapi.instruments import get_available_expiries

        return get_available_expiries(underlying)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        del strikes_around_atm  # public API returns the whole expiry; caller filters
        from market.providers.nse_bse_client import (
            BSE_INDEX_SCRIP_CODES,
            fetch_bse_json_options,
            fetch_option_chain,
            parse_option_chain_response,
        )

        underlying = underlying.upper()
        expiry_dash = _normalize_expiry_dash(expiry_ddmmmyyyy)
        if underlying in _BSE_SYMBOLS:
            scrip_cd = BSE_INDEX_SCRIP_CODES.get(underlying)
            if not scrip_cd:
                raise RuntimeError(f"No public BSE derivative code for {underlying}")
            df, spot = fetch_bse_json_options(
                expiry_dash.replace("-", " "), scrip_cd=scrip_cd
            )
            if df is None or df.empty:
                raise RuntimeError(
                    f"Public BSE option chain empty for {underlying} {expiry_dash}"
                )
            df = df.rename(columns={"Strike": "StrikePrice"})
            spot = float(df["Spot"].iloc[0]) if "Spot" in df.columns else 0.0
        else:
            payload = fetch_option_chain(underlying, expiry_dash)
            df = parse_option_chain_response(payload, expiry_dash)
            spot = float(df["Spot"].iloc[0]) if "Spot" in df.columns else 0.0

        if df.empty:
            raise RuntimeError(f"Public {underlying} chain empty for {expiry_dash}")

        rows = []
        for rec in df.to_dict("records"):
            strike = rec.get("StrikePrice")
            if strike is None:
                continue
            for side in ("CE", "PE"):
                rows.append(
                    {
                        "strike": float(strike),
                        "type": side,
                        "tradingsymbol": None,
                        "token": None,
                        "ltp": _safe_float(rec.get(f"{side}_LTP")),
                        "open": None,
                        "high": None,
                        "low": None,
                        "close": None,
                        "oi": _safe_float(rec.get(f"{side}_OI")),
                        "volume": _safe_float(rec.get(f"{side}_Volume")),
                        "net_change": rec.get(f"{side}_Change"),
                        "pct_change": rec.get(f"{side}_pChange"),
                    }
                )
        rows.sort(key=lambda r: (r["strike"], r["type"]))
        return {
            "underlying": underlying,
            "spot": spot,
            "atm_strike": _atm_from_rows(rows, spot),
            "expiry": expiry_ddmmmyyyy,
            "rows": rows,
        }

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        # The public APIs have no token/tradingsymbol concept — callers that
        # need one must use a broker provider. Returns None (unresolved).
        return None

    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        # No batch-quote endpoint on the public APIs; the pipeline reads the
        # full chain via get_atm_chain()/option_chain_json instead.
        return {}

    def get_batch_quotes_by_token(self, exchange, symbol_token_pairs, mode="FULL"):
        return {}

    def get_spot_quote(self, underlying):
        from market.providers.nse_bse_client import (
            INDEX_RENAME,
            fetch_all_indices_snapshot,
            fetch_bse_index_quote,
            get_index_from_snapshot,
        )

        underlying = underlying.upper()
        if underlying in _BSE_SYMBOLS:
            entry = fetch_bse_index_quote(underlying)
            if not entry:
                return None
            return {
                "ltp": _safe_float(entry.get("Last Price")),
                "open": None,
                "high": None,
                "low": None,
                "close": _safe_float(entry.get("Prev Close")),
            }
        snapshot = fetch_all_indices_snapshot()
        if snapshot.empty:
            raise RuntimeError(f"NSE allIndices snapshot returned no data")
        inverse = {v: k for k, v in INDEX_RENAME.items()}
        display = inverse.get(underlying, underlying)
        row = get_index_from_snapshot(snapshot, display) or get_index_from_snapshot(
            snapshot, underlying
        )
        if not row:
            raise RuntimeError(f"No allIndices row for {underlying}")
        return {
            "ltp": _safe_float(row.get("last")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("previousClose")),
        }

    def get_futures_quote(self, underlying, which="NEAR"):
        # This provider IS the NSE/BSE public API, so FutSource="NSE_BSE"
        # here reflects the actual source rather than flagging a fallback.
        return _public_futures_quote(underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        # Public ScripMaster universe — no broker login required.
        from brokers.smartapi.instruments import (
            get_fno_underlyings as _public_fno_underlyings,
        )

        return _public_fno_underlyings(refresh=force_refresh)

    def index_tokens(self):
        # No token model on the public APIs — index quotes go through
        # get_spot_quote()/fetch_all_indices_snapshot() instead.
        return {}
