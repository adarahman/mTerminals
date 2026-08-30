import logging
from types import SimpleNamespace

from application.market_pipeline.extra_chains import ExtraChainService
from application.market_pipeline.resources import RetirableExecutorPool
from application.pipeline_config import RuntimeConfig


def _expiry_manager(*, near="08-Sep-2026", monthly="29-Sep-2026"):
    return SimpleNamespace(
        context=SimpleNamespace(
            near=SimpleNamespace(date_str=near) if near else None,
            monthly=SimpleNamespace(date_str=monthly) if monthly else None,
        )
    )


def _config(**overrides):
    values = {
        "symbol": "NIFTY",
        "expiry": "01-Sep-2026",
        "futures_expiry": "NEAR",
        "no_extra_chains": False,
        "use_smartapi": False,
    }
    values.update(overrides)
    return RuntimeConfig(**values)


def test_builds_secondary_expiries_once_and_reuses_cached_bundles():
    calls = []

    def build(symbol, expiry, exchange, **_kwargs):
        calls.append((symbol, expiry, exchange))
        return expiry, {"master": expiry}, {"context": expiry}, 1.0, expiry

    pool = RetirableExecutorPool(
        max_workers=2,
        register_shutdown=lambda _shutdown: None,
    )
    service = ExtraChainService(
        build_bundle=build,
        exchange_for_symbol=lambda _symbol: "NSE",
        executor_pool=pool,
        logger=logging.getLogger(__name__),
        clock=lambda: 100.0,
    )
    timings = {}

    first = service.build(_expiry_manager(), _config(), timings=timings)
    second = service.build(_expiry_manager(), _config(), timings=timings)
    pool.retire()

    assert set(first) == {"08-Sep-2026", "29-Sep-2026"}
    assert second == first
    assert sorted(calls) == [
        ("NIFTY", "08-Sep-2026", "NSE"),
        ("NIFTY", "29-Sep-2026", "NSE"),
    ]
    assert timings["extraNEAR"] == 0.0
    assert timings["extraMONTHLY"] == 0.0


def test_skips_disabled_and_active_expiry_slots():
    calls = []
    pool = RetirableExecutorPool(
        max_workers=1,
        register_shutdown=lambda _shutdown: None,
    )
    service = ExtraChainService(
        build_bundle=lambda *args, **kwargs: calls.append((args, kwargs)),
        exchange_for_symbol=lambda _symbol: "NSE",
        executor_pool=pool,
        logger=logging.getLogger(__name__),
    )

    assert service.build(_expiry_manager(), _config(no_extra_chains=True)) == {}
    assert service.build(
        _expiry_manager(near="01-Sep-2026", monthly=None), _config()
    ) == {}
    assert calls == []
