"""Bootstrap process configuration before runtime services are composed."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from market.instruments.lot_sizes import configure_lot_size_resolver
from server.cli_args import build_arg_parser
from server.live_trading_runtime import LiveTradingConfig
from server.runtime_bootstrap import RuntimeBootstrap, initialize_runtime_state
from server.startup_configuration import StartupConfiguration, configure_startup
from server.websocket_security import build_allowed_origins, origin_allowed


@dataclass(frozen=True, slots=True)
class ProcessBootstrap:
    args: Any
    host_process_args: list[str]
    startup: StartupConfiguration
    runtime: RuntimeBootstrap
    live_trading: LiveTradingConfig
    allowed_origins: set[str]
    origin_policy: Callable[..., bool]
    report: Callable[..., Any]


def bootstrap_process(
    *,
    project_root: Path,
    runtime_state: Any,
    broker_services: Any,
    broker_settings: Any,
    instrument_key: Callable[..., str],
    lot_size_resolver: Callable[[str], int],
    supports_websocket: Callable[[str], bool],
    environment: Mapping[str, str] = os.environ,
    parse_args: Callable[[], tuple[Any, list[str]]] | None = None,
    emit: Callable[[str], Any] = lambda message: print(message, flush=True),
) -> ProcessBootstrap:
    """Parse host settings and initialize all pre-composition process state."""
    configure_lot_size_resolver(lot_size_resolver)
    args, host_process_args = (
        parse_args()
        if parse_args is not None
        else build_arg_parser().parse_known_args()
    )
    startup = configure_startup(
        args=args,
        runtime_state=runtime_state,
        broker_services_enabled=broker_services.BROKER_SERVICES_ENABLED,
        live_feed_provider=broker_settings.live_feed_provider,
        activate_provider=broker_services.md_set_active_provider,
        supports_websocket=supports_websocket,
    )
    emit(startup.feed_summary)
    emit(startup.portfolio_summary)
    runtime = initialize_runtime_state(
        runtime_state=runtime_state,
        instrument_key=instrument_key,
        environment=environment,
    )
    live_trading = LiveTradingConfig.from_environment(project_root, environment)
    live_trading.report(emit)
    allowed_origins = build_allowed_origins(
        startup.host,
        startup.http_port,
        environment.get("ALLOWED_ORIGINS", "").split(","),
    )
    return ProcessBootstrap(
        args=args,
        host_process_args=host_process_args,
        startup=startup,
        runtime=runtime,
        live_trading=live_trading,
        allowed_origins=allowed_origins,
        origin_policy=partial(origin_allowed, allowed_origins=allowed_origins),
        report=partial(print, flush=True),
    )
