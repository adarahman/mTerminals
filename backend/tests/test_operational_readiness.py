import json

import operational_readiness as readiness


def test_preflight_reports_missing_credentials_and_writable_runtime(tmp_path):
    result = readiness.validate_environment(env={}, runtime_dir=tmp_path)
    assert result["ok"] is False and "SMARTAPI_KEY" in result["errors"][0]


def test_preflight_allows_explicit_rest_only_mode(tmp_path):
    result = readiness.validate_environment(
        env={}, runtime_dir=tmp_path, require_smartapi=False
    )
    assert result["ok"] is True and result["warnings"] == []


def test_preflight_warns_when_live_trading_is_enabled(tmp_path):
    env = {name: "configured" for name in readiness.REQUIRED_SMARTAPI_VARS}
    env["LIVE_TRADING_ENABLED"] = "true"
    result = readiness.validate_environment(env=env, runtime_dir=tmp_path)
    assert result["ok"] is True and "live trading is enabled" in result["warnings"][0]


def test_smoke_health_accepts_only_healthy_200(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr(readiness, "urlopen", lambda *_args, **_kwargs: Response())
    monkeypatch.setattr(json, "load", lambda _response: {"status": "ok", "reasons": []})
    assert readiness.smoke_health("http://localhost:5500")["ok"] is True
