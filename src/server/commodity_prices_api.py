"""Read-only live MCX futures quote endpoint."""

from aiohttp import web

from analytics.commodity_option_analysis import analyze_commodity_option_chain


COMMODITY_SYMBOLS = (
    "ALUMINIUM",
    "COPPER",
    "CRUDEOIL",
    "GOLD",
    "GOLDM",
    "LEAD",
    "NATURALGAS",
    "SILVER",
    "SILVERM",
    "ZINC",
)


class CommodityPricesApi:
    def __init__(self, *, market_data=None, active_provider=None):
        self._market_data = (
            market_data if market_data is not None else self._active_market_data
        )
        self._active_provider = (
            active_provider if active_provider is not None else self._get_active_provider
        )

    @staticmethod
    def _active_market_data():
        from brokers.market_data_registry import market_data

        return market_data

    @staticmethod
    def _get_active_provider():
        from brokers.market_data_registry import get_active_provider

        return get_active_provider()

    async def quote(self, request):
        symbol = request.query.get("symbol", "CRUDEOIL").strip().upper()
        if symbol not in COMMODITY_SYMBOLS:
            return web.json_response(
                {"error": "Unsupported commodity symbol."}, status=400
            )

        provider = self._market_data()
        get_quote = getattr(provider, "get_commodity_futures_quote", None)
        provider_name = self._active_provider()
        if not callable(get_quote):
            return web.json_response(
                {
                    "error": (
                        "The active market-data provider does not support MCX "
                        "commodity quotes. Select Angel One SmartAPI."
                    ),
                    "provider": provider_name,
                },
                status=501,
            )

        quote = get_quote(symbol)
        if not quote:
            return web.json_response(
                {
                    "error": f"No live MCX quote available for {symbol}.",
                    "provider": provider_name,
                    "symbol": symbol,
                },
                status=404,
            )
        return web.json_response(
            {"provider": provider_name, "symbol": symbol, "quote": quote}
        )

    async def option_chain(self, request):
        symbol = request.query.get("symbol", "CRUDEOIL").strip().upper()
        if symbol not in COMMODITY_SYMBOLS:
            return web.json_response(
                {"error": "Unsupported commodity symbol."}, status=400
            )

        provider = self._market_data()
        get_expiries = getattr(provider, "get_commodity_option_expiries", None)
        get_chain = getattr(provider, "get_commodity_option_chain", None)
        provider_name = self._active_provider()
        if not callable(get_expiries) or not callable(get_chain):
            return web.json_response(
                {
                    "error": (
                        "The active market-data provider does not support MCX "
                        "commodity options. Select Angel One SmartAPI."
                    ),
                    "provider": provider_name,
                },
                status=501,
            )

        expiries = get_expiries(symbol)
        if not expiries:
            return web.json_response(
                {
                    "error": f"No MCX option expiries found for {symbol}.",
                    "provider": provider_name,
                    "symbol": symbol,
                },
                status=404,
            )
        expiry = request.query.get("expiry", "").strip().upper() or expiries[0]
        if expiry not in expiries:
            return web.json_response(
                {"error": "Expiry is not available for this commodity."},
                status=400,
            )

        chain = get_chain(symbol, expiry, strikes_around_atm=10)
        if not chain:
            return web.json_response(
                {
                    "error": f"No option-chain data available for {symbol}.",
                    "provider": provider_name,
                    "symbol": symbol,
                    "expiry": expiry,
                },
                status=404,
            )
        return web.json_response(
            {
                "provider": provider_name,
                "symbol": symbol,
                "expiry": expiry,
                "expiries": expiries,
                "chain": chain,
                "analysis": analyze_commodity_option_chain(chain),
            }
        )
