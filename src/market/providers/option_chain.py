"""Canonical public NSE/BSE option-chain and context adapter."""
from __future__ import annotations

import pandas as pd

from market.providers import nse_bse_client


class PublicOptionChainAdapter:
    bse_symbols = tuple(nse_bse_client.BSE_INDEX_SCRIP_CODES)

    def fetch_nse_payload(self, symbol: str, expiry: str):
        return nse_bse_client.fetch_option_chain(symbol, expiry)

    def parse_nse_payload(self, payload: dict, expiry: str):
        return nse_bse_client.parse_option_chain_response(payload, expiry)

    def fetch_bse_chain(self, symbol: str, expiry: str):
        scrip_code = nse_bse_client.BSE_INDEX_SCRIP_CODES.get(symbol)
        if not scrip_code:
            raise RuntimeError(f"No BSE scrip code for {symbol}")
        expiry_bse = pd.to_datetime(expiry, format="%d-%b-%Y").strftime(
            "%d %b %Y"
        )
        frame, spot = nse_bse_client.fetch_bse_json_options(
            expiry_bse, scrip_cd=scrip_code
        )
        if frame is None or frame.empty:
            return pd.DataFrame()
        frame = frame.rename(columns={"Strike": "StrikePrice"})
        frame["Expiry"] = expiry
        frame["Spot"] = spot
        frame["Symbol"] = symbol
        for side in ("CE", "PE"):
            for field in ("PctChgOI", "pChange", "BuyQty", "SellQty"):
                column = f"{side}_{field}"
                if column not in frame.columns:
                    frame[column] = 0
        return frame

    def fetch_bse_quote(self, symbol: str):
        return nse_bse_client.fetch_bse_index_quote(symbol)

    def fetch_futures(self, symbol: str, which: str):
        return nse_bse_client.fetch_public_futures(symbol, which)

    def fetch_indices(self):
        return nse_bse_client.fetch_all_indices()

    def unified_market_data(self, indices):
        return nse_bse_client.get_unified_market_data(indices)
