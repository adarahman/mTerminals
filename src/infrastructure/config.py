"""
infrastructure/config.py
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

    from infrastructure.config import settings
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

from infrastructure.paths import BACKEND_DIR, PROJECT_ROOT, RUNTIME_DIR

try:  # ws_server_live adds backend/ to sys.path; package tests may not.
    from brokers.provider_registry import EXECUTION_PROVIDER_KEYS
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    try:
        from backend.brokers.provider_registry import EXECUTION_PROVIDER_KEYS
    except ModuleNotFoundError:
        # Keep config.py portable for deployment preflight and isolated tests
        # that intentionally copy only configuration dependencies.
        EXECUTION_PROVIDER_KEYS = frozenset(
            {"SMARTAPI", "UPSTOX", "KITE", "SHOONYA", "BREEZE"}
        )

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool) -> bool:
    """Read a strict, human-editable boolean environment setting."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

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
    # Project settings must win over inherited shell vars so a stale daily
    # token or old broker selection in the parent process does not silently
    # override the checked-in .env defaults for the current checkout.
    load_dotenv(ENV_PATH, override=True)
    logger.info(f"Loaded .env from {ENV_PATH} (override=True)")
else:
    logger.warning(
        "No .env file found at either of: "
        + ", ".join(_ENV_CANDIDATES)
        + ". SMARTAPI_* settings will be None unless set some other way "
        "(shell environment, launcher script, etc.)."
    )


