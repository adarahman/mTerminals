"""Configuration and execution services for option-chain analytics."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from application.pipeline_config import RuntimeConfig


class AnalyticsPipelineRunner:
    """Run one configured analytics pass and return its captured payload."""

    def __init__(
        self,
        *,
        configure: Callable[[], Any],
        clear_capture: Callable[[], Any],
        invoke: Callable[[Any], Any],
        captured_payload: Callable[[], Any],
    ):
        self._configure = configure
        self._clear_capture = clear_capture
        self._invoke = invoke
        self._captured_payload = captured_payload
        self._consecutive_timeouts = 0

    def run_once(self):
        runtime_config = self._configure()
        self._clear_capture()
        try:
            self._invoke(runtime_config)
        except TimeoutError as exc:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts == 1:
                print(
                    f"[pipeline] warm-up snapshot deferred: {exc}",
                    flush=True,
                )
            else:
                print(f"[pipeline] FAILED: {exc}", flush=True)
            return None
        except Exception as exc:
            print(f"[pipeline] FAILED: {exc}", flush=True)
            return None
        self._consecutive_timeouts = 0
        return self._captured_payload()


class PipelineRuntimeConfigurator:
    """Build and apply one analytics runtime configuration."""

    def __init__(
        self,
        *,
        data_source: Callable[[], str],
        activate_provider: Callable[[str], Any],
        resolve_default_expiry: Callable[[str], str],
        apply_config: Callable[[RuntimeConfig], Any],
    ):
        self._data_source = data_source
        self._activate_provider = activate_provider
        self._resolve_default_expiry = resolve_default_expiry
        self._apply_config = apply_config

    def configure(
        self,
        *,
        symbol: str,
        expiry=None,
        no_extra_chains=None,
        strict_expiry=None,
        no_virtual_oi=None,
        price_source=None,
        futures_expiry=None,
        strikes_each_side=None,
        operation_timeout_seconds=None,
    ) -> RuntimeConfig:
        source = self._data_source()
        self._activate_provider(source)
        config = RuntimeConfig(
            symbol=symbol,
            expiry=expiry or self._resolve_default_expiry(symbol),
            no_extra_chains=no_extra_chains,
            strict_expiry=strict_expiry,
            no_virtual_oi=no_virtual_oi,
            price_source=price_source,
            futures_expiry=futures_expiry,
            strikes_each_side=strikes_each_side,
            use_smartapi=(source != "NSE_BSE"),
            operation_timeout_seconds=operation_timeout_seconds,
        )
        self._apply_config(config)
        return config
