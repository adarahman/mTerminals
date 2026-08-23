import sqlite3

from risk.live_order_store import LiveOrderStore


def test_completed_identity_survives_store_recreation(tmp_path):
    path = tmp_path / "live-orders.db"
    first = LiveOrderStore(str(path), max_entries=5)
    assert first.record("liveorder00000001", "ORDER123") == "ORDER123"

    restarted = LiveOrderStore(str(path), max_entries=5)
    assert restarted.get("liveorder00000001") == "ORDER123"


def test_first_order_id_remains_canonical_for_duplicate_identity(tmp_path):
    store = LiveOrderStore(str(tmp_path / "live-orders.db"), max_entries=5)
    store.record("liveorder00000001", "ORDER123")
    assert store.record("liveorder00000001", "ORDER999") == "ORDER123"


def test_store_prunes_oldest_completed_entries(tmp_path):
    path = tmp_path / "live-orders.db"
    store = LiveOrderStore(str(path), max_entries=2)
    for index in range(3):
        store.record(f"liveorder0000000{index}", f"ORDER{index}")
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE completed_live_orders SET completed_at = ? "
                "WHERE client_order_id = ?",
                (f"2026-01-01T00:00:0{index}+00:00", f"liveorder0000000{index}"),
            )
    store.record("liveorder00000002", "ORDER2")

    assert store.get("liveorder00000000") is None
    assert store.get("liveorder00000001") == "ORDER1"
    assert store.get("liveorder00000002") == "ORDER2"
