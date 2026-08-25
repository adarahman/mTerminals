"""Process-wide market selection owned by the application layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime

from server.broker_services import MD_PROVIDER_KEYS, md_provider_has_credentials
from infrastructure.config import settings as _broker_settings
from nse_eod_fetch import is_trading_day
from server import runtime_state


MARKET_OPEN_TIME = dtime(9, 15)
MARKET_CLOSE_TIME = dtime(15, 30)


def build_market_selection(
    symbol: str,
    expiry: str | None,
    data_source: str,
    price_source: str = "AUTO",
    futures_expiry: str = "NEAR",
) -> "MarketSelectionState":
    """Construct the process-wide selection; the instance lives in runtime_state."""
    return MarketSelectionState(
        symbol=symbol,
        expiry=expiry,
        data_source=data_source,
        price_source=price_source,
        futures_expiry=futures_expiry,
    )


def _resolve_default_data_source() -> str:
    """Startup default for the runtime DATA SOURCE dropdown.

    Prefers the configured MARKET_DATA_PROVIDER when usable (registered AND
    credentialed); otherwise the first credentialed BROKER source, so a
    stale/empty token in .env doesn't silently strand the dashboard on the
    public NSE/BSE API. NSE/BSE is the default only when NO broker has
    credentials (fresh install, or BROKER_SERVICES_ENABLED=false forces it
    explicitly regardless)."""
    configured = _broker_settings.market_data_provider
    if configured in MD_PROVIDER_KEYS and md_provider_has_credentials(configured):
        return configured
    for candidate in MD_PROVIDER_KEYS:
        if candidate == "NSE_BSE":
            continue
        if md_provider_has_credentials(candidate):
            return candidate
    return "NSE_BSE"


def _market_session_status(now) -> str:
    """Best-effort NSE session label for the UI. Uses the same yearly
    holiday calendar as is_trading_day(); ad-hoc exchange closures still
    require that calendar/source to be updated."""
    if now.weekday() < 5 and not is_trading_day(now):
        return "HOLIDAY"
    if not is_trading_day(now):
        return "MARKET_CLOSED"
    if MARKET_OPEN_TIME <= now.time() <= MARKET_CLOSE_TIME:
        return "OPEN"
    return "MARKET_CLOSED"


@dataclass
class MarketSelectionState:
    symbol: str
    expiry: str | None
    data_source: str
    price_source: str = "AUTO"
    futures_expiry: str = "NEAR"

    def select_symbol(self, symbol: str, expiry: str | None) -> None:
        self.symbol = symbol
        self.expiry = expiry

    def select_data_source(self, data_source: str) -> None:
        self.data_source = data_source

    def select_price_source(self, price_source: str) -> None:
        self.price_source = price_source

    def select_futures_expiry(self, futures_expiry: str) -> None:
        self.futures_expiry = futures_expiry

    def snapshot(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry,
            "data_source": self.data_source,
            "price_source": self.price_source,
            "futures_expiry": self.futures_expiry,
        }
