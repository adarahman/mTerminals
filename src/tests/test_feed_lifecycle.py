from server.feed_lifecycle import stop


def test_stop_dispatches_supported_provider():
    calls = []
    assert stop("upstox", {"UPSTOX": lambda: calls.append("stopped")}, lambda cb: cb())
    assert calls == ["stopped"]
    assert not stop("kite", {"UPSTOX": lambda: None}, lambda cb: cb())
