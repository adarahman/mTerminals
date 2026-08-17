from types import SimpleNamespace


def _request(origin, remote):
    headers = {} if origin is None else {"Origin": origin}
    return SimpleNamespace(headers=headers, remote=remote)


def test_file_dashboard_origin_is_allowed_from_loopback(ws_server_live):
    assert ws_server_live._origin_allowed(_request("null", "127.0.0.1"))
    assert ws_server_live._origin_allowed(_request("null", "::1"))


def test_null_origin_is_rejected_from_remote_peer(ws_server_live):
    assert not ws_server_live._origin_allowed(_request("null", "192.168.1.50"))


def test_unlisted_web_origin_remains_rejected(ws_server_live):
    assert not ws_server_live._origin_allowed(_request("https://example.invalid", "127.0.0.1"))
