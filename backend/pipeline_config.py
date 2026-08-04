"""Runtime-config contract for the option-chain pipeline (Step 5d of the
v4 migration plan).

option_chain_json.py's per-tick behavior (which symbol/expiry to fetch,
whether to pull extra NEAR/MONTHLY chains, etc.) lives as plain module
globals inside that file — every function in option_chain_json.py reads
them unqualified (SYMBOL, EXPIRY, STRIKES_EACH_SIDE, ...), so this module
does NOT try to relocate that storage (that would mean touching every
read site in option_chain_json.py's main() pipeline, a much larger and
riskier diff for no behavior change).

What this module DOES extract is the *external* contract: previously
ws_server_live.py repointed those globals by poking module attributes
directly (option_chain_json.SYMBOL = ..., option_chain_json.STRIKES_EACH_
SIDE = ...) from two separate call sites. That's replaced by a single
typed RuntimeConfig passed to option_chain_json.set_runtime_config().

NOTE — EXCHANGE is deliberately NOT a field here. The old code path did
poke option_chain_json.EXCHANGE, but option_chain_json.main() always
recomputes EXCHANGE as a LOCAL variable from SYMBOL (BSE for SENSEX/
BANKEX/SENSEX50, else NSE) without a `global EXCHANGE` declaration — so
that module attribute was write-only and had no effect on any pipeline
run, even before this refactor. Dropped here rather than carried forward
as dead config surface.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class RuntimeConfig:
    """All fields default to None, meaning "leave option_chain_json's
    current value alone" — set_runtime_config() only overwrites fields
    that are explicitly passed, matching the previous poke-by-attribute
    behavior where a caller could update just one global without
    disturbing the others."""
    symbol: Optional[str] = None
    expiry: Optional[str] = None
    no_extra_chains: Optional[bool] = None
    strict_expiry: Optional[bool] = None
    no_virtual_oi: Optional[bool] = None
    strikes_each_side: Optional[int] = None
    use_smartapi: Optional[bool] = None
    # "EQ" (default — NSE option-chain response's own underlyingValue,
    # cash-market index quote) or "FUT" (near-month futures LTP, already
    # fetched every tick via fetch_futures_wide for df_fut — see
    # option_chain_json.py's PRICE_SOURCE docstring for why EQ goes stale
    # near the 3:15-3:30 close window and FUT doesn't).
    price_source: Optional[str] = None
    
    price_source: Optional[str] = None
    # Near-month futures expiry to use when price_source="FUT" (or when
    # fetch_futures_wide needs an explicit expiry rather than resolving
    # the nearest one itself). None = leave option_chain_json's current
    # value alone, matching every other field's semantics here.
    futures_expiry: Optional[str] = None
