"""Construction boundary for live-trading and supervisory services."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from decision.auto_executor import AutoExecutor
from risk.account_guard import LiveAccountRiskGuard
from risk.live_order_store import LiveOrderStore
from risk.position_reconciler import PositionReconciler
from server.order_gateway import LiveOrderGateway
from server.order_submission import OrderSubmissionService
from server.trading_supervision import LiveTradingSupervisor


@dataclass(frozen=True, slots=True)
class LiveTradingConfig:
    enabled: bool
    kill_switch_file: str
    max_lots_per_order: int
    max_orders_per_minute: int
    reconcile_seconds: int

    @classmethod
    def from_environment(
        cls, project_root: Path, environment: Mapping[str, str] = os.environ
    ) -> LiveTradingConfig:
        return cls(
            enabled=environment.get("LIVE_TRADING_ENABLED", "").strip().lower()
            == "true",
            kill_switch_file=str(project_root / "LIVE_TRADING_KILL"),
            max_lots_per_order=int(environment.get("LIVE_MAX_LOTS_PER_ORDER", "1")),
            max_orders_per_minute=int(
                environment.get("LIVE_MAX_ORDERS_PER_MINUTE", "5")
            ),
            reconcile_seconds=int(environment.get("POSITION_RECONCILE_SECONDS", "120")),
        )

    def report(self, emit: Callable[[str], Any]) -> None:
        if self.enabled:
            emit(
                f"[live-trading] ENABLED — max {self.max_lots_per_order} "
                f"lot(s)/order, {self.max_orders_per_minute}/min. Kill switch: "
                f"touch {self.kill_switch_file} to disable instantly."
            )
            return
        emit(
            "[live-trading] disabled (paper trading only) — set "
            "LIVE_TRADING_ENABLED=true to enable"
        )


class LiveOrderTokenResolver:
    """Resolve dashboard option identities into execution-broker contracts."""

    def __init__(
        self,
        *,
        bse_symbols: set[str],
        resolve_option_contract: Callable[..., Any] | None,
        find_option_token: Callable[..., Any],
    ) -> None:
        self._bse_symbols = bse_symbols
        self._resolve_option_contract = resolve_option_contract
        self._find_option_token = find_option_token

    def resolve(self, symbol, instrument_type, expiry, strike):
        exchange = "BFO" if symbol in self._bse_symbols else "NFO"
        if instrument_type not in ("CE", "PE"):
            return None
        if self._resolve_option_contract is not None:
            return self._resolve_option_contract(
                symbol, expiry, strike, instrument_type, exchange
            )
        try:
            normalized_expiry = (
                datetime.strptime(expiry, "%d-%b-%Y").strftime("%d%b%Y").upper()
            )
        except (ValueError, TypeError):
            return None
        resolved = self._find_option_token(
            symbol, normalized_expiry, strike, instrument_type, exchange
        )
        if not resolved:
            return None
        return exchange, resolved["tradingsymbol"], resolved["token"]


@dataclass(frozen=True, slots=True)
class LiveTradingRuntime:
    config: LiveTradingConfig
    account_guard: LiveAccountRiskGuard
    position_reconciler: PositionReconciler
    orders: LiveOrderGateway
    submission: OrderSubmissionService
    auto_executor: AutoExecutor
    supervisor: LiveTradingSupervisor
    resolve_token: Callable[..., Any]


def build_live_trading_runtime(
    *,
    config: LiveTradingConfig,
    bse_symbols: set[str],
    resolve_option_contract: Callable[..., Any] | None,
    find_option_token: Callable[..., Any],
    place_order: Callable[..., Any],
    get_positions: Callable[..., Any],
    get_order_book: Callable[..., Any],
    lot_sizes: Mapping[str, int],
    paper_engine: Any,
    price_book: Any,
    portfolio_broadcast: Callable[..., Awaitable[Any]],
    last_payload: Callable[[], Any],
    instrument_key: Callable[..., str],
    cached_positions: Callable[[], Any],
    symbol: Callable[[], str],
    broker_label: Callable[[], str],
    store_alert: Callable[[Any], Any],
    broadcast: Callable[..., Awaitable[Any]],
    report: Callable[..., Any],
) -> LiveTradingRuntime:
    account_guard = LiveAccountRiskGuard(config.kill_switch_file)
    reconciler = PositionReconciler(config.kill_switch_file)
    resolver = LiveOrderTokenResolver(
        bse_symbols=bse_symbols,
        resolve_option_contract=resolve_option_contract,
        find_option_token=find_option_token,
    )
    orders = LiveOrderGateway(
        enabled=config.enabled,
        kill_switch_file=config.kill_switch_file,
        max_lots_per_order=config.max_lots_per_order,
        max_orders_per_minute=config.max_orders_per_minute,
        lot_sizes=lot_sizes,
        account_guard=account_guard,
        position_reconciler=reconciler,
        resolve_token=resolver.resolve,
        place_order=place_order,
        get_positions=get_positions,
        get_order_book=get_order_book,
        order_store=LiveOrderStore(max_entries=500),
    )
    supervisor_holder: dict[str, LiveTradingSupervisor] = {}
    submission = OrderSubmissionService(
        live_orders=orders,
        paper_engine=paper_engine,
        price_book=price_book,
        portfolio_broadcast=portfolio_broadcast,
        reconciliation_alert=lambda result, source: supervisor_holder[
            "supervisor"
        ].publish_reconciliation_alert(result, source),
        last_payload=last_payload,
        instrument_key=instrument_key,
        report=report,
    )
    auto_executor = AutoExecutor(account_guard, submission.submit_auto)
    supervisor = LiveTradingSupervisor(
        account_guard=account_guard,
        auto_executor=auto_executor,
        live_orders=orders,
        reconciler=reconciler,
        lot_sizes=lot_sizes,
        cached_positions=cached_positions,
        symbol=symbol,
        broker_label=broker_label,
        live_trading_enabled=config.enabled,
        max_lots_per_order=config.max_lots_per_order,
        max_orders_per_minute=config.max_orders_per_minute,
        store_alert=store_alert,
        broadcast=broadcast,
        report=report,
    )
    supervisor_holder["supervisor"] = supervisor
    return LiveTradingRuntime(
        config=config,
        account_guard=account_guard,
        position_reconciler=reconciler,
        orders=orders,
        submission=submission,
        auto_executor=auto_executor,
        supervisor=supervisor,
        resolve_token=resolver.resolve,
    )
