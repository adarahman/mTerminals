"""Uniform structured logging for broker boundaries.

Use this for lifecycle events that need to be comparable across providers.
It intentionally does not replace provider-native diagnostic logs; those
remain useful when investigating a specific SDK response.
"""
from __future__ import annotations

import logging
from typing import Optional


def broker_event(
    logger: logging.Logger,
    *,
    provider: str,
    operation: str,
    status: str,
    level: int = logging.INFO,
    reason: Optional[str] = None,
) -> None:
    """Emit a comparison-friendly broker event.

    Stable fields: ``event``, ``subsystem``, ``provider``, ``operation``,
    ``status``, and (on failure) ``reason``. Avoid passing credentials or raw
    request payloads as a reason.
    """
    logger.log(
        level,
        "broker %s %s: %s",
        provider.upper(),
        operation,
        status,
        extra={
            "event": "broker." + operation,
            "subsystem": "broker",
            "provider": provider.upper(),
            "operation": operation,
            "status": status,
            "reason": reason,
        },
    )
