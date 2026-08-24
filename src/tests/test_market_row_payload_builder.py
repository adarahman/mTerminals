import pandas as pd

from application.payload_builders.market_rows import (
    build_bid_ask_map,
    build_capital_map,
    build_chain_rows,
)


def test_market_row_builder_combines_chain_depth_and_capital():
    depth = build_bid_ask_map(pd.DataFrame([{
        "StrikePrice": 25000, "CE_BidPrice": 99.5, "PE_AskPrice": 101.5,
        "CE_BidQty": 10, "CE_AskQty": 11, "PE_BidQty": 12, "PE_AskQty": 13,
        "CE_BuyQty": 100, "CE_SellQty": 110, "PE_BuyQty": 120, "PE_SellQty": 130,
    }]))
    capital = build_capital_map(pd.DataFrame([{
        "strike": 25000, "ce_premium_locked": 1234.56, "footprint_score": 88.8,
    }]))
    master = pd.DataFrame([{
        "strike": 25000, "ce_ltp": 100, "pe_ltp": 101,
        "ce_oi": 1000, "pe_oi": 900, "ce_iv": 12.345, "pe_iv": None,
    }])

    row = build_chain_rows(master, 25000, depth, capital)[0]

    assert row["atm"] is True
    assert row["ceBid"] == 99.5
    assert row["peTotalAskQty"] == 130
    assert row["cePremiumLocked"] == 1234.56
    assert row["footprintScore"] == 88.8
    assert row["peIV"] is None
