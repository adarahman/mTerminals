"""Lightweight JSON options pipeline (refactored).

Behavior-preserving refactor of the former import-time-heavy module:

- CLI arguments are parsed ONLY when run as a script (__main__), never at
  import. Hosts that `import option_chain_json` get host-friendly defaults
  and repoint them via set_runtime_config(); the "hide sys.argv around the
  import" workaround in ws_server_live.py is no longer required (harmless
  if left in place).
- main() is decomposed into stage helpers (_gather_market_data,
  _merge_volume_value, _build_extra_chains, ...) — each independently
  readable/testable; control flow and ordering inside each stage match the
  previous monolith exactly.
- Every historical public name is preserved on this module's namespace:
  set_runtime_config, main, _fetch_and_parse, _resolve_expiry,
  _build_expiry_bundle, _fetch_all_indices_cached, the runtime globals
  (SYMBOL/EXPIRY/EXCHANGE/STRIKES_EACH_SIDE/USE_SMARTAPI/PRICE_SOURCE/
  FUTURES_EXPIRY/...), and the expiry_manager re-exports
  (BSE_EXPIRY_DEFAULT/_nearest_Thursday/_nearest_Tuesday/
  _generate_bse_expiry_series) that ws_server_live reads THROUGH this
  module.

Deliberate non-changes: _fetch_and_parse keeps its exact signature and
keeps reading the USE_SMARTAPI/STRIKES_EACH_SIDE module globals (they're
mutated in place by set_runtime_config between passes, same as before).
"""

import argparse
import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime

import pandas as pd

from config import settings as _pipeline_settings  # noqa: F401  (historical import surface)
from engine import build_engine_result
from expiry_manager import (
    BSE_EXPIRY_DEFAULT,
    _generate_bse_expiry_series,
    _nearest_Thursday,
    _nearest_Tuesday,
    make_expiry_manager,
)
from index_contributors import SYMBOL_TO_INDEX_BASKET, _compute_index_contributors
from lot_sizes import LOT_SIZES

# Authenticated broker adapters are imported lazily only when USE_SMARTAPI
# is true. The separate unauthenticated daily instrument master remains
# available through the broker instrument helpers for lot sizes/reference
# metadata without initializing a login session.
from market_api import (
    BSE_INDEX_SCRIP_CODES,
    fetch_all_indices,
    fetch_bse_index_quote,
    fetch_bse_json_options,
    fetch_option_chain,
    fetch_public_futures,
    get_unified_market_data,
    parse_option_chain_response,
)
from oi.oi_analysis import (
    append_json_history,
    build_oi_history,
    compute_dte,
    read_last_json_snapshot,
)
from pipeline_config import RuntimeConfig
from storage.caches import TTLSlot

logger = logging.getLogger(__name__)

# Expiry-date generation helpers live in expiry_manager.py (Step 5a of the
# v4 migration plan); lot-size resolution in lot_sizes.py (5b); index
# contributors in index_contributors.py (5c). They are re-exported above —
# ws_server_live reads BSE_EXPIRY_DEFAULT/_nearest_* THROUGH this module.

_BSE_SYMBOLS = {"SENSEX", "BANKEX", "SENSEX50"}


def _exchange_for_symbol(symbol: str) -> str:
    return "BSE" if symbol in _BSE_SYMBOLS else "NSE"


# ─── Runtime configuration (module globals, host-mutable) ────────────
# Initialized to host-friendly constants; the standalone CLI overwrites
# them in _apply_cli_overrides() (see __main__). NOTHING here touches
# sys.argv at import time — that was the old behavior and forced every
# importing host to hide argv mid-import.
#
# NOTE: SmartAPI is enabled by default unless BROKER_SERVICES_ENABLED=false.
# MARKET_DATA_PROVIDER selects the REST quote adapter, not whether the
# authenticated SmartAPI chain pipeline is on — keeping Upstox/Shoonya
# websocket provider selection independent from the SmartAPI REST chain path.
try:
    from config import settings as _broker_settings
except ImportError:  # pragma: no cover - standalone legacy invocation
    _broker_settings = None
USE_SMARTAPI = (
    _broker_settings.broker_services_enabled
    if _broker_settings is not None
    else True
)

EXCHANGE = "NSE"
SYMBOL = "NIFTY"
EXPIRY = _nearest_Tuesday()  # NSE weekly default; CLI/host overrides below
LOOP_INTERVAL = 0
NO_EXTRA_CHAINS = False
STRICT_EXPIRY = False
NO_VIRTUAL_OI = False

# How many strikes each side of ATM the engine computes Greeks/OI-velocity/
# signal analytics for. Both call sites below read this module-level name at
# call time (not a baked-in literal) or the set_runtime_config() override
# from a long-lived host would be a no-op — which was the original bug:
# strikes stayed pinned at the standalone value in public-only mode, where
# 50 was intended.
STRIKES_EACH_SIDE = 15

