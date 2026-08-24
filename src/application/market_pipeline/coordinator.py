"""
Thin application market pipeline coordinator.

No broker logic.
No parsing.
No calculations.

Only orchestration.
"""


class MarketPipelineCoordinator:

    def __init__(
        self,
        option_chain_service,
        futures_service,
        quote_service,
    ):
        self.option_chain_service = option_chain_service
        self.futures_service = futures_service
        self.quote_service = quote_service


    def fetch_option_chain(
        self,
        *args,
        **kwargs,
    ):
        return self.option_chain_service.fetch(
            *args,
            **kwargs,
        )


    def fetch_futures(
        self,
        *args,
        **kwargs,
    ):
        return self.futures_service.fetch(
            *args,
            **kwargs,
        )


    def fetch_quotes(
        self,
        *args,
        **kwargs,
    ):
        return self.quote_service.fetch(
            *args,
            **kwargs,
        )