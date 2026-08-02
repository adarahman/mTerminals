"""
caches.py
=========
Encapsulated replacements for mTerminals' scattered module-level cache
globals (_DF_IDX_CACHE, _quote_cache, _scrip_master_cache, _BATCH_CACHE,
_HISTORY_MEM, _OI_SNAPSHOTS_MEM, _FII_DII_CACHE, _SENTIMENT_CACHE,
_FILE_CACHE, _VIX_HISTORY).

This is an ENCAPSULATION pass, not a behavior change: every class here
preserves the exact read/write/staleness semantics of the bare dict/list/
deque global it replaces. Nothing outside the owning module needs to
change — each module keeps its existing function names (_quote_cache_get,
_load_scrip_master, append_json_history, etc.) as thin wrappers around one
of these instances instead of a bare dict.

Why several small classes instead of one MarketCache god-object:
  - The 8 globals fall into 3 recurring *patterns* (TTL-refresh,
    day/key memoization, rolling/accumulating state) reused across
    otherwise-unrelated files. Naming the pattern once and reusing it
    fixes the actual duplication; forcing unrelated subsystems (VIX
    history, ScripMaster, FII/DII sentiment) into one shared object with
    one shared lock would add coupling with no corresponding benefit —
    nothing today reads across these domains.
  - A couple of these (DirtyFrameStore, TickScopedDict) have enough
    domain-specific behavior (parquet flushing, per-tick refill) that
    forcing them through a fully generic cache class would obscure more
    than it encapsulates. They're still classes — just purpose-built
    ones — rather than bare globals.

Classes
-------
  TTLSlot         Single cached value, stale after ttl_seconds.
                  clock='epoch' uses time.time() (matches
                  option_chain_json's old time.time()-based TTL check).
                  clock='datetime' uses datetime.now() (matches
                  smartapi_client's old ScripMaster TTL check, which
                  also needs a real timestamp to compare against a
                  file's on-disk mtime elsewhere in that module).
                  Replaces: option_chain_json._DF_IDX_CACHE
                            smartapi_client._scrip_master_cache

  TTLKeyCache     Per-key value + timestamp cache, thread-safe,
                  time.monotonic()-based.
                  Replaces: smartapi_client._quote_cache (+ its lock)

  MemoCache       key -> value memoization, no automatic eviction
                  (caller picks keys that naturally bound size, e.g. a
                  trading day — same contract as the globals it
                  replaces, including their lack of eviction).
                  Replaces: mTerminals_json._FII_DII_CACHE
                            build_training_warehouse._SENTIMENT_CACHE
                            fii_dii_sentiment._FILE_CACHE

  TickScopedDict  Dict fully cleared and refilled once per engine tick,
                  read multiple times before the next refill.
                  Replaces: smartapi_pipeline_adapter._BATCH_CACHE

  RollingWindow   Time-windowed (timestamp, value) deque, pruned on
                  every append.
                  Replaces: decision_engine._VIX_HISTORY

  DirtyFrameStore Accumulating DataFrame + dirty flag + last-flush
                  timestamp, for a periodically-flushed parquet log.
                  Replaces: oi_analysis._HISTORY_MEM

  ListSlot        Trivial encapsulated list (get/replace) — kept as a
                  class purely for symmetry/testability with the rest
                  of this module; behavior is identical to before.
                  Replaces: mTerminals_json._OI_SNAPSHOTS_MEM
"""

import threading
import time
from collections import deque
from datetime import datetime


class TTLSlot:
    """Single cached value that goes stale after `ttl_seconds`."""

    def __init__(self, ttl_seconds, clock="epoch"):
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self.value = None
        self.fetched_at = None

    def _now(self):
        return datetime.now() if self._clock == "datetime" else time.time()

    def is_fresh(self):
        if self.value is None or self.fetched_at is None:
            return False
        if self._clock == "datetime":
            age = (self._now() - self.fetched_at).total_seconds()
        else:
            age = self._now() - self.fetched_at
        return age < self.ttl_seconds

    def set(self, value, fetched_at=None):
        self.value = value
        self.fetched_at = fetched_at if fetched_at is not None else self._now()


class TTLKeyCache:
    """Per-key cache with a TTL, thread-safe."""

    def __init__(self, ttl_seconds):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple] = {}

    def get(self, key):
        with self._lock:
            entry = self._store.get(key)
            if entry and (time.monotonic() - entry[0]) < self.ttl_seconds:
                return entry[1]
            return None

    def set(self, key, value):
        with self._lock:
            self._store[key] = (time.monotonic(), value)


class MemoCache:
    """key -> value memoization cache with no automatic eviction."""

    def __init__(self):
        self._store: dict = {}

    def __contains__(self, key):
        return key in self._store

    def get(self, key, default=None):
        return self._store.get(key, default)

    def set(self, key, value):
        self._store[key] = value

    def clear(self):
        self._store.clear()


class TickScopedDict:
    """Dict meant to be cleared and refilled once per engine tick, then
    read multiple times before the next tick."""

    def __init__(self):
        self._store: dict = {}

    def refill(self, *sources):
        """Clears the cache, then updates it from each dict in `sources`
        (later sources win on key collision) — same order of operations
        as the old _BATCH_CACHE.clear()/.update()/.update() call site."""
        self._store.clear()
        for src in sources:
            self._store.update(src)

    def get(self, key, default=None):
        return self._store.get(key, default)


class RollingWindow:
    """Time-windowed (timestamp, value) deque, pruned on every append."""

    def __init__(self, max_age_seconds):
        self.max_age_seconds = max_age_seconds
        self._data = deque()

    def append(self, timestamp, value):
        self._data.append((timestamp, value))
        cutoff = timestamp - self.max_age_seconds
        while self._data and self._data[0][0] < cutoff:
            self._data.popleft()

    def values_since(self, window_start, before=None):
        return [
            v for ts, v in self._data
            if ts >= window_start and (before is None or ts < before)
        ]

    def __bool__(self):
        return bool(self._data)

    def __iter__(self):
        return iter(self._data)


class DirtyFrameStore:
    """Accumulating DataFrame + dirty flag + last-flush timestamp, for a
    periodically-flushed parquet log."""

    def __init__(self):
        self.df = None
        self.log_path = None
        self.last_flush = None
        self.dirty = False

    def loaded_for(self, log_path):
        return self.df is not None and self.log_path == log_path

    def load(self, df, log_path):
        self.df = df
        self.log_path = log_path
        self.last_flush = datetime.now()
        self.dirty = False

    def replace(self, df):
        self.df = df
        self.dirty = True

    def flush_due(self, flush_interval_seconds):
        return (
            self.last_flush is None
            or (datetime.now() - self.last_flush).total_seconds() >= flush_interval_seconds
        )

    def mark_flushed(self):
        self.last_flush = datetime.now()
        self.dirty = False


class ListSlot:
    """Trivial encapsulated list (get/replace)."""

    def __init__(self):
        self._data: list = []

    def get(self):
        return self._data

    def set(self, items):
        self._data = items
