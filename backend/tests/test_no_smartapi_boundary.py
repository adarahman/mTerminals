"""Regression coverage for the strict --no-smartapi process boundary."""

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_no_smartapi_does_not_import_broker_client_or_adapter():
    probe = (
        'import sys; '
        'sys.argv=["ws_server_live.py","--no-smartapi"]; '
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
