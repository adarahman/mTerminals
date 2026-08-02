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
