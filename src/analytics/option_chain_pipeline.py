"""Canonical analytics boundary for option-chain pipeline execution."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from application.pipeline_config import RuntimeConfig


class OptionChainPipeline:
    """Execute one option-chain analytics pass from immutable inputs."""

    def __init__(
        self, *, implementation: Callable[[RuntimeConfig], Any]
    ) -> None:
        self._implementation = implementation

    def run(self, runtime_config: RuntimeConfig):
        if not isinstance(runtime_config, RuntimeConfig):
            raise TypeError("runtime_config must be a RuntimeConfig")
        return self._implementation(runtime_config)
