from types import SimpleNamespace

import pandas as pd

from application.market_pipeline.chain_service import ChainAnalyticsService
from application.pipeline_config import RuntimeConfig


def _config(*, broker_enabled=False):
    return RuntimeConfig(
        symbol="NIFTY",
        expiry="01-Sep-2026",
        futures_expiry="NEAR",
        strikes_each_side=15,
        use_smartapi=broker_enabled,
    )


def test_canonicalization_is_provider_free_in_public_mode_and_injected_in_broker_mode():
    public_config = _config()
    broker_config = _config(broker_enabled=True)
    adapters = SimpleNamespace(
        canonicalize_symbol=lambda symbol: {"FULL COMPANY LTD": "COMPANY"}.get(
            symbol, symbol
        )
    )

    assert (
        ChainAnalyticsService.canonicalize(
            " full company ltd ", public_config
        )
        == "FULL COMPANY LTD"
    )
    assert (
        ChainAnalyticsService.canonicalize(
            "full company ltd", broker_config, adapters
        )
        == "COMPANY"
    )


def test_expiry_bundle_cleans_chain_and_builds_engine_context():
    engine_calls = []
    engine_result = SimpleNamespace(
        master={"signal": "hold"},
        to_ctx_dict=lambda: {"context": True},
    )
    service = ChainAnalyticsService(
        public_market=SimpleNamespace(),
        engine_builder=lambda **kwargs: engine_calls.append(kwargs)
        or engine_result,
        dte_calculator=lambda expiry: 3.0,
        lot_sizes={"NIFTY": 65},
    )
    frame = pd.DataFrame(
        [
            {"StrikePrice": 25000},
            {"StrikePrice": 25000},
            {"StrikePrice": None},
        ]
    )
    service.fetch = lambda *_args, **_kwargs: (
        frame,
        25000.0,
        "01-Sep-2026",
        ["01-Sep-2026"],
    )

    clean, master, context, dte, resolved = service.build_expiry_bundle(
        "NIFTY",
        "01-Sep-2026",
        runtime_config=_config(),
        velocity_window_minutes=5,
    )

    assert clean["StrikePrice"].tolist() == [25000.0]
    assert master == {"signal": "hold"}
    assert context == {"context": True}
    assert dte == 3.0
    assert resolved == "01-Sep-2026"
    assert engine_calls[0]["lot_size"] == 65
    assert engine_calls[0]["n_strikes_each_side"] == 15
    assert "velocity_window_minutes" not in engine_calls[0]
