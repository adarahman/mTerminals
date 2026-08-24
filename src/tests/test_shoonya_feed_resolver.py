from datetime import datetime

from server.feeds.shoonya import (
    FeedState,
    resolve_chain_tokens,
    start_new_feed,
    switch_existing_feed,
)


def _parse(value):
    return datetime.strptime(value, "%d-%b-%Y").date()


def test_resolver_builds_option_and_index_subscriptions(monkeypatch):
    import brokers.shoonya.market_data as source

    monkeypatch.setattr(source, "list_expiries", lambda *_args, **_kwargs: ["28-Aug-2026"])
    monkeypatch.setattr(
        source,
        "get_atm_chain",
        lambda *_args, **_kwargs: {
            "rows": [{"token": "100", "strike": 25000, "type": "CE"}]
        },
    )
    monkeypatch.setattr(source, "index_tokens", lambda: {"NIFTY": {"exchange": "NSE", "token": "26000"}})

    resolved = resolve_chain_tokens(
        "NIFTY", 10, None, lambda _symbol: False, _parse, lambda _message: None
    )

    metadata, expiry, index_instrument = resolved
    assert expiry == "28-Aug-2026"
    assert metadata["NFO|100"] == {"strike": 25000, "option_type": "CE"}
    assert metadata["NSE|26000"] == {"strike": None, "option_type": "INDEX"}
    assert index_instrument == "NSE|26000"


def test_lifecycle_reuses_one_socket_for_subscription_switch():
    events = []

    class Aggregator:
        def __init__(self, metadata, *_args, **_kwargs):
            self.metadata = metadata
            self.on_tick = object()

        def start(self):
            events.append("aggregator.start")

        def update_token_meta(self, metadata):
            self.metadata = metadata
            events.append("aggregator.update")

    class Stream:
        def connect(self):
            events.append("stream.connect")

        def run_forever_with_reconnect(self):
            return None

        def subscribe(self, instruments):
            events.append(("subscribe", list(instruments)))

        def unsubscribe(self, instruments):
            events.append(("unsubscribe", list(instruments)))

    state = FeedState()
    first = ({"NFO|100": {"strike": 25000, "option_type": "CE"}}, "28-Aug-2026", None)
    second = ({"NFO|101": {"strike": 25100, "option_type": "PE"}}, "04-Sep-2026", None)
    resolutions = iter((first, second))
    resolve = lambda *_args: next(resolutions)

    assert start_new_feed(
        state, object(), "NIFTY", 10, None, resolve, Aggregator, lambda *_args: None,
        object(), lambda **_kwargs: Stream(), lambda **_kwargs: type("Thread", (), {"start": lambda self: None})(),
        lambda _seconds: None, lambda _message: None,
    )
    assert switch_existing_feed(
        state, "NIFTY", 10, "04-Sep-2026", resolve, lambda _message: None
    )
    assert state.current_expiry == "04-Sep-2026"
    assert ("unsubscribe", ["NFO|100"]) in events
    assert ("subscribe", ["NFO|101"]) in events


def test_stop_feed_unsubscribes_and_clears_instruments():
    from server.feeds.shoonya import FeedState, stop_feed

    calls = []

    class Stream:
        def unsubscribe(self, instruments):
            calls.append(list(instruments))

    state = FeedState(stream=Stream(), instruments=["NFO|101", "NSE|26000"])
    assert stop_feed(state, report=lambda _message: None)
    assert calls == [["NFO|101", "NSE|26000"]]
    assert state.instruments is None
