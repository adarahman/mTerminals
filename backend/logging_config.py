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

_DEFAULT_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_configured = False


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
    _configured = True