# Underlying price source fed into df["Spot"] (and downstream into every
# engine.py bs_* Greeks call, wall selection, PCR, etc.):
#   "AUTO" (default) — keep the option-chain EQ spot while it is healthy,
#         but replace it with the active broker's live cash/index quote when
#         EQ is stale. If no live cash quote is available, use the selected
#         futures LTP near/after the close window. This prevents a stale NSE
#         underlyingValue from freezing every downstream analytic.
#   "EQ" — force the option-chain response's underlyingValue.
#   "FUT" — force the selected near-month futures LTP. Futures are not
#         basis-adjusted back toward EQ.
# Runtime-configurable via set_runtime_config()/pipeline_config.
PRICE_SOURCE = "AUTO"

# Which monthly futures contract PRICE_SOURCE="FUT" resolves to — "NEAR"
# (current month, default), "NEXT", or "FAR". See fetch_futures_wide()'s
# docstring (broker_pipeline.py): this used to silently reuse the options
# chain's own (often weekly) expiry as the futures filter, matching only on
# the monthly week and returning empty futures every other week. Manual
# only, via set_runtime_config().
FUTURES_EXPIRY = "NEAR"


def set_runtime_config(cfg: RuntimeConfig) -> None:
    """External hook for a long-lived host (ws_server_live.py) to repoint
    this module's per-tick runtime config between pipeline calls. Replaces
    poking module attributes directly by name from multiple call sites.

    Only overwrites fields explicitly set (non-None) on `cfg` — a caller can
    update e.g. just strikes_each_side without disturbing SYMBOL/EXPIRY/etc.

    Standalone runs never call this — they use the CLI-derived values from
    _apply_cli_overrides() and never touch it again.

    NOTE: there is no `exchange` field — main() always recomputes EXCHANGE
    locally from SYMBOL (see pipeline_config.py's module docstring).
    """
    global SYMBOL, EXPIRY, NO_EXTRA_CHAINS, STRICT_EXPIRY, NO_VIRTUAL_OI
    global STRIKES_EACH_SIDE, USE_SMARTAPI, PRICE_SOURCE, FUTURES_EXPIRY
    if cfg.symbol is not None:
        SYMBOL = cfg.symbol
    if cfg.expiry is not None:
        EXPIRY = cfg.expiry
    if cfg.no_extra_chains is not None:
        NO_EXTRA_CHAINS = cfg.no_extra_chains
    if cfg.strict_expiry is not None:
        STRICT_EXPIRY = cfg.strict_expiry
    if cfg.no_virtual_oi is not None:
        NO_VIRTUAL_OI = cfg.no_virtual_oi
    if cfg.strikes_each_side is not None:
        STRIKES_EACH_SIDE = cfg.strikes_each_side
    if cfg.use_smartapi is not None:
        USE_SMARTAPI = cfg.use_smartapi
    if cfg.price_source is not None:
        src = cfg.price_source.strip().upper()
        if src not in ("AUTO", "EQ", "FUT"):
            raise ValueError(
                f"price_source must be 'AUTO', 'EQ', or 'FUT', got {cfg.price_source!r}"
            )
        PRICE_SOURCE = src
    if cfg.futures_expiry is not None:
        fexp = cfg.futures_expiry.strip().upper()
        if fexp not in ("NEAR", "NEXT", "FAR"):
            raise ValueError(
                f"futures_expiry must be 'NEAR', 'NEXT', or 'FAR', "
                f"got {cfg.futures_expiry!r}"
            )
        FUTURES_EXPIRY = fexp


# ─── df_idx TTL cache ────────────────────────────────────────────────
# fetch_all_indices() is the one NSE HTTP call with no SmartAPI equivalent
# — it feeds _compute_index_contributors()'s ffmc weighting AND the
# Volume/Value merge into all_indices. Its consumers don't need per-tick
# freshness (free-float weights and session volume totals barely move
# second to second), so the call is decoupled onto its own TTL, cutting
# real NSE HTTP volume without touching anything downstream (same
# DataFrame, refreshed less often).
DF_IDX_TTL_SECONDS = 20
_DF_IDX_CACHE = TTLSlot(ttl_seconds=DF_IDX_TTL_SECONDS, clock="epoch")
_DF_IDX_REFRESH_LOCK = threading.Lock()
_DF_IDX_REFRESHING = False


def _refresh_df_idx_background():
    """Runs off the tick's critical path — see _fetch_all_indices_cached."""
    global _DF_IDX_REFRESHING
    try:
        _DF_IDX_CACHE.set(fetch_all_indices())
    except Exception as e:
        logger.error(f"[_refresh_df_idx_background] fetch_all_indices failed: {e}")
    finally:
        with _DF_IDX_REFRESH_LOCK:
            _DF_IDX_REFRESHING = False


