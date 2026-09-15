"""Shared pytest fixtures.

Notably: makes src/server/app.py (the composition root) importable.
Before this fixture existed, importing that module in a test process did
three things no CI box (and no offline dev machine) can rely on:

  1. Parsed sys.argv with its own argparse.ArgumentParser — pytest's own
     CLI args (-k foo, -x, etc.) would blow it up.
  2. (Historical — fixed) Used to run brokers/smartapi/client.py's
     `INDEX_TOKENS = _build_index_tokens()` at import time, downloading
     Angel One's ScripMaster over the network with NO test seam — a real
     HTTP call as a side effect of `import server.app`, raising if the
     network was unavailable (or blocked, as in this sandbox) and there
     was no local cache yet. INDEX_TOKENS is now built lazily
     (client.get_index_tokens(), backed by a module __getattr__ for
     backward-compat imports) — importing client.py, or anything that
     transitively imports it, no longer touches the network. This fixture
     still seeds a fake ScripMaster cache below so any test that DOES
     resolve index tokens gets deterministic data instead of a real
     download.
  3. Wrote a live paper_trading.db / ScripMaster cache file into whatever
     the current working directory happened to be, via paths.py's
     CACHE_DIR.

This is very likely *why* order submission had zero direct tests
despite everything built on top of it (account_guard, auto_executor)
being well covered — the module simply could not be imported in a normal
test process. None of the underlying logic is actually untestable; it
just needed an import-time seam. RUNTIME_DIR (paths.py) already exists
as an escape hatch for exactly this, so this fixture:

  - points RUNTIME_DIR at a throwaway tmp directory so no test run ever
    touches the real runtime/cache/ (ScripMaster cache, paper_trading.db)
  - pre-seeds a minimal ScripMaster cache file there so
    _build_index_tokens() has something to index without a network call
  - clears sys.argv before import so pytest's own flags aren't parsed by
    the server composition root's argparse.
"""
import json
import os
import sys

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.dirname(BACKEND_DIR)

_FAKE_SCRIP_MASTER = [
    {
        "token": "26000", "symbol": "NIFTY", "name": "NIFTY", "expiry": "",
        "strike": "-1", "lotsize": "1", "instrumenttype": "AMXIDX",
        "exch_seg": "NSE", "tick_size": "5",
    },
    {
        "token": "26009", "symbol": "BANKNIFTY", "name": "BANKNIFTY",
        "expiry": "", "strike": "-1", "lotsize": "1",
        "instrumenttype": "AMXIDX", "exch_seg": "NSE", "tick_size": "5",
    },
]

# NOTE (historical — fixed): this used to document a suite-wide,
# collection-time failure — importing brokers/smartapi/client.py ran
# `INDEX_TOKENS = _build_index_tokens()` at module level, a real HTTP call
# with no test seam, breaking collection for any test that transitively
# imported it (mTerminals_json.py -> brokers/market_data.py ->
# brokers/smartapi/client.py, among others), independent of
# OrderSubmissionService or ws_server_live below. The real fix landed in
# smartapi/client.py itself: INDEX_TOKENS is now built lazily via
# get_index_tokens(), so importing the module (directly or transitively)
# no longer does network I/O — only an actual call to get_index_tokens()
# does, at which point the fake ScripMaster cache seeded below (or a real
# one, outside tests) is what gets read.


@pytest.fixture(scope="session")
def ws_server_live(tmp_path_factory):
    """Imports server.app exactly once for the whole test session
    (it's an expensive, side-effecting import) and hands back the live
    module object so tests can monkeypatch its globals per-test.

    Session scope is deliberate: re-importing per-test would re-run every
    module-level side effect (ScripMaster load, PaperTradingEngine()
    opening its SQLite file, etc.) for no benefit, since none of that
    state is what these tests are exercising — they patch the specific
    runtime dependencies used by OrderSubmissionService.
    """
    runtime_dir = tmp_path_factory.mktemp("ws_server_live_runtime")
    cache_dir = runtime_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "_scrip_master_cache.json").write_text(json.dumps(_FAKE_SCRIP_MASTER))

    old_argv = sys.argv
    old_cwd = os.getcwd()
    old_runtime_dir_env = os.environ.get("RUNTIME_DIR")
    old_live_enabled_env = os.environ.get("LIVE_TRADING_ENABLED")

    os.environ["RUNTIME_DIR"] = str(runtime_dir)
    os.environ.pop("LIVE_TRADING_ENABLED", None)  # module reads this once at import; keep it off
    sys.argv = ["main.py"]
    for p in (PROJECT_ROOT, BACKEND_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.chdir(str(runtime_dir))

    try:
        # Load the composition root under its canonical module name so the
        # dataclass/type machinery resolves against a single module object.
        # Pop any cached entry first so the test env (RUNTIME_DIR,
        # LIVE_TRADING_ENABLED) is applied on this fresh import.
        sys.modules.pop("server.app", None)
        import server.app as module

        yield module
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        if old_runtime_dir_env is None:
            os.environ.pop("RUNTIME_DIR", None)
        else:
            os.environ["RUNTIME_DIR"] = old_runtime_dir_env
        if old_live_enabled_env is None:
            os.environ.pop("LIVE_TRADING_ENABLED", None)
        else:
            os.environ["LIVE_TRADING_ENABLED"] = old_live_enabled_env


@pytest.fixture(scope="session")
def smartapi_modules(tmp_path_factory):
    """Imports brokers/smartapi_client.py and brokers/smartapi_ws_client.py
    exactly once for the whole session, with the same RUNTIME_DIR/
    ScripMaster-cache seam as the ws_server_live fixture above, but
    without that fixture's chdir/sys.argv/PaperTradingEngine overhead —
    session/reconnect tests don't need any of that, just an import that
    doesn't reach out to the real network or the real runtime/cache/.

    Session-scoped for the same reason as ws_server_live: re-importing
    per-test would re-run ScripMaster indexing for no benefit, since
    individual tests patch SmartApiSession/SmartTickStream instances
    directly rather than relying on module-level state.
    """
    runtime_dir = tmp_path_factory.mktemp("smartapi_runtime")
    cache_dir = runtime_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "_scrip_master_cache.json").write_text(json.dumps(_FAKE_SCRIP_MASTER))

    old_runtime_dir_env = os.environ.get("RUNTIME_DIR")
    os.environ["RUNTIME_DIR"] = str(runtime_dir)
    for p in (PROJECT_ROOT, BACKEND_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)

    try:
        import brokers.smartapi.client as smartapi_client
        import brokers.smartapi.websocket as smartapi_ws_client
        yield smartapi_client, smartapi_ws_client
    finally:
        if old_runtime_dir_env is None:
            os.environ.pop("RUNTIME_DIR", None)
        else:
            os.environ["RUNTIME_DIR"] = old_runtime_dir_env
