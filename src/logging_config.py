"""Compatibility imports for the former root-level logging module.

New code must import from :mod:`infrastructure.logging`.
"""

from infrastructure.logging import (
    RedactSensitiveHeaders,
    StructuredFormatter,
    configure_logging,
    redact_sensitive_text,
)

__all__ = [
    "RedactSensitiveHeaders",
    "StructuredFormatter",
    "configure_logging",
    "redact_sensitive_text",
]