def _fetch_all_indices_cached():
    """Stale-while-revalidate: on TTL expiry, kick off the 6-way NSE refresh
    in a background thread and return the last known value immediately,
    instead of blocking this tick's pipeline on it. Only the very first
    call (cold start, nothing cached yet) blocks."""
    global _DF_IDX_REFRESHING

    if _DF_IDX_CACHE.value is None:
        _DF_IDX_CACHE.set(fetch_all_indices())
        return _DF_IDX_CACHE.value

    if not _DF_IDX_CACHE.is_fresh():
        with _DF_IDX_REFRESH_LOCK:
            already_refreshing = _DF_IDX_REFRESHING
            if not already_refreshing:
                _DF_IDX_REFRESHING = True
        if not already_refreshing:
            threading.Thread(
                target=_refresh_df_idx_background,
                daemon=True,
                name="df_idx_refresh",
            ).start()

    return _DF_IDX_CACHE.value


# =====================================================================
# FETCH, PARSE & STRUCTURING
# =====================================================================


def _fetch_bse_chain_no_smartapi(symbol, expiry_dash):
    """market_api.fetch_bse_json_options() normalised to match
    fetch_option_chain_wide()'s column schema. PctChgOI/pChange/BuyQty/
    SellQty aren't available from this BSE endpoint at all, so they're
    zeroed rather than guessed; anything downstream reading those fields
    for SENSEX/BANKEX in public-only mode sees 0, not fabricated data."""
    scrip_cd = BSE_INDEX_SCRIP_CODES.get(symbol.upper())
    if not scrip_cd:
        logger.error("No public BSE derivative code for %s", symbol)
        return pd.DataFrame()
    expiry_bse = expiry_dash.replace("-", " ")  # "02-Jul-2026" -> "02 Jul 2026"
    df, spot = fetch_bse_json_options(expiry_bse, scrip_cd=scrip_cd)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={"Strike": "StrikePrice"})
    df["Expiry"] = expiry_dash
    df["Spot"] = spot
    df["Symbol"] = symbol
    for side in ("CE", "PE"):
        for col in (
            f"{side}_PctChgOI",
            f"{side}_pChange",
            f"{side}_BuyQty",
            f"{side}_SellQty",
        ):
            if col not in df.columns:
                df[col] = 0
    return df


def _canon_symbol(symbol):
    """Map a full-company-name symbol to the exchange ticker once, so every
    downstream consumer uses one consistent key: the chain DataFrame's Symbol
    column, _day_open_oi()/NSE-anchor keys, LOT_SIZES lookups and
    build_engine_result()'s own symbol filter must all agree or OI /
    ChgOI / lot-size scaling silently diverge. Idempotent for tickers.
    No-op in public-only mode."""
    raw = (symbol or "").strip().upper()
    if not USE_SMARTAPI or not raw:
        return raw
    try:
        from broker_pipeline import _canon_underlying

        return _canon_underlying(raw)
    except Exception:
        return raw


