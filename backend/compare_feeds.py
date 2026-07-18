"""
compare_feeds.py
=================
Runs your existing smartapi_client.py against the trimmed
nse_bse_fundamentals.py wherever they overlap, and prints deltas.

smartapi_client.py is already a fully independent SmartAPI pipeline — spot
quotes, ATM chain, Greeks, contract discovery — none of it touches NSE/BSE
HTTP. nse_bse_fundamentals.py is what's LEFT after removing everything
smartapi_client.py already covers: breadth (advances/declines) and
fundamentals (P/E, P/B, dividend yield, ffmc) that no broker feed publishes.
So there isn't much left to literally A/B compare — the one place worth
actively checking is the Greeks endpoint, since Angel One's own forum has
an open report of it returning monthly Greeks for a weekly-expiry request.
"""

import smartapi_client as sa
import nse_bse_fundamentals as nse


def check_india_vix():
    """No NSE-side function survives in this refactor to compare VIX
    against — smartapi_client.py's INDEX_TOKENS already resolves VIX
    dynamically from the ScripMaster (AMXIDX row), so this just confirms
    it's actually resolving and returning a sane value."""
    quote = sa.get_index_quote("INDIA VIX")
    if not quote:
        print("VIX: get_index_quote('INDIA VIX') returned nothing — check "
              "INDEX_TOKENS has an 'INDIA VIX' key (depends on the exact "
              "`name` field Angel's ScripMaster uses this run).")
        return
    print(f"VIX (smartapi_client): {quote['ltp']}  "
          f"(open={quote['open']} high={quote['high']} low={quote['low']})")


def check_greeks_weekly_vs_monthly(symbol: str = "NIFTY", exchange: str = "NFO"):
    """
    Targets the known Angel One bug directly: pull Greeks for the nearest
    two expiries and flag strikes where delta+theta come back identical —
    that's the signature of the monthly-Greeks-returned-for-weekly-request
    bug reported on Angel's forum.
    """
    expiries = sa.list_expiries(symbol, exchange=exchange)
    if len(expiries) < 2:
        print(f"[check_greeks] not enough expiries resolved for {symbol} "
              f"on {exchange} — is the ScripMaster loaded?")
        return

    near, next_ = expiries[0], expiries[1]
    greeks_near = sa.get_option_greeks(symbol, near)
    greeks_next = sa.get_option_greeks(symbol, next_)

    if not greeks_near or not greeks_next:
        print(f"[check_greeks] optionGreek call returned empty for "
              f"{symbol} {near} or {next_}")
        return

    total = len(greeks_near)
    suspicious = sum(
        1 for key, row_near in greeks_near.items()
        if (row_next := greeks_next.get(key))
        and row_near["delta"] == row_next["delta"]
        and row_near["theta"] == row_next["theta"]
    )

    verdict = "LIKELY BUG HIT" if total and suspicious > total * 0.5 else "looks distinct, probably fine"
    print(f"Greeks check {symbol}: {near} (weekly?) vs {next_} — "
          f"{suspicious}/{total} strikes have IDENTICAL delta+theta ({verdict})")


def check_atm_chain_with_greeks(symbol: str = "NIFTY", exchange: str = "NFO", strikes: int = 5):
    """Sanity-check the merged LTP+Greeks path end to end — this is the
    shape ws_server_live.py would actually consume."""
    expiries = sa.list_expiries(symbol, exchange=exchange)
    if not expiries:
        print(f"[check_atm_chain] no expiries for {symbol}")
        return
    chain = sa.get_atm_chain_with_greeks(symbol, expiries[0], strikes_around_atm=strikes, exchange=exchange)
    if not chain:
        print(f"[check_atm_chain] get_atm_chain_with_greeks returned nothing for {symbol} {expiries[0]}")
        return

    missing_greeks = sum(1 for r in chain["rows"] if r.get("delta") is None)
    print(f"ATM chain {symbol} {expiries[0]}: spot={chain['spot']} atm={chain['atm_strike']} "
          f"{len(chain['rows'])} rows, {missing_greeks} missing Greeks "
          f"(illiquid far legs Angel hasn't priced)")


def check_ffmc_sample(index_name: str = nse.FNO_STOCK_INDEX, n: int = 5):
    """ffmc (free-float market cap) has no SmartAPI equivalent — this is
    the one genuinely NSE-only data point left after the refactor."""
    df = nse.fetch_ffmc_weights([index_name])
    if df.empty:
        print(f"[check_ffmc] no data for {index_name}")
        return
    print(f"ffmc sample ({index_name}), NSE-only, no SmartAPI equivalent:")
    print(df.head(n).to_string(index=False))


if __name__ == "__main__":
    print("=" * 60)
    check_india_vix()
    print()
    check_greeks_weekly_vs_monthly("NIFTY")
    print()
    check_atm_chain_with_greeks("NIFTY")
    print()
    check_ffmc_sample()
    print("=" * 60)
