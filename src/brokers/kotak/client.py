"""Kotak Neo market-data session — TOTP-secret auto-login.

Canonical module: :mod:`brokers.kotak.client`.

Kotak's NEO Trade API v2 (neo_api_client) has no long-lived paste-in
access token like Upstox/Kite. It uses a two-step 2FA login:

  1. totp_login(mobile_number, ucc, totp)  -> view token + sid
  2. totp_validate(mpin)                   -> edit token + edit sid + base url

The SDK stores these on its NeoAPI client object, and subsequent calls
(search_scrip / scrip_master / quotes) read them from there — there is
nothing to persist across processes. So this module mirrors the
Shoonya session pattern (brokers/shoonya_client.py): a lazily-created,
locked, TOTP-generated login that re-authenticates on first use and
re-uses the resulting NeoAPI client for the lifetime of the process.

The TOTP in step 1 is generated from the base32 secret the user
registered with Kotak's authenticator (KOTAK_TOTP_SECRET), exactly the
same way shoonya_client.py generates Shoonya's two_fa code. The MPIN
(KOTAK_MPIN) is the second factor for step 2.

Rate limiting: the Kotak API returns 429 on the quotes endpoint when
throttled. The wrapper below is deliberately thin — it does NOT retry
or back off on 429 (that belongs in the market-data adapter, which
knows whether a partial result is safe to serve). It only guarantees a
single authenticated session and a consistent exception type.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from core.errors import BrokerError

try:
    from infrastructure.config import settings
except ModuleNotFoundError:  # pragma: no cover - depends on launch style
    from infrastructure.config import settings

logger = logging.getLogger(__name__)

# How long a failed login is cached before the next attempt is allowed.
# Mirrors shoonya_client's cooldown reasoning: without it, every call
# site that touches `_session.client` would re-run the full TOTP login
# handshake (fresh TOTP + totp_login + totp_validate POSTs) on every
# poll tick when login is down, hammering Kotak's login endpoint.
_LOGIN_RETRY_COOLDOWN_SEC = 30.0

def _default_api_factory():
    """Build a fresh Kotak NeoAPI client. The SDK is a normal PyPI
    package (neo_api_client), so this is a plain import — unlike
    Shoonya's source-checkout SDK path."""
    try:
        from neo_api_client import NeoAPI
    except ImportError as exc:
        raise BrokerError(
            "Kotak SDK (neo_api_client) is not installed. Run "
            "`pip install neo_api_client` (or the project requirements)."
        ) from exc
    return NeoAPI(
        environment="prod",
        access_token=None,
        neo_fin_key=None,
        consumer_key=settings.kotak_consumer_key or None,
    )


def _iso_expiry(expiry_ddmmmyyyy: str) -> str:
    """'28-Aug-2025' (this codebase's option_chain_json expiry format) ->
    Kotak's 'DDMMMYYYY' scrip-master search format ('28AUG2025'). Kotak's
    search_scrip parses with %d%b%Y, which is case-insensitive on the month
    abbreviation, but we uppercase for consistency with SmartAPI convention."""
    dt = datetime.strptime(expiry_ddmmmyyyy, "%d-%b-%Y")
    return dt.strftime("%d%b%Y").upper()


class KotakSession:
    def __init__(self, api_factory=None):
        self._api_factory = api_factory or _default_api_factory
        self._client = None
        self._lock = threading.RLock()
        self._last_login_attempt = 0.0  # time.monotonic() of the last attempt
        self._last_login_error: BrokerError | None = None

    @property
    def client(self):
        self.ensure_session()
        return self._client

    def ensure_session(self):
        with self._lock:
            if self._client is not None:
                return self._client

            now = time.monotonic()
            if (
                self._last_login_error is not None
                and (now - self._last_login_attempt) < _LOGIN_RETRY_COOLDOWN_SEC
            ):
                # A recent attempt already failed — re-raise the cached
                # error instead of re-running the login handshake (fresh
                # TOTP code + totp_login + totp_validate) on every single
                # caller. Callers see the same BrokerError they would have
                # gotten from a real attempt; they just don't each trigger
                # one.
                raise self._last_login_error

            self._last_login_attempt = now
            try:
                required = {
                    "KOTAK_CONSUMER_KEY": settings.kotak_consumer_key,
                    "KOTAK_MOBILE": settings.kotak_mobile,
                    "KOTAK_UCC": settings.kotak_ucc,
                    "KOTAK_TOTP_SECRET": settings.kotak_totp_secret,
                    "KOTAK_MPIN": settings.kotak_mpin,
                }
                missing = [name for name, value in required.items() if not value]
                if missing:
                    raise BrokerError("Missing Kotak settings: " + ", ".join(missing))
                client = self._api_factory()
                try:
                    import pyotp

                    totp = pyotp.TOTP(settings.kotak_totp_secret).now()
                    result = client.totp_login(
                        mobile_number=settings.kotak_mobile,
                        ucc=settings.kotak_ucc,
                        totp=totp,
                    )
                    status = (
                        result.get("data") or result
                    ).get("status") if isinstance(result, dict) else None
                    if status not in (None, "success") and "data" not in result:
                        raise BrokerError(
                            f"Kotak totp_login rejected: {result.get('message', result)}"
                        )
                    client.totp_validate(mpin=settings.kotak_mpin)
                except BrokerError:
                    raise
                except Exception as exc:
                    raise BrokerError(f"Kotak login failed: {exc}") from exc
            except BrokerError as err:
                self._last_login_error = err
                logger.warning(
                    "[kotak_client] login attempt failed, will not retry for "
                    f"{_LOGIN_RETRY_COOLDOWN_SEC}s: {err}"
                )
                raise

            self._client = client
            self._last_login_error = None
            logger.info("[kotak_client] Logged in, session established")
            return client


_session = KotakSession()


def healthcheck() -> tuple[bool, str | None]:
    """Test whether a usable Kotak session exists.

    Same contract as shoonya_client.healthcheck(): (True, None) on success,
    (False, error_message) on failure. Registered in
    brokers.connection._CHECKS so check_connection("KOTAK") reflects real
    session state instead of the previous always-ready default.
    """
    try:
        _session.ensure_session()
        return True, None
    except BrokerError as exc:
        return False, str(exc)
