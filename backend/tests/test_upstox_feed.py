from datetime import UTC, datetime

from server.feeds.upstox import (
    FeedState,
    resolve_chain_tokens,
    start_new_feed,
    switch_existing_feed,
)


def _parse(value):
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).date()


def test_resolver_builds_option_and_index_subscriptions(monkeypatch):
    import brokers.upstox_client as source

    monkeypatch.setattr(
        source, "list_expiries", lambda *_args, **_kwargs: ["2026-08-28"]
    )
    monkeypatch.setattr(
        source,
        "get_atm_chain",
        lambda *_args, **_kwargs: {
            "rows": [{"instrument_key": "NSE_FO|100", "strike": 25000, "type": "CE"}]
        },
    )
    monkeypatch.setattr(source, "INDEX_KEYS", {"NIFTY": "NSE_INDEX|Nifty 50"})

    resolved = resolve_chain_tokens(
        "NIFTY",
        10,
        None,
        is_bse=lambda _symbol: False,
        parse_expiry=_parse,
        report=lambda _message: None,
    )

    metadata, expiry, index_key = resolved
    assert expiry == "2026-08-28"
    assert metadata["NSE_FO|100"] == {"strike": 25000, "option_type": "CE"}
    assert metadata["NSE_INDEX|Nifty 50"] == {"strike": None, "option_type": "INDEX"}
    assert index_key == "NSE_INDEX|Nifty 50"


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
        def __init__(self, **kwargs):
            assert kwargs["mode"] == "full"

        def connect(self):
            events.append("stream.connect")

        def run_forever_with_reconnect(self):
            return None

        def subscribe(self, instruments):
            events.append(("subscribe", list(instruments)))

        def unsubscribe(self, instruments):
            events.append(("unsubscribe", list(instruments)))

    class Thread:
        def start(self):
            return None

    state = FeedState()
    first = ({"NSE_FO|100": {"strike": 25000, "option_type": "CE"}}, "2026-08-28", None)
    second = (
        {"NSE_FO|101": {"strike": 25100, "option_type": "PE"}},
        "2026-09-04",
        None,
    )
    resolutions = iter((first, second))

    assert start_new_feed(
        state,
        object(),
        "NIFTY",
        10,
        None,
        lambda *_args: next(resolutions),
        Aggregator,
        lambda *_args: None,
        object(),
        Stream,
        lambda **_kwargs: Thread(),
        lambda _seconds: None,
        lambda _message: None,
    )
    assert switch_existing_feed(
        state,
        "NIFTY",
        10,
        "2026-09-04",
        lambda *_args: next(resolutions),
        lambda _message: None,
    )
    assert state.current_expiry == "2026-09-04"
    assert ("unsubscribe", ["NSE_FO|100"]) in events
    assert ("subscribe", ["NSE_FO|101"]) in events


def test_stop_feed_unsubscribes_and_clears_instruments():
    from server.feeds.upstox import stop_feed

    events = []

    class StopStream:
        def unsubscribe(self, instruments):
            events.append(list(instruments))

    state = FeedState(
        stream=StopStream(), instruments=["NSE_FO|100", "NSE_INDEX|Nifty 50"]
    )
    assert stop_feed(state, report=lambda _message: None)
    assert events == [["NSE_FO|100", "NSE_INDEX|Nifty 50"]]
    assert state.instruments is None
