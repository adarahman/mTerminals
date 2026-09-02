#!/usr/bin/env python3
"""Lightweight local launcher for the mTerminals analytics backend.

Run this small supervisor instead of starting ``python -m main`` directly.
The control page remains available while the market backend is stopped.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import signal
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_URL = "http://127.0.0.1:5500"
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9 .&_-]{0,63}$")
DATA_PROVIDERS = {"SMARTAPI", "UPSTOX", "KITE", "SHOONYA", "BREEZE", "KOTAK", "NSE_BSE"}
BROKER_CREDENTIALS = {
    "SMARTAPI": (("SMARTAPI_KEY", "API key"), ("SMARTAPI_CLIENT_CODE", "Client code"), ("SMARTAPI_PIN", "PIN"), ("SMARTAPI_TOTP_SECRET", "TOTP secret")),
    "UPSTOX": (("UPSTOX_ACCESS_TOKEN", "Daily access token"),),
    "KITE": (("KITE_API_KEY", "API key"), ("KITE_ACCESS_TOKEN", "Daily access token")),
    "SHOONYA": (("SHOONYA_USER_ID", "User ID"), ("SHOONYA_PASSWORD", "Password"), ("SHOONYA_TOTP_SECRET", "TOTP secret"), ("SHOONYA_VENDOR_CODE", "Vendor code"), ("SHOONYA_API_SECRET", "API secret")),
    "BREEZE": (("BREEZE_API_KEY", "API key"), ("BREEZE_API_SECRET", "API secret"), ("BREEZE_API_SESSION", "Daily API session")),
    "KOTAK": (("KOTAK_CONSUMER_KEY", "Consumer key"), ("KOTAK_MOBILE", "Mobile number"), ("KOTAK_UCC", "UCC"), ("KOTAK_TOTP_SECRET", "TOTP secret"), ("KOTAK_MPIN", "MPIN")),
    "NSE_BSE": (),
}


def _env_path() -> Path:
    """Match the backend's .env precedence."""
    source_env = PROJECT_ROOT / "src" / ".env"
    return source_env if source_env.is_file() else PROJECT_ROOT / ".env"


