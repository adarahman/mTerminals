from server.feeds.smartapi import FeedState, start_new_feed, switch_existing_feed


class Aggregator:
    def __init__(self, metadata, *_args, **_kwargs):
        self.token_meta = metadata
        self.on_tick = object()

    def start(self):
        pass

    def update_token_meta(self, metadata):
        self.token_meta = metadata


class Stream:
    def __init__(self, **_kwargs):
        self.calls = []

    def connect(self):
        pass

    def run_forever_with_reconnect(self):
        pass

    def subscribe(self, exchange, tokens):
        self.calls.append(("subscribe", exchange, list(tokens)))

    def unsubscribe(self, exchange, tokens):
        self.calls.append(("unsubscribe", exchange, list(tokens)))


class Thread:
    def start(self):
        pass


def _resolved(option="101", index="26000"):
    metadata = {
        option: {"strike": 25000, "option_type": "CE"},
        index: {"strike": None, "option_type": "INDEX"},
    }
    return "NFO", metadata, "28AUG2026", index, "NSE_CM", None, None


def test_lifecycle_switches_exchange_scoped_subscriptions_on_one_socket():
    state = FeedState()
    results = iter((_resolved(), _resolved("102", "26001")))

    assert start_new_feed(
        state,
        object(),
        "NIFTY",
        10,
        None,
        resolve=lambda *_args: next(results),
        aggregator_factory=Aggregator,
        callback=lambda *_args: None,
        tick_event=object(),
        stream_factory=Stream,
        exchange_types={"NFO": 2, "NSE_CM": 1},
        spawn_thread=lambda **_kwargs: Thread(),
        wait=lambda _seconds: None,
        report=lambda _message: None,
    )
    stream = state.stream
    assert switch_existing_feed(
        state,
        "NIFTY",
        10,
        None,
        resolve=lambda *_args: next(results),
        exchange_types={"NFO": 2, "NSE_CM": 1},
        report=lambda _message: None,
    )

    assert ("unsubscribe", 2, ["101"]) in stream.calls
    assert ("unsubscribe", 1, ["26000"]) in stream.calls
    assert ("subscribe", 2, ["102"]) in stream.calls
    assert ("subscribe", 1, ["26001"]) in stream.calls
    assert state.current_expiry == "28AUG2026"
