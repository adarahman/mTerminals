"""Preflight and smoke checks for an mTerminals deployment."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from dotenv import load_dotenv

REQUIRED_SMARTAPI_VARS = (
    "SMARTAPI_KEY",
    "SMARTAPI_CLIENT_CODE",
    "SMARTAPI_PIN",
    "SMARTAPI_TOTP_SECRET",
)


def load_deployment_environment():
    """Load the same supported .env locations as the application."""
    project_root = Path(__file__).resolve().parents[1]
    for candidate in (project_root / "backend" / ".env", project_root / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return str(candidate)
    return None


def validate_environment(env=None, runtime_dir=None, require_smartapi=True):
    env = os.environ if env is None else env
    errors, warnings = [], []
    if require_smartapi:
        missing = [
            name
            for name in REQUIRED_SMARTAPI_VARS
            if not str(env.get(name, "")).strip()
        ]
        if missing:
            errors.append("missing required SmartAPI settings: " + ", ".join(missing))
    if str(env.get("LIVE_TRADING_ENABLED", "")).strip().lower() == "true":
        warnings.append(
            "live trading is enabled; confirm account limits and kill-switch access"
        )
    target = Path(
        runtime_dir
        or env.get("RUNTIME_DIR")
        or Path(__file__).resolve().parents[1] / "runtime"
    )
    try:
        target.mkdir(parents=True, exist_ok=True)
        probe = target / ".mterminals-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        errors.append(f"runtime directory is not writable: {target} ({exc})")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "runtimeDir": str(target),
    }


def check_port_available(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, port))
        return None
    except OSError as exc:
        return f"cannot bind {host}:{port}: {exc}"


def smoke_health(base_url, timeout=10.0):
    url = base_url.rstrip("/") + "/health"
    try:
        with urlopen(url, timeout=timeout) as response:
            body, status = json.load(response), response.status
    except HTTPError as exc:
        status = exc.code
        try:
            body = json.load(exc)
        except (json.JSONDecodeError, TypeError):
            body = {}
    except (URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "url": url, "error": str(exc)}
    service_status = body.get("status")
    return {
        "ok": status == 200 and service_status == "ok",
        "url": url,
        "httpStatus": status,
        "serviceStatus": service_status,
        "reasons": body.get("reasons", []),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--runtime-dir")
    preflight.add_argument("--host", default="127.0.0.1")
    preflight.add_argument("--http-port", type=int, default=5500)
    preflight.add_argument("--skip-port-check", action="store_true")
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--base-url", default="http://127.0.0.1:5500")
    smoke.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        load_deployment_environment()
        broker_services_enabled = os.getenv(
            "BROKER_SERVICES_ENABLED", "true"
        ).strip().lower() in {"1", "true", "yes", "on"}
        result = validate_environment(
            runtime_dir=args.runtime_dir,
            require_smartapi=broker_services_enabled,
        )
        if not args.skip_port_check:
            error = check_port_available(args.host, args.http_port)
            if error:
                result["errors"].append(error)
                result["ok"] = False
    else:
        result = smoke_health(args.base_url, args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