def _fetch_and_parse(symbol, expiry, exchange, strict_expiry=False):
    symbol = _canon_symbol(symbol)
    if USE_SMARTAPI:
        from broker_pipeline import (
            fetch_option_chain_wide,
            get_available_expiries,
        )
    if exchange == "BSE":
        # fetch_option_chain_wide() is exchange-parametrized
        # (STRIKE_INTERVALS/_get_strike_interval already cover SENSEX/
        # BANKEX/SENSEX50), so exchange="BFO" is a genuine drop-in for the
        # hand-built BSE scrape: same output columns, produced natively.
        if USE_SMARTAPI:
            df = fetch_option_chain_wide(symbol, expiry, exchange="BFO")
            if df.empty:
                raise RuntimeError(
                    f"SmartAPI BFO chain fetch empty for {symbol} {expiry}"
                )
        else:
            # NSE-native path never covers BSE — market_api.py's own BSE
            # scrape, normalised to the same column schema so downstream
            # doesn't need to know which source it came from.
            df = _fetch_bse_chain_no_smartapi(symbol, expiry)
            if df.empty:
                raise RuntimeError(
                    f"NSE-fallback BFO chain fetch empty for {symbol} {expiry}"
                )
        spot = df["Spot"].iloc[0] if "Spot" in df.columns else 0.0
        # A BSE option-chain response can contain valid strikes while its
        # embedded underlying field is blank/stale. Recover the current BSE
        # index quote and stamp it in rather than discarding a usable chain.
        try:
            valid_spot = pd.notna(spot) and float(spot) > 0
        except (TypeError, ValueError):
            valid_spot = False
        if not valid_spot:
            quote = fetch_bse_index_quote(symbol)
            recovered = quote.get("Last Price") if quote else None
            try:
                recovered = float(recovered)
            except (TypeError, ValueError):
                recovered = 0.0
            if recovered > 0:
                df = df.copy()
                df["Spot"] = recovered
                spot = recovered
                logger.warning(
                    "[BSE] recovered missing chain spot for %s from index quote: %s",
                    symbol,
                    recovered,
                )
        expiry_dates = _generate_bse_expiry_series(symbol)
        return df, spot, expiry_dates
    else:
        # When using NSE API (public-only mode), get expiries from NSE itself
        # instead of ScripMaster to avoid format mismatches.
        if USE_SMARTAPI:
            from brokers.market_data import get_active_provider

            provider = get_active_provider()

            if provider in ("UPSTOX", "SHOONYA", "KITE", "BREEZE", "KOTAK"):
                from brokers.market_data import market_data

                try:
                    provider_expiries = market_data.list_expiries(
                        symbol,
                        exchange=("BFO" if exchange == "BSE" else "NFO"),
                    )
                    expiry_dates = [
                        pd.to_datetime(e, format="%d%b%Y").strftime("%d-%b-%Y")
                        for e in provider_expiries
                    ]
                except Exception as exc:
                    logger.warning(
                        "[Expiry] %s expiry lookup failed for %s: %s",
                        provider,
                        symbol,
                        exc,
                    )
                    expiry_dates = []

                # Provider authentication/search failure must not kill the
                # option-chain pipeline. ScripMaster is already loaded.
                if not expiry_dates:
                    logger.warning(
                        "[Expiry] %s returned no expiries for %s; "
                        "falling back to ScripMaster",
                        provider,
                        symbol,
                    )
                    expiry_dates = get_available_expiries(symbol)
            else:
                expiry_dates = get_available_expiries(symbol)
            resolved = expiry

            # Compare by parsed calendar date, not raw string equality — the
            # frontend's expiry string and get_available_expiries()'s strings
            # can be the same date in different case/format, and a raw `in`
            # check silently treated that as "not found", falling through to
            # future[0] every time. Swap in the matched CANONICAL string so
            # downstream exact-string lookups get one they recognize.
            def _expiry_date(s):
                try:
                    return pd.to_datetime(s, format="%d-%b-%Y").date()
                except (ValueError, TypeError):
                    return None

            target_date = _expiry_date(resolved)
            matched = next(
                (
                    e
                    for e in expiry_dates
                    if target_date is not None and _expiry_date(e) == target_date
                ),
                None,
            )
            if matched is not None:
                resolved = matched
            else:
                if strict_expiry:
                    raise RuntimeError(
                        f"requested expiry {expiry!r} not available for "
                        f"{symbol!r} (offered: {expiry_dates})"
                    )
                today = date.today()
                future = [
                    e
                    for e in expiry_dates
                    if pd.to_datetime(e, format="%d-%b-%Y").date() >= today
                ]
                if not future:
                    raise RuntimeError(
                        f"no future expiries available for {symbol!r} "
                        f"(offered: {expiry_dates})"
                    )
                resolved = future[0]
                logger.info(f"[Expiry] '{expiry}' unavailable → selected: '{resolved}'")
            df = fetch_option_chain_wide(
                symbol, resolved, strikes_around_atm=STRIKES_EACH_SIDE
            )
        else:
            payload = fetch_option_chain(symbol, expiry)
            if not payload:
                raise RuntimeError(f"NSE API returned no data for {symbol}")
            expiry_dates = payload.get("records", {}).get("expiryDates", [])
            resolved = _resolve_expiry(payload, expiry, strict=strict_expiry)
            # Re-fetch with the resolved expiry to ensure consistency.
            if resolved != expiry:
                payload = fetch_option_chain(symbol, resolved)
            df = parse_option_chain_response(payload, resolved)

        if df.empty:
            raise RuntimeError(
                f"{'SmartAPI' if USE_SMARTAPI else 'NSE'} chain fetch empty for {symbol} {resolved}"
            )
        spot = df["Spot"].iloc[0] if "Spot" in df.columns else 0.0
        return df, spot, resolved, expiry_dates


def _resolve_expiry(data, requested_expiry, strict=False):
    available = data["records"].get("expiryDates", [])
    if requested_expiry and data["records"].get("data", []):
        return requested_expiry
    if strict:
        raise RuntimeError(
            f"Requested expiry '{requested_expiry}' has no data. Available: {available}"
        )
    today = date.today()
    for exp in available:
        try:
            if pd.to_datetime(exp, format="%d-%b-%Y").date() >= today:
                logger.info(f"[Expiry] '{requested_expiry}' empty → selected: '{exp}'")
                return exp
        except Exception:
            continue
    raise RuntimeError(f"No valid future expiry found: {available}")


