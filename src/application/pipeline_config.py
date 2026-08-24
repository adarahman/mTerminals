"""Runtime-config contract for one option-chain analytics pass.

The application builds a complete ``RuntimeConfig`` for every pass and
hands it directly to the analytics entry point. Legacy helper functions are
being migrated from their historical module globals to this explicit input.

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


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Selection and behavior inputs for a single analytics execution."""
    symbol: Optional[str] = None
    expiry: Optional[str] = None
    no_extra_chains: Optional[bool] = None
    strict_expiry: Optional[bool] = None
    no_virtual_oi: Optional[bool] = None
    strikes_each_side: Optional[int] = None
    use_smartapi: Optional[bool] = None
    # "AUTO" (default — use the live cash/index quote when the option-chain
    # underlying is stale, otherwise EQ; near close, fall back to futures),
    # "EQ" (force the option-chain underlyingValue), or "FUT" (force the
    # near-month futures LTP, already
    # fetched every tick via fetch_futures_wide for df_fut — see
    # option_chain_json.py's PRICE_SOURCE docstring for why EQ goes stale
    # near the 3:15-3:30 close window and FUT doesn't).
    price_source: Optional[str] = None
    # Near-month futures expiry to use when price_source="FUT" (or when
    # fetch_futures_wide needs an explicit expiry rather than resolving
    # the nearest one itself). None = leave option_chain_json's current
    # value alone, matching every other field's semantics here.
    futures_expiry: Optional[str] = None

    def __post_init__(self) -> None:
        if self.symbol is not None:
            symbol = self.symbol.strip().upper()
            if not symbol:
                raise ValueError("symbol cannot be empty")
            object.__setattr__(self, "symbol", symbol)
        if self.expiry is not None:
            expiry = self.expiry.strip()
            if not expiry:
                raise ValueError("expiry cannot be empty")
            object.__setattr__(self, "expiry", expiry)
        if self.strikes_each_side is not None:
            if (
                isinstance(self.strikes_each_side, bool)
                or self.strikes_each_side <= 0
            ):
                raise ValueError("strikes_each_side must be a positive integer")
        if self.price_source is not None:
            price_source = self.price_source.strip().upper()
            if price_source not in {"AUTO", "EQ", "FUT"}:
                raise ValueError("price_source must be AUTO, EQ, or FUT")
            object.__setattr__(self, "price_source", price_source)
        if self.futures_expiry is not None:
            futures_expiry = self.futures_expiry.strip().upper()
            if futures_expiry not in {"NEAR", "NEXT", "FAR"}:
                raise ValueError(
                    "futures_expiry must be NEAR, NEXT, or FAR"
                )
            object.__setattr__(self, "futures_expiry", futures_expiry)
