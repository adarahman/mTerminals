from types import SimpleNamespace

from application.payload_builders.strategies import build_strategies


def test_strategy_builder_resolves_live_leg_prices():
    engine_result = SimpleNamespace(
        strategies=[
            {
                "name": "Bull Call Spread",
                "rationale": "Defined-risk bullish trade",
                "legs": [
                    {"type": "CE", "strike": 25000, "action": "BUY", "ltp": 0},
                    {"type": "CE", "strike": 25200, "action": "SELL", "ltp": 0},
                ],
            }
        ]
    )
    chain = [
        {"strike": 25000, "ceLTP": 120.5, "peLTP": 90},
        {"strike": 25200, "ceLTP": 55.25, "peLTP": 150},
    ]

    strategies = build_strategies(
        {"bias": "Bullish", "iv_rank": 30, "atm": 25000},
        engine_result,
        chain,
    )

    assert strategies[0]["name"] == "Bull Call Spread"
    assert strategies[0]["legs"][0]["ltp"] == 120.5
    assert strategies[0]["legs"][1]["ltp"] == 55.25