def _build_expiry_bundle(
    symbol, expiry, exchange="NSE", strict_expiry=False, **engine_kwargs
):
    symbol = _canon_symbol(symbol)
    if exchange == "BSE":
        df, spot, _ = _fetch_and_parse(symbol, expiry, exchange, strict_expiry)
        resolved = expiry
    else:
        df, spot, resolved, _ = _fetch_and_parse(
            symbol, expiry, exchange, strict_expiry
        )

    df_clean = (
        df.dropna(subset=["StrikePrice"])
        .drop_duplicates(subset=["StrikePrice"])
        .sort_values("StrikePrice")
        .copy()
    )
    dte = compute_dte(resolved)

    engine_kwargs.pop(
        "velocity_window_minutes", None
    )  # dead param; discarded so it can't leak through **engine_kwargs below
    engine_result = build_engine_result(
        df=df,
        df_clean=df_clean,
        df_idx=None,
        df_fut=None,
        df_full_history=None,
        symbol=symbol,
        expiry=resolved,
        dte=dte,
        lot_size=engine_kwargs.pop("lot_size", LOT_SIZES.get(symbol, 65)),
        n_strikes_each_side=engine_kwargs.pop("n_strikes_each_side", STRIKES_EACH_SIDE),
        **engine_kwargs,
    )
    return df_clean, engine_result.master, engine_result.to_ctx_dict(), dte, resolved


# =====================================================================
# PIPELINE STAGES
# =====================================================================


def _select_runtime_spot(df, spot, df_fut, all_indices):
    """Choose the price actually fed into analytics.

    AUTO is intentionally broker-neutral: when a broker cash/index quote is
    available, it is the best freshness check against NSE's option-chain
    underlyingValue. A material mismatch means the NSE field is stale. If no
    live cash quote exists, AUTO falls back to futures during the final cash
    session window. EQ/FUT remain explicit force modes.
    """
    source = PRICE_SOURCE.strip().upper()
    eq = float(spot or 0.0)

    def _live_index_quote(symbol):
        for row in all_indices or []:
            if str(row.get("Symbol", "")).strip().upper() != symbol.upper():
                continue
            for key in ("Last Price", "ltp", "LTP", "last_price"):
                try:
                    value = float(row.get(key))
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    return value
        return 0.0

    def _futures_ltp(frame):
        if frame is None or getattr(frame, "empty", True):
            return 0.0
        row = frame.iloc[0]
        for key in ("LTP", "ltp", "Last Price", "last_price", "lastPrice"):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 0.0

    live_cash = _live_index_quote(SYMBOL) if USE_SMARTAPI else 0.0
    fut_ltp = _futures_ltp(df_fut)
    selected = eq
    used = "EQ"

    if source == "FUT":
        if fut_ltp > 0:
            selected, used = fut_ltp, "FUT"
    elif source == "AUTO":
        # A broker cash/index quote is a direct freshness reference. 0.05%
        # is deliberately wider than ordinary quote noise but far smaller
        # than the stale-vs-live moves seen when NSE's field stops updating.
        if live_cash > 0 and (eq <= 0 or abs(live_cash - eq) / max(eq, 1.0) > 0.0005):
            selected, used = live_cash, "LIVE_EQ"
        elif live_cash > 0 and eq <= 0:
            selected, used = live_cash, "LIVE_EQ"
        elif datetime.now().time() >= dtime(15, 15) and fut_ltp > 0:
            selected, used = fut_ltp, "FUT"

    if selected <= 0:
        raise RuntimeError(f"No usable spot price for {SYMBOL}: EQ={eq}, FUT={fut_ltp}")

    if used != "EQ":
        df = df.copy()
        df["Spot"] = selected
        logger.warning(
            "[price-source] %s -> %s for %s (EQ=%s, live=%s, FUT=%s)",
            source, used, SYMBOL, eq, live_cash or None, fut_ltp or None,
        )
    return df, selected, used


