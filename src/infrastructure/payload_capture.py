"""Capture canonical payloads emitted by an output/export adapter."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class PayloadExportCapture:
    """Wrap an exporter and retain its most recently emitted payload.

    Some older exporters communicate only by writing a file.  A fallback
    loader may be injected for that case without coupling this adapter to a
    particular file format or legacy module.
    """

    def __init__(
        self,
        *,
        exporter: Callable[..., Any],
        fallback_loader: Callable[[], Any] | None = None,
        export_overrides: dict[str, Any] | None = None,
    ):
        self._exporter = exporter
        self._fallback_loader = fallback_loader
        self._export_overrides = dict(export_overrides or {})
        self._payload = None

    def export(self, *args, **kwargs):
        kwargs.update(self._export_overrides)
        payload = self._exporter(*args, **kwargs)
        if payload is None and self._fallback_loader is not None:
            try:
                payload = self._fallback_loader()
            except Exception:
                payload = None
        self._payload = payload
        return payload

    def clear(self) -> None:
        self._payload = None

    @property
    def payload(self):
        return self._payload
