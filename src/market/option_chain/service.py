"""Provider-neutral option-chain expiry resolution services."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any, Callable

import pandas as pd

from .requests import MarketDataRequestPlan


class ExpiryResolutionService:
    _FORMATS = ("%d-%b-%Y", "%d%b%Y", "%Y-%m-%d")

    @classmethod
    def parse_date(cls, value) -> date | None:
        normalized = str(value or "").strip().upper()
        for date_format in cls._FORMATS:
            try:
                return datetime.strptime(normalized, date_format).date()
            except ValueError:
                continue
        return None

    def resolve_available(
        self,
        requested_expiry: str,
        offered_expiries: Iterable[str],
        *,
        strict: bool = False,
        today: date | None = None,
    ) -> str:
        offered = list(offered_expiries)
        requested_date = self.parse_date(requested_expiry)
        matched = next(
            (
                expiry
                for expiry in offered
                if requested_date is not None
                and self.parse_date(expiry) == requested_date
            ),
            None,
        )
        if matched is not None:
            return matched
        if strict:
            raise RuntimeError(
                f"requested expiry {requested_expiry!r} not available "
                f"(offered: {offered})"
            )
        current_date = today or date.today()
        future = [
            expiry
            for expiry in offered
            if (parsed := self.parse_date(expiry)) is not None
            and parsed >= current_date
        ]
        if not future:
            raise RuntimeError(
                f"no future expiries available (offered: {offered})"
            )
        return future[0]

    def resolve_public_payload(
        self,
        payload: Mapping,
        requested_expiry: str,
        *,
        strict: bool = False,
        today: date | None = None,
    ) -> str:
        records = payload.get("records", {})
        if requested_expiry and records.get("data", []):
            return requested_expiry
        return self.resolve_available(
            requested_expiry,
            records.get("expiryDates", []),
            strict=strict,
            today=today,
        )


class OptionChainFetchService:
    """Fetch and normalize one option chain through injected adapters."""

    def __init__(
        self,
        *,
        canonicalize_symbol: Callable[[str], str],
        fetch_broker_chain: Callable[[str, str, str, int], Any],
        list_broker_expiries: Callable[[str, str], Iterable[str]],
        fetch_public_bse_chain: Callable[[str, str], Any],
        fetch_public_nse_payload: Callable[[str, str], Mapping | None],
        parse_public_nse_payload: Callable[[Mapping, str], Any],
        fetch_bse_quote: Callable[[str], Mapping | None],
        generate_bse_expiries: Callable[[str], Iterable[str]],
        expiry_resolver: ExpiryResolutionService | None = None,
    ) -> None:
        self._canonicalize_symbol = canonicalize_symbol
        self._fetch_broker_chain = fetch_broker_chain
        self._list_broker_expiries = list_broker_expiries
        self._fetch_public_bse_chain = fetch_public_bse_chain
        self._fetch_public_nse_payload = fetch_public_nse_payload
        self._parse_public_nse_payload = parse_public_nse_payload
        self._fetch_bse_quote = fetch_bse_quote
        self._generate_bse_expiries = generate_bse_expiries
        self._expiry_resolver = expiry_resolver or ExpiryResolutionService()

    def fetch(self, request: MarketDataRequestPlan, *, strikes_each_side: int):
        symbol = self._canonicalize_symbol(request.symbol)
        if request.option_exchange == "BSE":
            frame = (
                self._fetch_broker_chain(
                    symbol,
                    request.option_expiry,
                    request.broker_derivatives_exchange,
                    strikes_each_side,
                )
                if request.broker_enabled
                else self._fetch_public_bse_chain(
                    symbol, request.option_expiry
                )
            )
            self._require_frame(frame, symbol, request.option_expiry)
            frame, spot = self._recover_bse_spot(frame, symbol)
            return (
                frame,
                spot,
                list(self._generate_bse_expiries(symbol)),
            )

        if request.broker_enabled:
            offered = list(
                self._list_broker_expiries(
                    symbol, request.broker_derivatives_exchange
                )
            )
            resolved = self._expiry_resolver.resolve_available(
                request.option_expiry,
                offered,
                strict=request.strict_expiry,
            )
            frame = self._fetch_broker_chain(
                symbol,
                resolved,
                request.broker_derivatives_exchange,
                strikes_each_side,
            )
        else:
            payload = self._fetch_public_nse_payload(
                symbol, request.option_expiry
            )
            if not payload:
                raise RuntimeError(f"NSE API returned no data for {symbol}")
            offered = payload.get("records", {}).get("expiryDates", [])
            resolved = self._expiry_resolver.resolve_public_payload(
                payload,
                request.option_expiry,
                strict=request.strict_expiry,
            )
            if resolved != request.option_expiry:
                payload = self._fetch_public_nse_payload(symbol, resolved)
                if not payload:
                    raise RuntimeError(
                        f"NSE API returned no data for {symbol} {resolved}"
                    )
            frame = self._parse_public_nse_payload(payload, resolved)

        self._require_frame(frame, symbol, resolved)
        spot = frame["Spot"].iloc[0] if "Spot" in frame.columns else 0.0
        return frame, spot, resolved, list(offered)

    @staticmethod
    def _require_frame(frame, symbol: str, expiry: str) -> None:
        if frame is None or getattr(frame, "empty", True):
            raise RuntimeError(
                f"option chain fetch empty for {symbol} {expiry}"
            )

    def _recover_bse_spot(self, frame, symbol: str):
        spot = frame["Spot"].iloc[0] if "Spot" in frame.columns else 0.0
        try:
            valid_spot = pd.notna(spot) and float(spot) > 0
        except (TypeError, ValueError):
            valid_spot = False
        if valid_spot:
            return frame, spot
        quote = self._fetch_bse_quote(symbol)
        try:
            recovered = float(quote.get("Last Price")) if quote else 0.0
        except (TypeError, ValueError):
            recovered = 0.0
        if recovered <= 0:
            return frame, spot
        frame = frame.copy()
        frame["Spot"] = recovered
        return frame, recovered
