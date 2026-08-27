from server.feeds.orchestration import build_feed_managers


def test_feed_managers_own_distinct_provider_state():
    managers = build_feed_managers(
        default_symbol=lambda: "NIFTY",
        main_loop=lambda: None,
        log=lambda _message: None,
    )

    assert set(managers) == {"SMARTAPI", "UPSTOX", "SHOONYA", "KOTAK"}
    assert len({id(manager.state) for manager in managers.values()}) == 4

    managers["SMARTAPI"].state.current_expiry = "01SEP2026"
    assert managers["SMARTAPI"].current_expiry == "01SEP2026"
    assert managers["UPSTOX"].current_expiry is None
