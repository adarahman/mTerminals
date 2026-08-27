"""WebSocket origin and listener-host security policy."""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable


def host_is_loopback(host: str) -> bool:
    normalized = str(host or "").strip().lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def build_allowed_origins(
    host: str, port: int, extra_origins: Iterable[str] = ()
) -> frozenset[str]:
    defaults = {
        f"http://{host}:{port}",
        f"http://localhost:{port}",
        f"http://127.0.0.1:{port}",
    }
    extras = {origin.strip() for origin in extra_origins if origin.strip()}
    return frozenset(defaults | extras)


def peer_is_loopback(request) -> bool:
    try:
        return ipaddress.ip_address(request.remote).is_loopback
    except (TypeError, ValueError):
        return False


def origin_allowed(request, allowed_origins: Iterable[str]) -> bool:
    origin = request.headers.get("Origin")
    if origin is None or origin == "null":
        return peer_is_loopback(request)
    return origin in allowed_origins