@dataclass(frozen=True)
class Settings:
    # -- Broker service mode ----------------------------------------------
    # The single switch for a deliberately public-data-only deployment.
    # It belongs in configuration rather than server CLI flags so launch
    # behavior is reproducible and not tied to a particular broker name.
    broker_services_enabled: bool = field(
        default_factory=lambda: _env_bool("BROKER_SERVICES_ENABLED", True)
    )

    # -- Live execution broker -------------------------------------------
    # Market data remains on SmartAPI for now; this selector controls the
    # account/order adapter only. Keeping the default preserves existing
    # installations until Shoonya credentials are deliberately configured.
    execution_broker: str = field(
        default_factory=lambda: os.getenv("EXECUTION_BROKER", "SMARTAPI").strip().upper()
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

    # -- Upstox credentials (brokers/upstox_client.py) -------------------
    # Upstox's access_token has no unattended refresh path (see
    # upstox_client.py's module docstring — OAuth2 authorization-code
    # only, token expires 3:30 AM IST the next day). upstox_access_token
    # is read here purely so the rest of the app has one place to look;
    # it still has to be re-pasted daily the same way it always did when
    # upstox_client.py read UPSTOX_ACCESS_TOKEN directly. upstox_client.py
    # itself intentionally stays config.py-independent (see its
    # docstring) so it keeps working as a standalone smoke-testable
    # module — these fields exist for the adapters that wrap it
    # (brokers/upstox_execution_adapter.py, market_data.py's
    # UpstoxMarketData) to hand a token in explicitly rather than relying
    # on upstox_client's own os.getenv() defaults.
    upstox_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("UPSTOX_API_KEY")
    )
    upstox_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("UPSTOX_API_SECRET")
    )
    upstox_redirect_uri: Optional[str] = field(
        default_factory=lambda: os.getenv("UPSTOX_REDIRECT_URI")
    )
    upstox_access_token: Optional[str] = field(
        default_factory=lambda: os.getenv("UPSTOX_ACCESS_TOKEN")
    )

    # -- Kite Connect / Zerodha credentials (brokers/kite_client.py) ----
    # Same token-lifecycle shape as Upstox above: kite_access_token has no
    # unattended refresh path (kite_client.py's module docstring — OAuth2
    # browser-redirect + request_token exchange only, no TOTP login), so
    # it has to be re-pasted daily the same way it always did when
    # kite_client.py read KITE_ACCESS_TOKEN directly. kite_client.py
    # itself intentionally stays config.py-independent (same reasoning as
    # upstox_client.py) so it keeps working as a standalone
    # smoke-testable module — these fields exist for
    # brokers/kite_execution_adapter.py to hand a token in explicitly
    # rather than relying on kite_client's own os.getenv() defaults.
    kite_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("KITE_API_KEY")
    )
    kite_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("KITE_API_SECRET")
    )
    kite_access_token: Optional[str] = field(
        default_factory=lambda: os.getenv("KITE_ACCESS_TOKEN")
    )

    # -- ICICI Breeze credentials (brokers/breeze_client.py) --------------
    # Breeze's API session has no TOTP/automated refresh path — it expires
    # DAILY and must be re-pasted from the ICICI redirect URL into
    # BREEZE_API_SESSION (see .env's BREEZE section). breeze_api_session
    # drives both the market-data provider's availability (provider_status
    # reports SESSION_REQUIRED when empty) and the client session itself.
    breeze_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("BREEZE_API_KEY")
    )
    breeze_api_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("BREEZE_API_SECRET")
    )
    breeze_api_session: Optional[str] = field(
        default_factory=lambda: os.getenv("BREEZE_API_SESSION") or None
    )
    breeze_product_type: str = field(
        default_factory=lambda: os.getenv("BREEZE_PRODUCT_TYPE", "options").strip().lower()
    )

    # -- Kotak Neo credentials (brokers/kotak_client.py) ------------------
    # Kotak's NEO Trade API v2 has no long-lived paste-in token (unlike
    # Upstox/Kite): it uses a two-step TOTP+MPIN login. These fields let
    # brokers/kotak_client.py auto-login the same way shoonya_client.py
    # does — KOTAK_TOTP_SECRET is the base32 secret from the user's Kotak
    # authenticator registration (pyotp generates the rotating TOTP from
    # it), KOTAK_MPIN is the MPIN for the second step. KOTAK_CONSUMER_KEY
    # is the trade-API token from the Kotak Neo app/web's "trade api card".
    kotak_consumer_key: Optional[str] = field(
        default_factory=lambda: os.getenv("KOTAK_CONSUMER_KEY")
    )
    kotak_mobile: Optional[str] = field(
        default_factory=lambda: os.getenv("KOTAK_MOBILE")
    )
    kotak_ucc: Optional[str] = field(
        default_factory=lambda: os.getenv("KOTAK_UCC")
    )
    kotak_totp_secret: Optional[str] = field(
        default_factory=lambda: os.getenv("KOTAK_TOTP_SECRET")
    )
    kotak_mpin: Optional[str] = field(
        default_factory=lambda: os.getenv("KOTAK_MPIN")
    )

    # -- Market-data provider selector -------------------------------------
    # Independent of execution_broker: which feed backs brokers/market_data.py's
    # `market_data` singleton (list_expiries/get_atm_chain/find_option_token/
    # index quotes/etc). Defaults to SMARTAPI to preserve existing behavior.
    # NOTE before flipping this to UPSTOX: server/app.py's
    # IndexQuoteFetcher.provider() calls market_data.get_batch_quotes_by_token()
    # and feeds the raw row straight into _map_smartapi_quote(), which parses
    # AngelOne's own field names (ltp, netChange, ...). UpstoxMarketData's
    # get_batch_quotes_by_token() returns Upstox-shaped rows instead, so that
    # one call site needs an Upstox-aware mapper before this switch is safe to
    # flip in production — see the note on UpstoxMarketData in market_data.py.
    market_data_provider: str = field(
        default_factory=lambda: os.getenv("MARKET_DATA_PROVIDER", "SMARTAPI").strip().upper()
    )

    # Optional automatic failover provider for market_data.py's singleton.
    # Independent of market_data_provider (the PRIMARY) — when set, and
    # different from the primary, market_data.py wraps the primary in a
    # FallbackMarketData that switches to this provider only while the
    # primary is actually failing (raising or returning empty), with a
    # cooldown before it tries the primary again. Empty/unset (the
    # default) preserves the old single-provider behavior exactly.
    # NOTE: get_batch_quotes/get_batch_quotes_by_token never fail over,
    # regardless of this setting — see FallbackMarketData's docstring in
    # market_data.py for why (raw provider-specific quote field shapes).
    market_data_fallback_provider: Optional[str] = field(
        default_factory=lambda: (os.getenv("MARKET_DATA_FALLBACK_PROVIDER") or "").strip().upper() or None
    )

    # -- Live tick-streaming feed provider ---------------------------------
    # Selects which broker's WebSocket manager overlays fast leg-level ticks
    # onto the slower snapshot-polled chain.
    #
    # Deliberately NOT independently defaulted from execution_broker
    # (order routing) or market_data_provider (REST chain-building
    # polling) anymore: in practice all three are almost always the same
    # broker, and a fourth env var that silently defaults to SMARTAPI
    # regardless of what EXECUTION_BROKER says is exactly the kind of
    # drift that leaves the tick feed on one broker while orders/quotes
    # run on another with nothing in .env explaining why. So the default
    # here rides on EXECUTION_BROKER, and LIVE_FEED_PROVIDER only needs to
    # be set explicitly for the one legitimate case where the tick feed
    # should differ from the order broker (e.g. Shoonya ticks overlaid on
    # Upstox-routed orders).
    #
    # Set to UPSTOX only once UPSTOX_ACCESS_TOKEN is populated (see
    # upstox_client.py's docstring — no unattended daily refresh) AND
    # `pip install upstox-python-sdk` has been run (see
    # upstox_ws_client.py's module docstring for why that's a separate,
    # optional dependency rather than a hard one). Set to SHOONYA once the
    # SHOONYA_* settings below are populated — no extra pip install
    # needed: shoonya_ws_client.py's websocket path ships inside the same
    # ShoonyaApi-py checkout brokers/shoonya_client.py already depends on
    # (see setup_shoonya.sh).
    live_feed_provider: str = field(
        default_factory=lambda: os.getenv(
            "LIVE_FEED_PROVIDER",
            os.getenv("EXECUTION_BROKER", "SMARTAPI"),
        ).strip().upper()
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

    # -- Tunables ---------------------------------------------------------
    quote_cache_ttl_s: float = field(
        default_factory=lambda: float(os.getenv("SMARTAPI_QUOTE_TTL_S", "5.0"))
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

# ── Execution-broker validation ──────────────────────────────────────────
# Keep this registry deliberately separate from the market-data registry:
# KOTAK and NSE/BSE can provide snapshots, but neither has an execution path
# wired into server/app.py.  Validating the actual order-routing surface
# at startup prevents a configuration typo (or a data-only provider) from
# failing much later, after the dashboard has already booted.
EXECUTION_BROKERS = EXECUTION_PROVIDER_KEYS

if settings.execution_broker not in EXECUTION_BROKERS:
    raise ValueError(
        f"EXECUTION_BROKER={settings.execution_broker!r} is invalid. "
        "Choose a configured execution broker: "
        + ", ".join(sorted(EXECUTION_BROKERS))
        + ". NSE_BSE and KOTAK are market-data-only in this build."
    )
