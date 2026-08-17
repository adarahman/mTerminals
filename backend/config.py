"""
config.py
=========
Centralized runtime configuration -- the single place environment
variables and .env are read from.

Previously, `brokers/smartapi_client.py` computed its own `.env` path
and read SMARTAPI_* variables directly at import time, while
`brokers/smartapi_instruments.py` separately read ANGELONE_CACHE_DIR
with its own default (./.angelone_cache) -- a *second*, disconnected
cache location from the one paths.py already centralizes for
everything else. Each new module that needed a setting was one more
place deciding its own default and its own env var name independently.

Import the module-level `settings` singleton instead of reading
os.environ / os.getenv directly in new code:

    from config import settings
    settings.smartapi_key

All fields are read once at import time (same as the code this
replaces) -- this doesn't add hot-reloading, it just gives every module
the same source of truth instead of each one recomputing its own.
"""

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

from paths import BACKEND_DIR, PROJECT_ROOT, RUNTIME_DIR

logger = logging.getLogger(__name__)

# .env location has been ambiguous across this codebase's own history:
# brokers/smartapi_client.py's dirname(dirname(abspath(__file__))) math
# actually resolved to backend/.env, but its own comment claimed the
# project root (one level up), and README.md instructs "project root"
# too. Rather than guess a single path again, check both, in order, and
# log which one (if either) was actually found -- so a missing/misplaced
# .env shows up immediately in the log instead of surfacing later as an
# opaque AuthenticationError.
_ENV_CANDIDATES = [
    os.path.join(BACKEND_DIR, ".env"),
    os.path.join(PROJECT_ROOT, ".env"),
]

ENV_PATH = next((p for p in _ENV_CANDIDATES if os.path.isfile(p)), None)

if ENV_PATH:
    load_dotenv(ENV_PATH)
    logger.info(f"Loaded .env from {ENV_PATH}")
else:
    logger.warning(
        "No .env file found at either of: "
        + ", ".join(_ENV_CANDIDATES)
        + ". SMARTAPI_* settings will be None unless set some other way "
        "(shell environment, launcher script, etc.)."
    )


@dataclass(frozen=True)
class Settings:
    # -- Live execution broker -------------------------------------------
    # Selects the account/order adapter only. Keeping the default preserves
    # existing installations until Shoonya/Breeze credentials are
    # deliberately configured. One of SMARTAPI, SHOONYA, BREEZE.
    execution_broker: str = field(
        default_factory=lambda: os.getenv("EXECUTION_BROKER", "SMARTAPI").strip().upper()
    )

    # -- Market data provider ----------------------------------------------
    # Independent of execution_broker: you can execute on BREEZE while
    # still streaming ticks from SmartAPI (or vice versa), since brokers/
    # market_data.py's MarketData Protocol is a separate seam from the
    # order-execution one below. Defaults to SMARTAPI (the existing,
    # battle-tested feed) so this stays a no-op until deliberately
    # switched. One of SMARTAPI, BREEZE.
    market_data_provider: str = field(
        default_factory=lambda: os.getenv("MARKET_DATA_PROVIDER", "SMARTAPI").strip().upper()
    )

    # -- AngelOne SmartAPI credentials (brokers/smartapi_client.py) -----
    smartapi_key: Optional[str] = field(
        default_factory=lambda: os.getenv("SMARTAPI_KEY")
    )
    smartapi_client_code: Optional[str] = field(
        default_factory=lambda: os.getenv("SMARTAPI_CLIENT_CODE")
    )
    smartapi_pin: Optional[str] = field(
        default_factory=lambda: os.getenv("SMARTAPI_PIN")
    )
    smartapi_totp_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("SMARTAPI_TOTP_SECRET")
    )

    # -- Shoonya / Finvasia credentials (brokers/shoonya_client.py) ------
    shoonya_user_id: Optional[str] = field(
        default_factory=lambda: os.getenv("SHOONYA_USER_ID")
    )
    shoonya_password: Optional[str] = field(
        default_factory=lambda: os.getenv("SHOONYA_PASSWORD")
    )
    shoonya_totp_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("SHOONYA_TOTP_SECRET")
    )
    shoonya_vendor_code: Optional[str] = field(
        default_factory=lambda: os.getenv("SHOONYA_VENDOR_CODE")
    )
    shoonya_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("SHOONYA_API_SECRET")
    )
    shoonya_imei: str = field(
        default_factory=lambda: os.getenv("SHOONYA_IMEI", "mTerminals")
    )
    shoonya_product_type: str = field(
        default_factory=lambda: os.getenv("SHOONYA_PRODUCT_TYPE", "M").strip().upper()
    )

    # -- ICICI Breeze credentials (brokers/breeze_client.py,
    # brokers/breeze_market_data.py) ---------------------------------------
    # Unlike SmartAPI/Shoonya, Breeze has no TOTP-based auto-login path:
    # api_session is a short-lived token (expires daily) obtained by
    # visiting https://api.icicidirect.com/apiuser/login?api_key=<key> in a
    # browser and copying the `apisession` value out of the redirect URL by
    # hand. There is no way to script this step away — BREEZE_API_SESSION
    # must be refreshed once a day before market open, or every call using
    # brokers/breeze_client.py's session will fail with an auth error.
    breeze_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("BREEZE_API_KEY")
    )
    breeze_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("BREEZE_API_SECRET")
    )
    breeze_api_session: Optional[str] = field(
        default_factory=lambda: os.getenv("BREEZE_API_SESSION")
    )
    # Product code Breeze orders are placed with for F&O: "options" or
    # "futures". Cash-market ("cash") isn't handled here — this dashboard
    # only ever resolves option/future contracts.
    breeze_product_type: str = field(
        default_factory=lambda: os.getenv("BREEZE_PRODUCT_TYPE", "options").strip().lower()
    )

    # -- Tunables ---------------------------------------------------------
    quote_cache_ttl_s: float = field(
        default_factory=lambda: float(os.getenv("SMARTAPI_QUOTE_TTL_S", "1.5"))
    )

    # -- Instrument-master cache dir (brokers/smartapi_instruments.py) --
    # Previously defaulted to ./.angelone_cache: a second cache location,
    # disconnected from paths.RUNTIME_DIR (where every other cache/log
    # lives) and resolved relative to the process's cwd rather than a
    # fixed project location. Now defaults inside the same runtime/ tree;
    # ANGELONE_CACHE_DIR still overrides it for anyone relying on the old
    # path.
    instrument_cache_dir: str = field(
        default_factory=lambda: os.getenv(
            "ANGELONE_CACHE_DIR", os.path.join(RUNTIME_DIR, "instrument_cache")
        )
    )


settings = Settings()
