"""Read-only live CDS currency-futures quotes."""

import asyncio

from aiohttp import web


CURRENCY_SYMBOLS = frozenset({"USDINR", "EURINR", "GBPINR", "JPYINR"})


class CurrencyRatesApi:
    def __init__(self, market_data=None, active_provider=None):
        self._market_data = market_data or self._active_market_data
        self._active_provider = active_provider or self._get_active_provider

    @staticmethod
    def _active_market_data():
        from brokers.market_data_registry import market_data
        return market_data

    @staticmethod
    def _get_active_provider():
        from brokers.market_data_registry import get_active_provider
        return get_active_provider()

    async def quote(self, request):
        symbol = request.query.get("symbol", "USDINR").strip().upper()
        if symbol not in CURRENCY_SYMBOLS:
            return web.json_response({"error": "Unsupported currency pair."}, status=400)
        provider_name = self._active_provider()
        provider = self._market_data()
        get_quote = getattr(provider, "get_currency_futures_quote", None)
        if not callable(get_quote):
            return web.json_response({
                "error": "The active market-data provider has no CDS quote adapter configured.",
                "provider": provider_name,
            }, status=501)
        which = request.query.get("which", "NEAR").strip().upper()
        if which not in {"NEAR", "NEXT", "FAR"}:
            return web.json_response({"error": "Invalid futures expiry slot."}, status=400)
        quote = await asyncio.to_thread(get_quote, symbol, which)
        if not quote:
            return web.json_response({
                "error": f"No live CDS quote available for {symbol}.",
                "provider": provider_name,
                "symbol": symbol,
            }, status=404)
        return web.json_response({"provider": provider_name, "symbol": symbol, "quote": quote})

    async def option_chain(self, request):
        symbol = request.query.get("symbol", "USDINR").strip().upper()
        if symbol not in CURRENCY_SYMBOLS:
            return web.json_response({"error": "Unsupported currency pair."}, status=400)
        provider_name = self._active_provider()
        provider = self._market_data()
        get_chain = getattr(provider, "get_currency_option_chain", None)
        if not callable(get_chain):
            return web.json_response({
                "error": "The active market-data provider has no CDS option-chain adapter configured.",
                "provider": provider_name,
            }, status=501)
        expiry = request.query.get("expiry")
        which = request.query.get("which", "NEAR").strip().upper()
        if which not in {"NEAR", "NEXT", "FAR"}:
            return web.json_response({"error": "Invalid futures expiry slot."}, status=400)
        chain = await asyncio.to_thread(get_chain, symbol, expiry=expiry, which=which)
        if not chain or not chain.get("rows"):
            return web.json_response({
                "error": f"No CDS option-chain quotes available for {symbol}.",
                "provider": provider_name,
                "symbol": symbol,
                "expiry": expiry,
                "expiries": (chain or {}).get("expiries", []),
            }, status=404)
        return web.json_response({"provider": provider_name, "symbol": symbol, "chain": chain})
