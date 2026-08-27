from types import SimpleNamespace

from server.websocket_security import (
    build_allowed_origins,
    host_is_loopback,
    origin_allowed,
)


def _request(origin, remote):
    headers = {} if origin is None else {"Origin": origin}
    return SimpleNamespace(headers=headers, remote=remote)


ALLOWED = build_allowed_origins("127.0.0.1", 5500)


def test_file_dashboard_origin_is_allowed_from_loopback():
    assert origin_allowed(_request("null", "127.0.0.1"), ALLOWED)
    assert origin_allowed(_request("null", "::1"), ALLOWED)


def test_null_origin_is_rejected_from_remote_peer():
    assert not origin_allowed(_request("null", "192.168.1.50"), ALLOWED)


def test_originless_clients_are_restricted_to_loopback():
    assert origin_allowed(_request(None, "127.0.0.1"), ALLOWED)
    assert not origin_allowed(_request(None, "192.168.1.50"), ALLOWED)


def test_unlisted_web_origin_remains_rejected():
    assert not origin_allowed(
        _request("https://example.invalid", "127.0.0.1"), ALLOWED
    )


def test_only_loopback_listener_hosts_are_considered_safe():
    assert host_is_loopback("localhost")
    assert host_is_loopback("127.0.0.1")
    assert host_is_loopback("::1")
    assert not host_is_loopback("0.0.0.0")
    assert not host_is_loopback("192.168.1.50")
