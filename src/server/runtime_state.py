# src/server/runtime_state.py

import asyncio

MAIN_LOOP: asyncio.AbstractEventLoop | None = None
BACKGROUND_TASKS: set[asyncio.Task] = set()

LAST_FUNDS = None
LAST_LIVE_POSITIONS = None
LAST_ALGO_STATUS = None

INDEX_QUOTES = {}

NODE_RELAY = None

LAST_PAYLOAD = None
LAST_PAYLOAD_AT = None
LAST_SENT = None

BASELINE_SEQ = 0
BASELINE_ID = None

PROCESS_STARTED_AT = None
LAST_HEALTH_LOG_STATE = None

PIPELINE_TASK = None
PIPELINE_STATUS = {}

LAST_PORTFOLIO_BROADCAST_TS = 0.0
EOD_DONE_DATE = None
LAST_SESSION_DATE = None

# ── Process-wide state owned by this module (single source of truth) ──────
# The composition root (server/app.py) configures these at import time; all
# other modules must read/write them via this module, never keep a local copy.
DASHBOARD_CLIENTS = None
CONNECTED = None
METRICS = None
LAST_RECONCILIATION_ALERT = None
LAST_KNOWN_LEG_PRICES: dict = {}
FEEDS: dict = {}
SYMBOL_SWITCH_EVENT = asyncio.Event()
TICK_ACTIVITY_EVENT = asyncio.Event()
MARKET_STREAM_LOCK = asyncio.Lock()
CANONICAL_PAYLOAD_PUBLISHER = None
MARKET_ENGINE_CYCLE = None
MARKET_TICK_PACER = None
HTTP_ROUTE_HANDLERS = None
DASHBOARD_WS_HANDLER = None
WS_HANDSHAKE = None
WS_MESSAGE_ROUTER = None
WS_QUERY_CONTROLLER = None
LIVE_TRADING_KILL_SWITCH_ACTIVE = False
PIPELINE_DELAYED_OVERLAY = None
PIPELINE_DELAYED_REASON = None


def store_canonical_payload(payload, published_at) -> None:
    global LAST_PAYLOAD, LAST_PAYLOAD_AT
    LAST_PAYLOAD = payload
    LAST_PAYLOAD_AT = published_at


def store_previous_payload(payload) -> None:
    global LAST_SENT
    LAST_SENT = payload


def invalidate_market_baseline() -> None:
    global LAST_SENT
    LAST_SENT = None
    SYMBOL_SWITCH_EVENT.set()