def _gather_market_data(exchange):
    """Fan out one pass's independent fetches concurrently and assemble the
    market-context pieces. These NSE/BSE calls are independent of each other
    (futures/indices/VIX/ticker-pills don't need the option-chain result);
    running them serially was pure waiting and the single biggest
    contributor to per-tick latency.

    Returns a dict with keys: df, spot, resolved, expiry_dates, df_fut,
    df_idx, india_vix, india_vix_chg_pct, all_indices."""
    if USE_SMARTAPI:
        from broker_pipeline import (
            fetch_all_pills_and_vix_batched,
            fetch_futures_wide,
            fetch_sensex_ticker,
            fetch_ticker_payload,
            fetch_vix,
        )

    with ThreadPoolExecutor(max_workers=5) as ex:
        if exchange == "BSE":
            fut_chain = ex.submit(
                _fetch_and_parse, SYMBOL, EXPIRY, "BSE", STRICT_EXPIRY
            )
            # fetch_futures_wide() resolves FUTIDX contracts generically off
            # the ScripMaster; `which` (FUTURES_EXPIRY) picks the monthly
            # contract — reusing the options chain's own expiry here was the
            # old empty-futures-every-other-week bug.
            fut_fut = (
                ex.submit(
                    fetch_futures_wide,
                    SYMBOL,
                    None,
                    exchange="BFO",
                    which=FUTURES_EXPIRY,
                )
                if USE_SMARTAPI
                else ex.submit(fetch_public_futures, SYMBOL, FUTURES_EXPIRY)
            )
        else:
            fut_chain = ex.submit(
                _fetch_and_parse, SYMBOL, EXPIRY, "NSE", STRICT_EXPIRY
            )
            fut_fut = (
                ex.submit(fetch_futures_wide, SYMBOL, None, which=FUTURES_EXPIRY)
                if USE_SMARTAPI
                else ex.submit(fetch_public_futures, SYMBOL, FUTURES_EXPIRY)
            )
        # TTL-cached (see _fetch_all_indices_cached) — submitted each tick,
        # but only actually hits NSE once per DF_IDX_TTL_SECONDS.
        fut_idx = ex.submit(_fetch_all_indices_cached)
        # Batched replacement for 6 separate ltpData calls (~6s/tick) with
        # 2 getMarketData calls (~0.35s each); overlaps with the fetches
        # above, .result() only gates the wrapper calls below.
        fut_batch = (
            ex.submit(fetch_all_pills_and_vix_batched) if USE_SMARTAPI else None
        )
        if fut_batch is not None:
            fut_batch.result()
        fut_ticker = ex.submit(fetch_ticker_payload) if USE_SMARTAPI else None
        fut_unified = ex.submit(fetch_vix) if USE_SMARTAPI else None
        fut_sensex = ex.submit(fetch_sensex_ticker) if USE_SMARTAPI else None
        fut_public_bse_quotes = (
            {
                sym: ex.submit(fetch_bse_index_quote, sym)
                for sym in BSE_INDEX_SCRIP_CODES
            }
            if not USE_SMARTAPI
            else {}
        )

        if exchange == "BSE":
            df, spot, expiry_dates = fut_chain.result()
            resolved = EXPIRY
        else:
            df, spot, resolved, expiry_dates = fut_chain.result()

        # Broker-neutral market_data adapters may return a quote dict;
        # the analytics engine expects a DataFrame.
        df_fut = fut_fut.result()
        if isinstance(df_fut, dict):
            df_fut = pd.DataFrame([df_fut])
        elif df_fut is None:
            df_fut = pd.DataFrame()
        df_idx = fut_idx.result()

        if USE_SMARTAPI:
            _live_vix, _live_vix_chg_pct = fut_unified.result()
            sensex_quote = fut_sensex.result()
            ticker_payload = fut_ticker.result()
        else:
            # Strict broker-free path: derive NSE ticker pills from the
            # already-fetched index frame; VIX from NSE; SENSEX from BSE's
            # public endpoint without any broker login.
            _live_vix, _live_vix_chg_pct, ticker_payload = get_unified_market_data(
                df_idx
            )
            bse_quotes = [
                future.result() for future in fut_public_bse_quotes.values()
            ]
            bse_quotes = [quote for quote in bse_quotes if quote]
            sensex_quote = next(
                (quote for quote in bse_quotes if quote.get("Symbol") == "SENSEX"),
                None,
            )

        _live_vix = _live_vix or 0.0
        all_indices = list(ticker_payload)
        if sensex_quote:
            all_indices.append(sensex_quote)
        if not USE_SMARTAPI:
            all_indices.extend(
                quote for quote in bse_quotes if quote.get("Symbol") != "SENSEX"
            )

        _merge_volume_value(all_indices, df_idx)
        df, spot, price_source_used = _select_runtime_spot(
            df, spot, df_fut, all_indices
        )

    return {
        "df": df,
        "spot": spot,
        "price_source_used": price_source_used,
        "resolved": resolved,
        "expiry_dates": expiry_dates,
        "df_fut": df_fut,
        "df_idx": df_idx,
        "india_vix": _live_vix,
        "india_vix_chg_pct": _live_vix_chg_pct,
        "all_indices": all_indices,
    }


def _merge_volume_value(all_indices, df_idx):
    """Merge real Volume/Value from df_idx (already fetched — no new network
    call). get_unified_market_data()'s /api/allIndices source reports
    Volume/Value hardcoded to 0 on index rows (an index isn't itself traded),
    but df_idx comes from equity-stock-indices, which includes each index's
    own aggregate row with real session-cumulative totals (the numbers NSE's
    live-market page shows). Matched on Symbol — already the same
    INDEX_RENAME'd string on both sides. Frontend: dashboard.js's price
    chart reads Value/Volume off allIndices to compute a running VWAP."""
    if df_idx is not None and not df_idx.empty and "Symbol" in df_idx.columns:
        vol_map = (
            df_idx.dropna(subset=["Volume"])
            .drop_duplicates(subset=["Symbol"], keep="first")
            .set_index("Symbol")[["Volume", "Value"]]
            .to_dict("index")
        )
        for entry in all_indices:
            row = vol_map.get(entry.get("Symbol"))
            if row:
                entry["Volume"] = row["Volume"]
                entry["Value"] = row["Value"]


