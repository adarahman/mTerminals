"""Regression coverage for the strict broker-free process boundary."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def test_public_only_mode_does_not_import_broker_client_or_adapter():
    probe = (
        'import os, sys; '
        'os.environ["BROKER_SERVICES_ENABLED"]="false"; '
        'import ws_server_live as server; '
        'assert server.USE_SMARTAPI is False; '
        'assert "brokers.smartapi_client" not in sys.modules; '
        'assert "smartapi_pipeline_adapter" not in sys.modules'
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Logged in, session established" not in result.stdout + result.stderr


def test_project_dotenv_overrides_stale_parent_env():
    """Regression: load_dotenv(..., override=True) ensures .env file values
    override inherited shell environment, preventing stale daily tokens or old
    broker selections from silently breaking production deployments."""
    env = dict(os.environ)
    # Set stale Shoonya values in the parent shell environment
    env["EXECUTION_BROKER"] = "SHOONYA"
    env["LIVE_FEED_PROVIDER"] = "SHOONYA"
    env["MARKET_DATA_PROVIDER"] = "SHOONYA"
    # But the .env file has KITE as the authoritative value (this repo's
    # current provider setup — KITE access token is deliberately left
    # empty in .env so the runtime DATA SOURCE falls back to NSE/BSE until
    # a token is pasted; see ws_server_live._resolve_default_data_source()).
    probe = (
        'import sys; '
        'sys.path.insert(0, "backend"); '
        'import config; '
        'assert config.settings.execution_broker == "KITE", f"Got {config.settings.execution_broker}"; '
        'assert config.settings.live_feed_provider == "KITE", f"Got {config.settings.live_feed_provider}"; '
        'assert config.settings.market_data_provider == "KITE", f"Got {config.settings.market_data_provider}"; '
        'print("OK")'
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout
