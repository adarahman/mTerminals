import pandas as pd

from application.market_pipeline.context import assemble_market_context
from application.pipeline_config import RuntimeConfig
from market.option_chain.gatherer import GatheredMarketInputs
from market.option_chain.requests import MarketDataRequestPlan


def _request(*, broker_enabled=False, exchange="NSE"):
    return MarketDataRequestPlan(
        symbol="NIFTY",
        option_expiry="01-Sep-2026",
        option_exchange=exchange,
        strict_expiry=False,
        futures_expiry="NEAR",
        broker_enabled=broker_enabled,
    )


def _config(*, broker_enabled=False):
    return RuntimeConfig(
        symbol="NIFTY",
        expiry="01-Sep-2026",
        futures_expiry="NEAR",
        use_smartapi=broker_enabled,
    )


def test_public_context_enriches_indices_and_normalizes_missing_futures():
    chain = pd.DataFrame([{"StrikePrice": 25000}])
    indices = pd.DataFrame(
        [{"Symbol": "NIFTY", "Volume": 12, "Value": 34}]
    )
    gathered = GatheredMarketInputs(
        chain=(chain, 25000.0, "01-Sep-2026", ["01-Sep-2026"]),
        futures=None,
        indices=indices,
        public_bse_quotes=({"Symbol": "SENSEX", "LTP": 80000},),
    )

    context = assemble_market_context(
        gathered=gathered,
        request=_request(),
        runtime_config=_config(),
        unified_public_market_data=lambda _indices: (
            12.5,
            -1.2,
            [{"Symbol": "NIFTY", "LTP": 25000}],
        ),
        select_spot=lambda df, spot, *_args: (df, spot, "EQ"),
    )

    assert context["resolved"] == "01-Sep-2026"
    assert context["india_vix"] == 12.5
    assert context["df_fut"].empty
    assert context["all_indices"] == [
        {"Symbol": "NIFTY", "LTP": 25000, "Volume": 12, "Value": 34},
        {"Symbol": "SENSEX", "LTP": 80000},
    ]


def test_broker_context_uses_gathered_quotes_and_shapes_futures_record():
    chain = pd.DataFrame([{"StrikePrice": 25000}])
    gathered = GatheredMarketInputs(
        chain=(chain, 25000.0, "01-Sep-2026", ["01-Sep-2026"]),
        futures={"ltp": 25100},
        indices=pd.DataFrame(),
        ticker_payload=[{"Symbol": "NIFTY", "LTP": 25000}],
        vix=(None, 0.5),
        sensex_quote={"Symbol": "SENSEX", "LTP": 80000},
    )

    context = assemble_market_context(
        gathered=gathered,
        request=_request(broker_enabled=True),
        runtime_config=_config(broker_enabled=True),
        unified_public_market_data=lambda _indices: (_ for _ in ()).throw(
            AssertionError("public adapter must not be used in broker mode")
        ),
        select_spot=lambda df, _spot, *_args: (df, 25001.0, "LIVE"),
    )

    assert context["spot"] == 25001.0
    assert context["price_source_used"] == "LIVE"
    assert context["india_vix"] == 0.0
    assert context["india_vix_chg_pct"] == 0.5
    assert context["df_fut"].to_dict("records") == [{"ltp": 25100}]
