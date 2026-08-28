"""Shared pytest fixtures.

Notably: makes src/server/app.py (the composition root) importable.
Before this fixture existed, importing that module in a test process did
three things no CI box (and no offline dev machine) can rely on:

  1. Parsed sys.argv with its own argparse.ArgumentParser — pytest's own
     CLI args (-k foo, -x, etc.) would blow it up.
  2. Ran brokers/smartapi_client.py's `INDEX_TOKENS = _build_index_tokens()`
     at import time, which downloads Angel One's ScripMaster over the
     network with NO test seam — a real HTTP call as a side effect of
     `import server.app`,
     that raises if the network is unavailable
     (or blocked, as it is in this sandbox) and there's no local cache yet.
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

# NOTE: test_strategies.py (and likely others) has a PRE-EXISTING, separate
# collection-time failure on a machine with no network access and no
# previously-downloaded ScripMaster cache: it transitively imports
# mTerminals_json.py -> brokers/market_data.py -> brokers/smartapi_client.py,
# which runs `INDEX_TOKENS = _build_index_tokens()` at module level — a real
# HTTP call with no test seam, same root cause as server/app.py's gap
# below. This is a suite-wide hermeticity issue, not something specific to
# OrderSubmissionService, and fixing it generally means adding a proper test
# seam in smartapi_client.py itself (e.g. an env var or injectable loader
# for the ScripMaster source) rather than a tests/-side workaround — a
# tests/-only fix would either have to monkeypatch every affected module's
# import chain individually, or write fake data into the same on-disk cache
# path the real app reads from (runtime/cache/_scrip_master_cache.json),
# which risks a dev machine silently running the real app against fake
# 2-row test data after a test run. Left unfixed here deliberately; flagging
# it rather than papering over it with a source-adjacent side effect.


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
    sys.argv = ["run_server.py"]
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
