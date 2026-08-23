"""
exceptions.py
=============
Custom exception hierarchy for the backend.

Before this, failure modes that are meaningfully different from each
other (a broker rejecting a login, a pricing model getting bad inputs,
the market being closed, an order being rejected) were all raised as
bare RuntimeError/ValueError with only the message text to tell them
apart. That's fine for a human reading a traceback, but it means calling
code that wants to react differently to "market's closed, retry later"
vs. "credentials are wrong, stop retrying" has nothing to `except` on
other than string-matching the message.

Catch BackendError to handle "any of ours" without swallowing unrelated
bugs (a KeyError from a typo isn't a BackendError and shouldn't be
treated as one). Catch a specific subclass when the caller needs to
react differently depending on what went wrong.

    from exceptions import AuthenticationError, MarketClosedError

    try:
        client.get_quote(...)
    except MarketClosedError:
        ...  # expected, not a bug -- wait for next session
    except AuthenticationError:
        ...  # credentials/session problem, don't just retry
    except BackendError:
        ...  # some other broker/pricing/data failure
"""


class BackendError(Exception):
    """Base class for all custom exceptions raised by this backend.

    Not meant to be raised directly -- raise one of the subclasses below,
    or add a new one here if none fit, so callers can catch by category.
    """


class BrokerError(BackendError):
    """A broker-side operation failed: order placement/modification/
    cancellation, session/API call, or any other broker-API failure that
    isn't specifically an auth or market-hours problem (see
    AuthenticationError / MarketClosedError for those)."""


class AuthenticationError(BrokerError):
    """Broker login/session failed, or required credentials are missing
    or invalid. A subclass of BrokerError since it's still fundamentally
    a broker-API failure, but callers commonly want to catch this one
    specifically (e.g. to stop retrying and surface a config problem
    instead of treating it as a transient failure)."""


class MarketClosedError(BackendError):
    """An operation was attempted while the relevant market/session is
    closed (e.g. quote/order requests outside trading hours). Distinct
    from BrokerError because this is an expected, schedule-driven
    condition rather than a failure -- callers typically want to wait
    rather than retry-with-backoff or alert."""


class PricingError(BackendError):
    """Pricing/Greeks computation failed or received invalid inputs
    (e.g. Black-Scholes given a non-positive time-to-expiry or IV that
    didn't converge)."""


class DataUnavailableError(BackendError):
    """Requested market/reference data (quote, option chain, instrument
    lookup, history) isn't available -- e.g. an unresolvable symbol, an
    empty upstream response, or a cache/history file that doesn't exist
    yet. Distinct from BrokerError: the broker call itself succeeded,
    it's the data that's missing or unusable."""