def _make_expiry_manager_or_none(expiry_dates):
    """Build the ExpiryManager unconditionally (pure computation off the
    already-fetched expiry_dates, no network cost). Previously it was only
    built inside the extra-chains block, so its verified NEAR/MONTHLY dates
    never reached build_engine_result() — calendar-spread legs fell back to
    placeholder labels and the frontend guessed dates from array position,
    which can land on a stale/wrong entry depending on raw order."""
    em = None
    if expiry_dates:
        try:
            em = make_expiry_manager(expiry_dates)
        except Exception as e:
            logger.warning(f"[ExpiryManager] Context skip ({e})")
    return em


def _build_extra_chains(em):
    """NEAR and MONTHLY extra-expiry bundles, built concurrently (they're
    independent of each other). Empty dict when disabled or nothing pending."""
    extra_chains = {}
    if em is None or NO_EXTRA_CHAINS:
        return extra_chains
    try:
        slots = [
            (slot_name, slot)
            for slot_name, slot in [
                ("NEAR", em.context.near),
                ("MONTHLY", em.context.monthly),
            ]
            if slot and slot.date_str != str(EXPIRY)
        ]
        if slots:
            with ThreadPoolExecutor(max_workers=len(slots)) as ex2:
                futs = {
                    ex2.submit(
                        _build_expiry_bundle, SYMBOL, slot.date_str, _exchange_for_symbol(SYMBOL)
                    ): (slot_name, slot)
                    for slot_name, slot in slots
                }
                for f in as_completed(futs):
                    slot_name, slot = futs[f]
                    try:
                        n_df, n_master, n_ctx, n_dte, _ = f.result()
                        extra_chains[slot.date_str] = (n_df, n_master, n_ctx, n_dte)
                    except Exception as e:
                        logger.warning(f"[{slot_name}] Skip extra bundle ({e})")
    except Exception as e:
        logger.warning(f"[ExtraChains] Skip ({e})")
    return extra_chains


def _calendar_spread_expiries(em):
    """Calendar spread convention: sell the active expiry (front week/month
    already being traded), buy the next MONTHLY expiry — both real, verified,
    future-filtered dates from ExpiryManager. Falls back to "" (→ engine's
    "NEAR"/"FAR" text placeholders) only if em wasn't available at all."""
    if em is None:
        return "", ""
    far = (
        em.context.monthly.date_str
        if em.context.monthly
        else em.context.far.date_str
        if em.context.far
        else ""
    )
    return em.context.current.date_str, far


def _patch_bse_spot_change(ctx_dict, all_indices):
    """SENSEX never appears in df_idx (fetch_all_indices()/DEFAULT_INDICES
    is NSE-only), so engine.py's spot_change/spot_chg_pct lookup falls back
    to 0 when SENSEX is the ACTIVE symbol. Patch from the BSE quote already
    fetched in the fan-out instead of touching engine.py's NSE lookup."""
    if SYMBOL not in _BSE_SYMBOLS:
        return
    quote = next((q for q in all_indices if q.get("Symbol") == SYMBOL), None)
    if not quote:
        return
    if quote.get("Change") is not None:
        ctx_dict["spot_change"] = quote["Change"]
    if quote.get("% Change") is not None:
        ctx_dict["spot_chg_pct"] = quote["% Change"]


# =====================================================================
# PIPELINE EXECUTION
# =====================================================================


def main():
    global EXPIRY
    exchange = _exchange_for_symbol(SYMBOL)

    try:
        md = _gather_market_data(exchange)
        df, spot = md["df"], md["spot"]

        if exchange != "BSE" and md["resolved"] != EXPIRY:
            EXPIRY = md["resolved"]

        if spot == 0 or spot is None:
            logger.error("Error: Invalid Spot Price. Core calculations aborted.")
            return

        # NOTE (2026-07-04): the per-tick joblib.load() of a VirtualOI
        # coordinator that happened here every poll was dead code — its
        # result was never passed anywhere. The real coordinator lives in
        # mTerminals_json.py as a module-level _VOI_COORDINATOR, loaded
        # once per process; --no-virtual-oi flows through export_dashboard_json().

        df_idx = md["df_idx"]
        all_indices = md["all_indices"]

        # Derived from df_idx (already fetched, no new network call) —
        # empty list for symbols with no matching NSE index basket (BSE).
        contributors = _compute_index_contributors(df_idx, SYMBOL, spot)

        dte = compute_dte(EXPIRY)
        df_clean = (
            df.dropna(subset=["StrikePrice"])
            .drop_duplicates(subset=["StrikePrice"])
            .sort_values("StrikePrice")
            .copy()
        )

        em = _make_expiry_manager_or_none(md["expiry_dates"])
        extra_chains = _build_extra_chains(em)

        # Fallback to local JSON snap logs for historical OI analysis.
        prev_json_poll = read_last_json_snapshot(SYMBOL)
        history_df = build_oi_history(df_clean, SYMBOL, prev_poll=prev_json_poll)
        append_json_history(history_df)

        _near_expiry_str, _far_expiry_str = _calendar_spread_expiries(em)

        engine_result = build_engine_result(
            df=df,
            df_clean=df_clean,
            df_idx=df_idx,
            df_fut=md["df_fut"],
            df_full_history=history_df,
            symbol=SYMBOL,
            expiry=EXPIRY,
            dte=dte,
            lot_size=LOT_SIZES.get(SYMBOL, 65),
            n_strikes_each_side=STRIKES_EACH_SIDE,
            india_vix=md["india_vix"],
            india_vix_chg_pct=md["india_vix_chg_pct"],
            near_expiry=_near_expiry_str,
            far_expiry=_far_expiry_str,
        )

        from mTerminals_json import export_dashboard_json

        ctx_dict = engine_result.to_ctx_dict()
        _patch_bse_spot_change(ctx_dict, all_indices)

        export_dashboard_json(
            df_clean=df_clean,
            master=engine_result.master,
            ctx_dict=ctx_dict,
            SYMBOL=SYMBOL,
            EXPIRY=EXPIRY,
            dte=dte,
            engine_result=engine_result,
            out_path="mTerminals.json",
            expiry_dates=md["expiry_dates"],
            extra_chains=extra_chains if extra_chains else None,
            use_virtual_oi=not NO_VIRTUAL_OI,
            contributors=contributors,
            all_indices=all_indices,
            price_source=md["price_source_used"],
            futures_expiry=FUTURES_EXPIRY,
        )
        print()
        logger.info("SUCCESS: JSON Framework updated snapshot successfully.")

    except Exception:
        traceback.print_exc()


