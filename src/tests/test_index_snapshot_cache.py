import logging

from application.market_pipeline.index_cache import IndexSnapshotCache


def test_cold_cache_fetches_synchronously_once():
    calls = []
    cache = IndexSnapshotCache(
        fetch=lambda: calls.append("fetch") or {"version": 1},
        ttl_seconds=20,
        logger=logging.getLogger(__name__),
    )

    assert cache.get() == {"version": 1}
    assert cache.get() == {"version": 1}
    assert calls == ["fetch"]


def test_stale_cache_returns_old_value_and_starts_only_one_refresh():
    fetches = iter([{"version": 1}, {"version": 2}])
    background = []
    cache = IndexSnapshotCache(
        fetch=lambda: next(fetches),
        ttl_seconds=0,
        logger=logging.getLogger(__name__),
        start_background=background.append,
    )

    assert cache.get() == {"version": 1}
    assert cache.get() == {"version": 1}
    assert cache.get() == {"version": 1}
    assert len(background) == 1

    background.pop()()

    assert cache.get() == {"version": 2}
    assert len(background) == 1


def test_failed_refresh_preserves_stale_value_and_allows_retry(caplog):
    outcomes = iter([{"version": 1}, RuntimeError("offline"), {"version": 2}])

    def fetch():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    background = []
    cache = IndexSnapshotCache(
        fetch=fetch,
        ttl_seconds=0,
        logger=logging.getLogger(__name__),
        start_background=background.append,
    )

    assert cache.get() == {"version": 1}
    assert cache.get() == {"version": 1}
    with caplog.at_level(logging.ERROR):
        background.pop()()
    assert "index snapshot refresh failed: offline" in caplog.text

    assert cache.get() == {"version": 1}
    background.pop()()
    assert cache.get() == {"version": 2}
