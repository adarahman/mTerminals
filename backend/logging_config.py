"""
logging_config.py
==================
One place to turn on logging for the whole backend, instead of each
entry point (or nothing at all) deciding its own format/level. Modules
that log should still just do the standard:

    import logging
    logger = logging.getLogger(__name__)
    logger.info(...)

...and NOT call print() for anything other than genuine CLI output (a
`__main__` smoke-test script printing its own results is fine; a
library function narrating what it's doing to whichever process happens
to import it is not -- print() can't be filtered by level, redirected
per-module, or turned off by a caller that doesn't want it).

Whatever actually runs the process (a script's __main__ block, a
long-lived server entry point, etc.) should call configure_logging()
once, near the top, before doing any real work:

    from logging_config import configure_logging
    configure_logging()

Calling it more than once is harmless (idempotent) -- safe even if two
different entry points both do it, e.g. one script importing another.
"""

import logging
import os
import re

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_configured = False

# Matches e.g. "'Authorization': 'Bearer eyJ...'" or "'X-PrivateKey': 'shFC9gWu'"
# inside a dict-repr'd headers blob, single- or double-quoted either side.
_SENSITIVE_HEADER_RE = re.compile(
    r"(['\"](?:Authorization|X-PrivateKey)['\"]\s*:\s*['\"])[^'\"]*(['\"])"
)


class RedactSensitiveHeaders(logging.Filter):
    """Scrubs live credentials out of log records before they're emitted.

    brokers/smartapi_client.py's underlying SmartApi SDK dumps the full
    outgoing request headers -- including the live session Bearer token
    and the API private key -- straight into the log on every HTTP
    failure (see its smartConnect.py `_request()`, `logger.error(f"...
    Headers: {headers}...")`). That's a plaintext credential leak on
    every timeout/network hiccup, and it happens inside a vendored pip
    package we can't safely patch in place (a `pip install -r
    requirements.txt` would silently wipe any in-place edit).
    Intercepting at the log-record level instead works regardless of
    how the SDK itself changes: this filter is attached directly to the
    `logzero` singleton logger the SDK (and our own brokers/*.py, which
    intentionally share it -- see their `from logzero import logger`)
    both log through, so it runs on every record before any handler
    (console, file, etc.) sees it.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if "Authorization" in msg or "X-PrivateKey" in msg:
            record.msg = _SENSITIVE_HEADER_RE.sub(r"\1[REDACTED]\2", msg)
            record.args = ()
        return True


def _attach_credential_redaction() -> None:
    """Best-effort: also covers logzero's singleton logger, which is a
    separate object from the stdlib root logger and has its own
    handler(s) attached directly (see RedactSensitiveHeaders' docstring
    for why). Safe to no-op if logzero isn't installed in this
    environment -- nothing here is required for the app to run."""
    logging.getLogger().addFilter(RedactSensitiveHeaders())
    try:
        import logzero
        logzero.logger.addFilter(RedactSensitiveHeaders())
    except ImportError:
        pass


def configure_logging(level: str = None) -> None:
    """Configure root logging once per process.

    level: logging level name (e.g. "DEBUG", "INFO", "WARNING"). Defaults
    to the LOG_LEVEL environment variable, or "INFO" if that's unset.
    """
    global _configured
    if _configured:
        return

    resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(level=resolved_level, format=_DEFAULT_FORMAT)
    _attach_credential_redaction()
    _configured = True