def update_env(values: dict[str, str]) -> None:
    """Update selected keys without exposing or disturbing other .env entries."""
    path = _env_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending: dict[str, str] = {}
    for key, value in values.items():
        value = str(value).strip()
        if not value:
            continue
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} cannot contain a new line.")
        if not re.fullmatch(r"[A-Za-z0-9_./:+-]+", value):
            value = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        pending[key] = value
    updated: list[str] = []
    for line in lines:
        match = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*=", line)
        key = match.group(1) if match else None
        updated.append(f"{key}={pending.pop(key)}" if key in pending else line)
    if pending and updated and updated[-1]:
        updated.append("")
    updated.extend(f"{key}={value}" for key, value in pending.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.launcher.tmp")
    temporary.write_text("\n".join(updated) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


CONTROL_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>mTerminals Control</title><style>
:root{color-scheme:dark;font-family:Inter,system-ui,sans-serif;background:#07101f;color:#e8eef8}*{box-sizing:border-box}[hidden]{display:none!important}
body{margin:0;height:100vh;height:100dvh;padding:14px;display:grid;place-items:center;overflow:hidden;background:radial-gradient(circle at top,#12233e,#07101f 55%)}
main{width:min(620px,100%);max-height:calc(100vh - 28px);max-height:calc(100dvh - 28px);display:flex;flex-direction:column;border:1px solid #263b59;border-radius:18px;background:#0c1729;box-shadow:0 24px 70px #0008;overflow:hidden}
.header{padding:20px 22px 0;flex:none}.form-scroll{padding:0 22px 18px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable}.footer{padding:12px 22px 18px;border-top:1px solid #263b59;background:#0c1729;box-shadow:0 -10px 24px #07101fcc;flex:none}
h1{font-size:22px;margin:0 0 4px}.sub{color:#94a7c3;margin:0 0 14px}.status{display:flex;align-items:center;gap:9px;padding:10px 12px;border-radius:10px;background:#101f35;margin-bottom:4px}
.dot{width:10px;height:10px;border-radius:50%;background:#64748b}.dot.ready{background:#22c55e}.dot.starting{background:#f59e0b}.dot.stopped{background:#ef4444}
label{display:block;color:#aebdd2;font-size:12px;margin:11px 0 5px}input,select{width:100%;padding:9px 11px;border:1px solid #314662;border-radius:9px;background:#081321;color:#f5f8fc;font-size:14px}
.selection-grid,.credential-grid{display:grid;grid-template-columns:1fr 1fr;column-gap:12px}.credentials{margin-top:12px;padding:10px 12px 12px;border:1px solid #263b59;border-radius:10px}.credentials legend{color:#aebdd2;font-size:12px}.hint{color:#7186a4;font-size:11px;margin:7px 0 0}
.actions{display:grid;grid-template-columns:1fr 1fr;gap:10px}button,a.button{border:0;border-radius:9px;padding:11px;text-align:center;font-weight:700;text-decoration:none;cursor:pointer}
#start{background:#22c55e;color:#052e16}#stop{background:#ef4444;color:white}.button{display:block;margin-top:8px;background:#2563eb;color:white}.button.disabled{opacity:.4;pointer-events:none}
button:disabled{opacity:.4;cursor:not-allowed}.msg{min-height:16px;margin-top:7px;color:#fbbf24;font-size:12px}
@media(max-width:520px){body{padding:0;place-items:stretch}main{width:100%;max-height:100vh;max-height:100dvh;border:0;border-radius:0}.header{padding:14px 16px 0}.form-scroll{padding:0 16px 12px}.footer{padding:10px 16px 12px}.selection-grid,.credential-grid{grid-template-columns:1fr}h1{font-size:19px}.sub{font-size:13px}}
@media(max-height:650px){.header{padding-top:12px}.sub{display:none}.status{margin-top:5px}.form-scroll{padding-bottom:10px}label{margin-top:8px}.footer{padding-top:9px;padding-bottom:9px}}
</style></head><body><main>
<div class="header"><h1>mTerminals Launcher</h1><p class="sub">Start the market engine only when you need it.</p>
<div class="status"><span id="dot" class="dot stopped"></span><strong id="status">Backend stopped</strong></div>
</div><div class="form-scroll">
<div class="selection-grid"><div><label for="symbol">Symbol</label><input id="symbol" list="symbols" value="NIFTY" autocomplete="off">
<datalist id="symbols"><option>NIFTY</option><option>BANKNIFTY</option><option>FINNIFTY</option><option>MIDCPNIFTY</option><option>SENSEX</option><option>BANKEX</option></datalist>
 </div><div><label for="expiry">Expiry (blank = nearest)</label><input id="expiry" type="date"></div></div>
<label for="broker">Market-data broker</label><select id="broker">
<option value="SMARTAPI">Angel One (SmartAPI)</option><option value="UPSTOX">Upstox</option>
<option value="KITE">Zerodha (Kite)</option><option value="SHOONYA">Shoonya</option>
<option value="BREEZE">ICICI Direct (Breeze)</option><option value="KOTAK">Kotak Neo</option>
<option value="NSE_BSE">NSE/BSE public data</option></select>
<fieldset id="credentials" class="credentials"><legend>Broker credentials</legend>
<label for="modify-credentials">Modify credentials?</label><select id="modify-credentials"><option value="no" selected>No</option><option value="yes">Yes</option></select>
<div id="credential-fields" class="credential-grid" hidden></div><p id="credential-hint" class="hint" hidden>Leave a field blank to keep its existing value in .env.</p></fieldset>
<p style="margin:7px 0 0;color:#7186a4;font-size:11px">Live-order execution broker remains the protected <code>EXECUTION_BROKER</code> configured in .env.</p>
</div><div class="footer">
<div class="actions"><button id="start">Start Backend</button><button id="stop">Stop Backend</button></div>
<a id="dashboard" class="button disabled" href="http://127.0.0.1:5500/dist/Dashboard/DashboardPro.html" target="_blank" rel="noopener">Open Dashboard</a>
<div id="msg" class="msg"></div>
</div></main><script>
const $=id=>document.getElementById(id);let busy=false;
const credentialSchema=__CREDENTIAL_SCHEMA__;
function drawCredentials(){const fields=$('credential-fields');fields.replaceChildren();const schema=credentialSchema[$('broker').value]||[];$('credentials').style.display=schema.length?'block':'none';const open=$('modify-credentials').value==='yes'&&schema.length>0;fields.hidden=!open;$('credential-hint').hidden=!open;if(!open)return;for(const [key,label] of schema){const group=document.createElement('div');const l=document.createElement('label');l.htmlFor='cred-'+key;l.textContent=label;const input=document.createElement('input');input.id='cred-'+key;input.dataset.envKey=key;input.type='password';input.autocomplete='off';input.placeholder='Existing value remains if blank';group.append(l,input);fields.append(group)}}
function credentials(){return Object.fromEntries([...document.querySelectorAll('[data-env-key]')].map(input=>[input.dataset.envKey,input.value]))}
async function request(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw new Error(d.error||'Request failed');return d}
function paint(s){$('status').textContent=s.ready?'Backend ready':s.running?'Backend starting…':'Backend stopped';$('dot').className='dot '+(s.ready?'ready':s.running?'starting':'stopped');$('start').disabled=busy||s.running;$('stop').disabled=busy||!s.running;$('symbol').disabled=s.running;$('expiry').disabled=s.running;$('broker').disabled=s.running;$('modify-credentials').disabled=s.running;document.querySelectorAll('[data-env-key]').forEach(input=>input.disabled=s.running);$('dashboard').classList.toggle('disabled',!s.ready);if(s.error)$('msg').textContent=s.error}
async function refresh(){try{paint(await request('/api/status'))}catch(e){$('msg').textContent=e.message}}
$('broker').onchange=drawCredentials;
$('modify-credentials').onchange=drawCredentials;
$('start').onclick=async()=>{busy=true;$('msg').textContent='Saving configuration and starting…';try{paint(await request('/api/start',{symbol:$('symbol').value,expiry:$('expiry').value,broker:$('broker').value,credentials:credentials()}))}catch(e){$('msg').textContent=e.message}finally{busy=false;refresh()}};
$('stop').onclick=async()=>{if(!confirm('Stop the analytics backend completely?'))return;busy=true;$('msg').textContent='Stopping…';try{paint(await request('/api/stop',{}));$('msg').textContent='Backend stopped.'}catch(e){$('msg').textContent=e.message}finally{busy=false;refresh()}};
drawCredentials();refresh();setInterval(refresh,1000);
</script></body></html>"""


class BackendSupervisor:
    def __init__(self, *, backend_port: int = 5500) -> None:
        self.backend_port = backend_port
        self.backend_url = f"http://127.0.0.1:{backend_port}"
        self.process: subprocess.Popen | None = None
        self.log_handle = None
        self.last_error = ""
        self._lock = threading.RLock()

    def _ready(self) -> bool:
        if not self.running:
            return False
        try:
            with urllib.request.urlopen(f"{self.backend_url}/health", timeout=0.25) as response:
                return response.status == HTTPStatus.OK
        except Exception:
            return False

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def status(self) -> dict:
        with self._lock:
            if self.process is not None and self.process.poll() is not None:
                code = self.process.returncode
                if code and not self.last_error:
                    self.last_error = f"Backend exited with status {code}. Check runtime/control_backend.log."
                self._close_log()
            return {
                "running": self.running,
                "ready": self._ready(),
                "pid": self.process.pid if self.running else None,
                "dashboardUrl": f"{self.backend_url}/dist/Dashboard/DashboardPro.html",
                "error": self.last_error,
            }

    def start(self, symbol: str, expiry_iso: str = "", broker: str = "SMARTAPI", credentials: dict | None = None) -> dict:
        symbol = (symbol or "").strip().upper()
        if not SYMBOL_RE.fullmatch(symbol):
            raise ValueError("Enter a valid symbol.")
        broker = (broker or "").strip().upper()
        if broker not in DATA_PROVIDERS:
            raise ValueError("Select a valid market-data broker.")
        credentials = credentials or {}
        if not isinstance(credentials, dict):
            raise ValueError("Broker credentials must be valid fields.")
        allowed_keys = {key for key, _label in BROKER_CREDENTIALS[broker]}
        if set(credentials) - allowed_keys:
            raise ValueError("Unexpected broker credential field.")
        expiry = ""
        if expiry_iso:
            try:
                expiry = datetime.strptime(expiry_iso, "%Y-%m-%d").strftime("%d-%b-%Y")
            except ValueError as exc:
                raise ValueError("Expiry must be a valid date.") from exc

        with self._lock:
            if self.running:
                return self.status()
            try:
                with urllib.request.urlopen(f"{self.backend_url}/health", timeout=0.25):
                    raise ValueError(
                        f"Port {self.backend_port} already has a backend not owned by this launcher. "
                        "Stop that process first."
                    )
            except ValueError:
                raise
            except Exception:
                pass
            self.last_error = ""
            env_updates = {"MARKET_DATA_PROVIDER": broker, "LIVE_FEED_PROVIDER": broker}
            env_updates.update({key: value for key, value in credentials.items() if value})
            update_env(env_updates)
            log_path = PROJECT_ROOT / "runtime" / "control_backend.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_handle = log_path.open("a", encoding="utf-8")
            command = [
                sys.executable, "-m", "main", "--host", "127.0.0.1",
                "--http-port", str(self.backend_port), "--symbol", symbol,
            ]
            if expiry:
                command.extend(["--expiry", expiry])
            environment = os.environ.copy()
            environment["MARKET_DATA_PROVIDER"] = broker
            environment["LIVE_FEED_PROVIDER"] = broker
            src_path = str(PROJECT_ROOT / "src")
            environment["PYTHONPATH"] = src_path + os.pathsep + environment.get("PYTHONPATH", "")
            self.process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                stdout=self.log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            process = self.process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            self._close_log()
            self.process = None
            self.last_error = ""
            return self.status()

    def _close_log(self) -> None:
        if self.log_handle is not None:
            self.log_handle.close()
            self.log_handle = None


class ControlHandler(BaseHTTPRequestHandler):
    supervisor: BackendSupervisor

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/":
            schema = json.dumps(BROKER_CREDENTIALS).replace("<", "\\u003c")
            body = CONTROL_PAGE.replace("__CREDENTIAL_SCHEMA__", schema).replace(BACKEND_URL, self.supervisor.backend_url).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/status":
            self._json(HTTPStatus.OK, self.supervisor.status())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 4096:
                raise ValueError("Request is too large.")
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/start":
                result = self.supervisor.start(
                    payload.get("symbol", ""),
                    payload.get("expiry", ""),
                    payload.get("broker", "SMARTAPI"),
                    payload.get("credentials", {}),
                )
            elif self.path == "/api/stop":
                result = self.supervisor.stop()
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            self._json(HTTPStatus.OK, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, format: str, *args) -> None:
        print(f"[control] {self.address_string()} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="mTerminals lightweight control GUI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5400)
    parser.add_argument("--backend-port", type=int, default=5500)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the launcher page automatically.",
    )
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("Control GUI is loopback-only; use 127.0.0.1 or localhost.")

    supervisor = BackendSupervisor(backend_port=args.backend_port)
    ControlHandler.supervisor = supervisor
    server = ThreadingHTTPServer((args.host, args.port), ControlHandler)
    atexit.register(supervisor.stop)

    def shutdown(_signum, _frame):
        supervisor.stop()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, shutdown)
    print(f"[control] launcher available at http://{args.host}:{args.port}/")
    print("[control] analytics backend is stopped until Start Backend is clicked")
    if not args.no_browser:
        launcher_url = f"http://{args.host}:{args.port}/"
        opener = threading.Timer(0.25, webbrowser.open, args=(launcher_url,))
        opener.daemon = True
        opener.start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        supervisor.stop()


if __name__ == "__main__":
    main()
