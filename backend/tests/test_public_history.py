import json

from brokers import public_history


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _payload(ts, close):
    return {"chart": {"result": [{
        "timestamp": [ts],
        "indicators": {"quote": [{
            "open": [close - 1], "high": [close + 1],
            "low": [close - 2], "close": [close], "volume": [123],
        }]},
    }]}}


def test_public_history_bootstraps_and_persists_cash_candles(tmp_path, monkeypatch):
    monkeypatch.setattr(public_history.time, "time", lambda: 2_000_000)
    monkeypatch.setattr(public_history.requests, "get", lambda *a, **k: _Response(_payload(1_999_900, 25000)))

    rows = public_history.fetch_public_history("NIFTY", "1m", 5, cache_dir=tmp_path)

    assert rows[0]["c"] == 25000
    saved = list(tmp_path.glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text())[0]["v"] == 123.0


def test_public_history_never_substitutes_cash_for_futures(tmp_path, monkeypatch):
    called = False

    def unexpected(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(public_history.requests, "get", unexpected)
    rows = public_history.fetch_public_history(
        "NIFTY", "1m", 5, instrument="FUT", expiry="2026-09-24", cache_dir=tmp_path
    )

    assert rows == []
    assert called is False
