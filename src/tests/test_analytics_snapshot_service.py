import logging
from types import SimpleNamespace

import pandas as pd

from application.market_pipeline.snapshot import AnalyticsSnapshotService
from application.pipeline_config import RuntimeConfig


class _ExtraChains:
    def __init__(self):
        self.calls = []

    def build(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return {"08-Sep-2026": {"secondary": True}}


class _EngineResult:
    master = {"signal": "hold"}

    def to_ctx_dict(self):
        return {"spot_change": 0, "spot_chg_pct": 0}


def _config(symbol="SENSEX"):
    return RuntimeConfig(
        symbol=symbol,
        expiry="03-Sep-2026",
        futures_expiry="NEAR",
        no_virtual_oi=False,
        use_smartapi=False,
    )


def test_builds_history_engine_context_and_export_payload():
    extra_chains = _ExtraChains()
    engine_calls = []
    exports = []
    appended = []
    clock = iter([10.0, 10.25])
    expiry_manager = SimpleNamespace(
        context=SimpleNamespace(
            current=SimpleNamespace(date_str="03-Sep-2026"),
            monthly=SimpleNamespace(date_str="24-Sep-2026"),
            far=None,
            near=SimpleNamespace(date_str="10-Sep-2026"),
        )
    )
    service = AnalyticsSnapshotService(
        extra_chains=extra_chains,
        logger=logging.getLogger(__name__),
        engine_builder=lambda **kwargs: engine_calls.append(kwargs)
        or _EngineResult(),
        contributors_builder=lambda *_args: ["contributor"],
        expiry_manager_factory=lambda _dates: expiry_manager,
        dte_calculator=lambda _expiry: 4.0,
        history_reader=lambda _symbol: {"previous": True},
        history_builder=lambda frame, symbol, prev_poll: {
            "rows": len(frame),
            "symbol": symbol,
            "previous": prev_poll,
        },
        history_appender=appended.append,
        lot_sizes={"SENSEX": 20},
        clock=lambda: next(clock),
    )
    frame = pd.DataFrame(
        [
            {"StrikePrice": 80000},
            {"StrikePrice": 80000},
            {"StrikePrice": None},
        ]
    )
    timings = {"vix": 0.2, "publicBse:SENSEX": 0.4}

    completed = service.build_and_export(
        market_data={
            "df": frame,
            "spot": 80000.0,
            "resolved": "ignored-for-bse",
            "expiry_dates": ["03-Sep-2026", "24-Sep-2026"],
            "df_fut": pd.DataFrame(),
            "df_idx": pd.DataFrame(),
            "india_vix": 12.0,
            "india_vix_chg_pct": -0.5,
            "all_indices": [
                {
                    "Symbol": "SENSEX",
                    "Change": 100.0,
                    "% Change": 0.12,
                }
            ],
            "price_source_used": "EQ",
        },
        runtime_config=_config(),
        exchange="BSE",
        broker_adapters=None,
        timings=timings,
        export_dashboard=lambda **kwargs: exports.append(kwargs),
    )

    assert completed is True
    assert appended == [
        {"rows": 1, "symbol": "SENSEX", "previous": {"previous": True}}
    ]
    assert engine_calls[0]["lot_size"] == 20
    assert engine_calls[0]["near_expiry"] == "03-Sep-2026"
    assert engine_calls[0]["far_expiry"] == "24-Sep-2026"
    assert exports[0]["EXPIRY"] == "03-Sep-2026"
    assert exports[0]["ctx_dict"]["spot_change"] == 100.0
    assert exports[0]["ctx_dict"]["spot_chg_pct"] == 0.12
    assert exports[0]["extra_chains"] == {
        "08-Sep-2026": {"secondary": True}
    }
    assert timings["quotes"] == 0.4
    assert timings["engine"] == 0.25


def test_invalid_spot_aborts_before_history_or_export():
    exports = []
    service = AnalyticsSnapshotService(
        extra_chains=_ExtraChains(),
        logger=logging.getLogger(__name__),
        history_reader=lambda _symbol: (_ for _ in ()).throw(
            AssertionError("history must not be read")
        ),
    )

    completed = service.build_and_export(
        market_data={"df": pd.DataFrame(), "spot": 0, "resolved": "x"},
        runtime_config=_config("NIFTY"),
        exchange="NSE",
        broker_adapters=None,
        timings={},
        export_dashboard=lambda **kwargs: exports.append(kwargs),
    )

    assert completed is False
    assert exports == []
