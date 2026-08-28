import asyncio
from datetime import datetime, timezone

from server.market_cycle_operations import MarketCycleOperations


def _operations(**overrides):
    broadcasts = []
    status = {"status": "STARTING", "reason": ""}

    async def broadcast(message):
        broadcasts.append(message)

    defaults = {
        "pipeline_status": status,
        "broadcast": broadcast,
        "use_broker_services": lambda: True,
        "live_feed_provider": lambda: "SMARTAPI",
        "data_source": lambda: "SMARTAPI",
        "feed_allowed": lambda provider: provider == "SMARTAPI",
        "fetch_all_eod": lambda *_args: None,
        "record_today_flow": lambda: None,
        "eod_task_done": lambda _task: None,
        "flow_task_done": lambda _task: None,
        "now": lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return MarketCycleOperations(**defaults), status, broadcasts


def test_pipeline_status_updates_and_broadcasts_only_visible_changes():
    operations, status, broadcasts = _operations()

    async def exercise():
        await operations.publish_pipeline_status("LIVE", elapsed=1.23456)
        await operations.publish_pipeline_status("LIVE", elapsed=2.0)

    asyncio.run(exercise())

    assert status == {
        "status": "LIVE",
        "reason": "",
        "elapsedSeconds": 2.0,
        "lastSuccessAt": "2026-08-27T00:00:00+00:00",
    }
    assert len(broadcasts) == 1
    assert broadcasts[0]["payload"]["elapsedSeconds"] == 1.235


def test_delayed_messages_follow_active_feed_capability():
    operations, _, _ = _operations()
    assert operations.delayed_reason(8) == (
        "REST analytics pass exceeded 8s; live prices continue via WebSocket"
    )
    assert operations.delayed_overlay() == "SMARTAPI websocket overlay remains active"

    public, _, _ = _operations(
        use_broker_services=lambda: False,
        data_source=lambda: "NSE_BSE",
    )
    assert public.delayed_reason(8) == (
        "Public REST analytics pass exceeded 8s; SmartAPI remains disabled"
    )
    assert public.delayed_overlay() == "NSE_BSE REST polling will retry"


def test_eod_jobs_run_and_keep_their_completion_callbacks():
    calls = []
    completed = []
    now = datetime(2026, 8, 27, tzinfo=timezone.utc)
    operations, _, _ = _operations(
        fetch_all_eod=lambda *args: calls.append(("eod", args)),
        record_today_flow=lambda: calls.append(("flow", ())),
        eod_task_done=lambda task: completed.append(("eod", task.exception())),
        flow_task_done=lambda task: completed.append(("flow", task.exception())),
    )

    async def exercise():
        tasks = operations.schedule_eod_jobs(now)
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert ("eod", (now, True)) in calls
    assert ("flow", ()) in calls
    assert sorted(completed) == [("eod", None), ("flow", None)]
