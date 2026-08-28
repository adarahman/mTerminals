"""Circuit-breaker fallback for market-data providers."""
import logging
import time

logger = logging.getLogger(__name__)


class FallbackMarketData:
    """Wraps a primary MarketData provider with automatic failover to a
    secondary provider — used for MARKET_DATA_FALLBACK_PROVIDER (e.g.
    primary=SmartAPI, fallback=Shoonya when SmartAPI's feed is down).

    Circuit-breaker shaped, not retry-every-call: once the primary is
    considered down, it's marked down for `cooldown_s` and every wrapped
    method routes straight to the fallback until the cooldown expires —
    a single shared flag, not per-method, since a dead SmartAPI session
    fails the same way across list_expiries/get_atm_chain/etc. and
    there's no value in re-discovering that on every individual call.
    Without this, a fully down primary would pay its own failure latency
    (or ScripMaster reload / login-retry cost) on every tick before
    falling back.

    "Down" is judged differently depending on how the primary failed. A
    raised exception trips the breaker immediately — it's an unambiguous
    signal (auth failure, network error, dead session). An empty/falsy
    RESULT does not trip it immediately: that can just mean one symbol
    genuinely had no data this tick while the primary is otherwise fine,
    and tripping a shared 30s-wide breaker on a single blip needlessly
    blacks out every other wrapped method too — worse still if the
    fallback is unhealthy (e.g. Shoonya session dead), turning one
    harmless empty result into a real outage. Empty results only trip the
    breaker after `_EMPTY_TRIP_THRESHOLD` consecutive empties for that
    method; each individual empty call still answers from the fallback
    (so the caller isn't left hanging), it just doesn't lock out the
    primary while doing so.

    DELIBERATELY DOES NOT WRAP get_batch_quotes / get_batch_quotes_by_token.
    Those two are the ones ShoonyaMarketData's and UpstoxMarketData's own
    docstrings flag as a "KNOWN GAP": they return each broker's RAW quote
    row (Shoonya: lp/o/h/l/c/v/oi/nc/pc; Upstox: last_price/net_change/...)
    rather than a normalized shape, because nothing in this codebase's 7
    real call sites needed that translation layer built — until now,
    only IndexQuoteFetcher.provider() calls
    these, and it parses AngelOne's own field names directly. A silent
    failover on these two would hand that caller a differently-shaped
    row with no error — every field read as None, not an exception —
    which is worse than the primary just failing loudly. If a Shoonya (or
    Upstox) quote mapper for that call site gets built later, these two
    can move into the same failover path as everything else below.
    """

    # Methods safe to fail over: list_expiries/get_atm_chain/find_option_token/
    # get_spot_quote/get_fno_underlyings are all normalized to the Protocol's
    # documented shape at each adapter's own boundary (see UpstoxMarketData's
    # and ShoonyaMarketData's _to_iso/_to_shoonya-style conversions above).
    _FAILOVER_METHODS = (
        "list_expiries",
        "get_atm_chain",
        "find_option_token",
        "get_spot_quote",
        "get_futures_quote",
        "get_fno_underlyings",
    )

    # An exception is an unambiguous signal the primary itself is broken
    # (auth failure, network error, dead session) — trip the breaker on the
    # first one. An empty/falsy RESULT is not the same signal: it can also
    # mean "this one symbol genuinely has no data right now" (e.g. a spot
    # quote momentarily missing from a batch) while the primary is
    # otherwise completely healthy. Tripping the shared breaker on a
    # single empty result blacks out every failover-wrapped method for
    # `cooldown_s`, including ones that would have succeeded — and if the
    # fallback happens to be down too (e.g. Shoonya session dead), that
    # turns one harmless blip into a full outage. Require a few
    # consecutive empties (per method) before treating "empty" as "down".
    _EMPTY_TRIP_THRESHOLD = 3

    def __init__(self, primary, fallback, primary_name, fallback_name, cooldown_s=30):
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name
        self._cooldown_s = cooldown_s
        self._primary_down_until = 0.0  # 0 = not down; else a time.monotonic() deadline
        self._consecutive_empty: dict[str, int] = {}

    def _primary_is_down(self):
        return self._primary_down_until and time.monotonic() < self._primary_down_until

    def _mark_primary_down(self, method_name, reason):
        already_down = self._primary_is_down()
        self._primary_down_until = time.monotonic() + self._cooldown_s
        if not already_down:
            logger.warning(
                f"[market_data] {self._primary_name}.{method_name} {reason} — "
                f"routing to {self._fallback_name} for the next {self._cooldown_s}s"
            )

    def _call(self, method_name, *args, **kwargs):
        if not self._primary_is_down():
            try:
                result = getattr(self._primary, method_name)(*args, **kwargs)
            except Exception as exc:
                self._consecutive_empty[method_name] = 0
                self._mark_primary_down(method_name, f"raised ({exc})")
            else:
                if result:
                    self._consecutive_empty[method_name] = 0
                    return result
                streak = self._consecutive_empty.get(method_name, 0) + 1
                self._consecutive_empty[method_name] = streak
                if streak >= self._EMPTY_TRIP_THRESHOLD:
                    self._mark_primary_down(
                        method_name,
                        f"returned empty {streak}x in a row",
                    )
                else:
                    logger.info(
                        f"[market_data] {self._primary_name}.{method_name} returned "
                        f"empty ({streak}/{self._EMPTY_TRIP_THRESHOLD}) — retrying "
                        f"primary directly, not failing over yet"
                    )
                    return getattr(self._fallback, method_name)(*args, **kwargs)
        return getattr(self._fallback, method_name)(*args, **kwargs)

    def list_expiries(self, underlying, exchange="NFO"):
        return self._call("list_expiries", underlying, exchange=exchange)

    def get_atm_chain(
        self, underlying, expiry_ddmmmyyyy, strikes_around_atm=10, exchange="NFO"
    ):
        return self._call(
            "get_atm_chain",
            underlying,
            expiry_ddmmmyyyy,
            strikes_around_atm,
            exchange=exchange,
        )

    def find_option_token(
        self, underlying, expiry_ddmmmyyyy, strike, opt_type, exchange="NFO"
    ):
        return self._call(
            "find_option_token",
            underlying,
            expiry_ddmmmyyyy,
            strike,
            opt_type,
            exchange=exchange,
        )

    def get_spot_quote(self, underlying):
        return self._call("get_spot_quote", underlying)

    def get_futures_quote(self, underlying, which="NEAR"):
        return self._call("get_futures_quote", underlying, which=which)

    def get_fno_underlyings(self, force_refresh=False):
        return self._call("get_fno_underlyings", force_refresh=force_refresh)

    # NOT routed through _call()/failover — see class docstring.
    def get_batch_quotes(self, exchange, symbol_token_pairs, mode="FULL"):
        return self._primary.get_batch_quotes(exchange, symbol_token_pairs, mode=mode)

    def get_batch_quotes_by_token(
        self, exchange, symbol_token_pairs, mode="FULL", **provider_options
    ):
        return self._primary.get_batch_quotes_by_token(
            exchange, symbol_token_pairs, mode=mode, **provider_options
        )

    def index_tokens(self):
        # Pure in-memory lookup dict, not a live call — always the primary's.
        return self._primary.index_tokens()


# ── NSE/BSE Public API provider ──────────────────────────────────────────
