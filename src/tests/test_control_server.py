import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[2] / "scripts" / "control_server.py"
SPEC = importlib.util.spec_from_file_location("control_server", MODULE_PATH)
control_server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(control_server)


def test_rejects_invalid_symbol_without_starting_process(monkeypatch):
    supervisor = control_server.BackendSupervisor()
    monkeypatch.setattr(control_server.subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not start"))

    with pytest.raises(ValueError, match="valid symbol"):
        supervisor.start("NIFTY; rm")


def test_control_page_contains_minimum_launcher_controls():
    page = control_server.CONTROL_PAGE
    assert 'id="symbol"' in page
    assert 'id="expiry"' in page
    assert 'class="selection-grid"' in page
    assert 'id="broker"' in page
    assert 'id="credentials"' in page
    assert 'id="modify-credentials"' in page
    assert '<option value="no" selected>No</option>' in page
    assert 'id="start"' in page
    assert 'id="stop"' in page
    assert "Start Backend" in page
    assert "mTerminals Launcher" in page


def test_start_and_stop_manage_only_the_owned_backend(monkeypatch, tmp_path):
    calls = []

    class FakeProcess:
        pid = 4321
        returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            calls.append("terminate")
            self.returncode = 0

        def wait(self, timeout):
            calls.append(("wait", timeout))
            return self.returncode

        def kill(self):
            calls.append("kill")
            self.returncode = -9

    def fake_popen(command, **kwargs):
        calls.append(("popen", command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(control_server, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(control_server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        control_server.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not listening")),
    )
    supervisor = control_server.BackendSupervisor(backend_port=5599)

    started = supervisor.start("NIFTY", "2026-09-08", "UPSTOX", {"UPSTOX_ACCESS_TOKEN": "fresh-token"})
    assert started["running"] is True
    command = calls[0][1]
    assert command[-4:] == ["--symbol", "NIFTY", "--expiry", "08-Sep-2026"]
    assert "--http-port" in command and "5599" in command
    assert calls[0][2]["env"]["MARKET_DATA_PROVIDER"] == "UPSTOX"
    assert calls[0][2]["env"]["LIVE_FEED_PROVIDER"] == "UPSTOX"
    env_text = (tmp_path / ".env").read_text()
    assert "MARKET_DATA_PROVIDER=UPSTOX" in env_text
    assert "LIVE_FEED_PROVIDER=UPSTOX" in env_text
    assert "UPSTOX_ACCESS_TOKEN=fresh-token" in env_text

    stopped = supervisor.stop()
    assert stopped["running"] is False
    assert "terminate" in calls
    assert "kill" not in calls


def test_update_env_preserves_blank_credentials_and_other_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(control_server, "PROJECT_ROOT", tmp_path)
    env_path = tmp_path / ".env"
    env_path.write_text("KEEP_ME=yes\nUPSTOX_ACCESS_TOKEN=old-token\n")
    control_server.update_env({"MARKET_DATA_PROVIDER": "UPSTOX", "UPSTOX_ACCESS_TOKEN": ""})
    text = env_path.read_text()
    assert "KEEP_ME=yes" in text
    assert "UPSTOX_ACCESS_TOKEN=old-token" in text
    assert "MARKET_DATA_PROVIDER=UPSTOX" in text


def test_rejects_credentials_for_a_different_broker():
    supervisor = control_server.BackendSupervisor()
    with pytest.raises(ValueError, match="Unexpected broker credential"):
        supervisor.start("NIFTY", broker="UPSTOX", credentials={"SMARTAPI_PIN": "1234"})


def test_update_env_quotes_special_characters_and_rejects_newlines(monkeypatch, tmp_path):
    monkeypatch.setattr(control_server, "PROJECT_ROOT", tmp_path)
    control_server.update_env({"SHOONYA_PASSWORD": "secret #1"})
    assert "SHOONYA_PASSWORD='secret #1'" in (tmp_path / ".env").read_text()
    with pytest.raises(ValueError, match="new line"):
        control_server.update_env({"SHOONYA_PASSWORD": "secret\nINJECTED=yes"})