# =====================================================================
# STANDALONE CLI — argv is parsed HERE and nowhere else
# =====================================================================


def _build_arg_parser(default_expiry=None):
    parser = argparse.ArgumentParser(prog="option_chain_json", add_help=True)
    parser.add_argument("--exchange", default="NSE", choices=["NSE", "BSE"])
    parser.add_argument("--symbol", default="NIFTY")
    parser.add_argument("--interval", default=0, type=int)
    parser.add_argument(
        "--no-extra-chains",
        action="store_true",
        help="Disable multi-expiry chains for faster performance",
    )
    parser.add_argument(
        "--strict-expiry",
        action="store_true",
        help="Don't auto-resolve to different expiry if requested expiry has no data",
    )
    parser.add_argument(
        "--no-virtual-oi",
        action="store_true",
        help="Disable VirtualOI model inference for faster performance",
    )
    if default_expiry is not None:
        # Second parse stage: --expiry's default depends on --symbol's
        # value (BSE indices expire Thursdays, NSE Tuesdays), mirroring
        # the original two-pass parse_known_args() behavior exactly.
        parser.add_argument("--expiry", default=default_expiry, help="Expiry DD-Mmm-YYYY")
    return parser


def _log_init_banner():
    logger.info("=== LIGHTWEIGHT JSON OPTIONS PIPELINE INITIALIZATION ===")
    logger.info(f"    Exchange: {EXCHANGE} | Symbol: {SYMBOL} | Expiry: {EXPIRY}")
    logger.info(
        f"    Loop    : {'every ' + str(LOOP_INTERVAL) + ' min' if LOOP_INTERVAL > 0 else 'single run'}\n"
    )


def _apply_cli_overrides(argv=None):
    """Parse CLI args and overwrite the module runtime globals. Called only
    from the __main__ block — importing this module NEVER touches argv,
    so hosting processes no longer need to hide their own arguments during
    import."""
    pre, _ = _build_arg_parser().parse_known_args(argv)
    sym = (pre.symbol or "NIFTY").strip().upper()
    default_expiry = (
        BSE_EXPIRY_DEFAULT.get(sym, _nearest_Thursday)()
        if sym in _BSE_SYMBOLS
        else _nearest_Tuesday()
    )
    args, _unknown = _build_arg_parser(default_expiry).parse_known_args(argv)

    global EXCHANGE, SYMBOL, EXPIRY, LOOP_INTERVAL
    global NO_EXTRA_CHAINS, STRICT_EXPIRY, NO_VIRTUAL_OI
    EXCHANGE = args.exchange.strip().upper()
    SYMBOL = args.symbol.strip().upper()
    EXPIRY = args.expiry.strip()
    LOOP_INTERVAL = args.interval
    NO_EXTRA_CHAINS = args.no_extra_chains
    STRICT_EXPIRY = args.strict_expiry
    NO_VIRTUAL_OI = args.no_virtual_oi
    _log_init_banner()


if __name__ == "__main__":
    _apply_cli_overrides()
    if LOOP_INTERVAL > 0:
        logger.info(
            f"[Loop] Active monitoring interval: {LOOP_INTERVAL} min. Use Ctrl+C to terminate.\n"
        )
        while True:
            main()
            time.sleep(LOOP_INTERVAL * 60)
    else:
        main()