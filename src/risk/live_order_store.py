"""Durable idempotency ledger for real-money order submissions."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from infrastructure.paths import CACHE_DIR


DB_PATH = os.path.join(CACHE_DIR, "live_order_idempotency.db")


class LiveOrderStore:
    def __init__(self, db_path: str = DB_PATH, max_entries: int = 500):
        self.db_path = db_path
        self.max_entries = max(1, int(max_entries))
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS completed_live_orders (
                    client_order_id TEXT PRIMARY KEY,
                    broker_order_id TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                )
            """)

    def get(self, client_order_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT broker_order_id FROM completed_live_orders "
                "WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def record(self, client_order_id: str, broker_order_id: str) -> str:
        """Records once and returns the canonical order ID for this key."""
        completed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT OR IGNORE INTO completed_live_orders "
                "(client_order_id, broker_order_id, completed_at) VALUES (?, ?, ?)",
                (client_order_id, str(broker_order_id), completed_at),
            )
            conn.execute(
                "DELETE FROM completed_live_orders WHERE client_order_id IN ("
                "SELECT client_order_id FROM completed_live_orders "
                "ORDER BY completed_at DESC LIMIT -1 OFFSET ?)",
                (self.max_entries,),
            )
            row = conn.execute(
                "SELECT broker_order_id FROM completed_live_orders "
                "WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("live order identity was pruned before it could be read")
        return str(row[0])
