"""Option-chain market row payload builders."""
from __future__ import annotations

import logging

from .common import integer, nullable_rounded_number, rounded_number


logger = logging.getLogger(__name__)

_BID_ASK_QTY_COLUMNS = (
    "CE_BidQty", "CE_AskQty", "PE_BidQty", "PE_AskQty",
    "CE_BuyQty", "CE_SellQty", "PE_BuyQty", "PE_SellQty",
)
_quantity_warning_emitted = False


def build_bid_ask_map(frame) -> dict[int, dict]:
    global _quantity_warning_emitted
    if not _quantity_warning_emitted:
        missing = [column for column in _BID_ASK_QTY_COLUMNS if column not in frame.columns]
        if missing:
            logger.warning(
                "bid/ask quantity columns missing: %s; exporting zero values",
                missing,
            )
        _quantity_warning_emitted = True

    result = {}
    for row in frame.to_dict("records"):
        strike = integer(row.get("StrikePrice", 0))
        if strike <= 0:
            continue
        result[strike] = {
            "ceBid": rounded_number(row.get("CE_BidPrice", 0), 2),
            "ceAsk": rounded_number(row.get("CE_AskPrice", 0), 2),
            "peBid": rounded_number(row.get("PE_BidPrice", 0), 2),
            "peAsk": rounded_number(row.get("PE_AskPrice", 0), 2),
            "ceChg": rounded_number(row.get("CE_Change", 0), 2),
            "peChg": rounded_number(row.get("PE_Change", 0), 2),
            "ceBidQty": integer(row.get("CE_BidQty", 0)),
            "ceAskQty": integer(row.get("CE_AskQty", 0)),
            "peBidQty": integer(row.get("PE_BidQty", 0)),
            "peAskQty": integer(row.get("PE_AskQty", 0)),
            "ceTotalBidQty": integer(row.get("CE_BuyQty", 0)),
            "ceTotalAskQty": integer(row.get("CE_SellQty", 0)),
            "peTotalBidQty": integer(row.get("PE_BuyQty", 0)),
            "peTotalAskQty": integer(row.get("PE_SellQty", 0)),
        }
    return result


def build_capital_map(frame) -> dict[int, dict]:
    if frame is None or getattr(frame, "empty", True):
        return {}
    return {
        integer(row["strike"]): row
        for row in frame.to_dict("records")
    }


def build_chain_rows(master, atm_strike, bid_ask_map, capital_map=None) -> list[dict]:
    capital_map = capital_map or {}
    rows = []
    for source in master.to_dict("records"):
        strike = integer(source["strike"])
        depth = bid_ask_map.get(strike, {})
        capital = capital_map.get(strike, {})
        row = {
            "strike": strike,
            "atm": strike == atm_strike,
            "atmStrike": atm_strike,
            "ceLTP": rounded_number(source.get("ce_ltp", 0), 2),
            "ceBid": depth.get("ceBid", 0.0),
            "ceAsk": depth.get("ceAsk", 0.0),
            "ceChg": depth.get("ceChg", 0.0),
            "ceBidQty": depth.get("ceBidQty", 0),
            "ceAskQty": depth.get("ceAskQty", 0),
            "ceTotalBidQty": depth.get("ceTotalBidQty", 0),
            "ceTotalAskQty": depth.get("ceTotalAskQty", 0),
            "ceOI": integer(source.get("ce_oi", 0)),
            "ceChgOI": integer(source.get("ce_oi_chg", 0)),
            "ceVol": integer(source.get("ce_volume", 0)),
            "ceIV": nullable_rounded_number(source.get("ce_iv"), 2),
            "ceSignal": str(source.get("ce_signal", "")),
            "peLTP": rounded_number(source.get("pe_ltp", 0), 2),
            "peBid": depth.get("peBid", 0.0),
            "peAsk": depth.get("peAsk", 0.0),
            "peChg": depth.get("peChg", 0.0),
            "peBidQty": depth.get("peBidQty", 0),
            "peAskQty": depth.get("peAskQty", 0),
            "peTotalBidQty": depth.get("peTotalBidQty", 0),
            "peTotalAskQty": depth.get("peTotalAskQty", 0),
            "peOI": integer(source.get("pe_oi", 0)),
            "peChgOI": integer(source.get("pe_oi_chg", 0)),
            "peVol": integer(source.get("pe_volume", 0)),
            "peIV": nullable_rounded_number(source.get("pe_iv"), 2),
            "peSignal": str(source.get("pe_signal", "")),
        }
        for prefix, key in (
            ("cePremiumLocked", "ce_premium_locked"),
            ("pePremiumLocked", "pe_premium_locked"),
            ("ceNotionalExposure", "ce_notional_exposure"),
            ("peNotionalExposure", "pe_notional_exposure"),
            ("ceCapitalFlow", "ce_capital_flow"),
            ("peCapitalFlow", "pe_capital_flow"),
            ("cePremiumTurnover", "ce_premium_turnover"),
            ("pePremiumTurnover", "pe_premium_turnover"),
            ("ceDeltaExposure", "ce_delta_exposure"),
            ("peDeltaExposure", "pe_delta_exposure"),
            ("ceGammaExposure", "ce_gamma_exposure"),
            ("peGammaExposure", "pe_gamma_exposure"),
        ):
            row[prefix] = nullable_rounded_number(capital.get(key), 2)
        row["footprintScore"] = nullable_rounded_number(
            capital.get("footprint_score"), 1
        )
        row["footprintFactors"] = {
            name: nullable_rounded_number(capital.get(key), 1)
            for name, key in (
                ("capitalActivity", "footprint_pct_capital_activity"),
                ("oiChangeActivity", "footprint_pct_oi_change_activity"),
                ("turnoverActivity", "footprint_pct_turnover_activity"),
                ("gammaActivity", "footprint_pct_gamma_activity"),
                ("deltaActivity", "footprint_pct_delta_activity"),
                ("writingActivity", "footprint_pct_writing_activity"),
            )
        }
        rows.append(row)
    return rows
