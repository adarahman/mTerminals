import asyncio
import logging


def test_background_task_failure_is_logged_and_released(ws_server_live, caplog):
    module = ws_server_live

    async def scenario():
        async def fail():
            raise RuntimeError("supervised boom")

        with caplog.at_level(logging.ERROR, logger="mterminals.server"):
            task = module._create_background_task(fail(), "test_subsystem")
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)  # allow the completion callback to run

        assert task not in module._BACKGROUND_TASKS

    asyncio.run(scenario())

    matching = [
        record for record in caplog.records
        if getattr(record, "event", None) == "background_task.failed"
    ]
    assert len(matching) == 1
    assert matching[0].subsystem == "test_subsystem"
    assert matching[0].status == "failed"
    assert matching[0].reason == "supervised boom"
    assert matching[0].exc_info is not None


def test_cancelled_background_task_is_not_logged_as_failure(ws_server_live, caplog):
    module = ws_server_live

    async def scenario():
        task = module._create_background_task(asyncio.sleep(60), "cancelled_test")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert task not in module._BACKGROUND_TASKS

    with caplog.at_level(logging.ERROR, logger="mterminals.server"):
        asyncio.run(scenario())

    assert not any(
        getattr(record, "event", None) == "background_task.failed"
        and getattr(record, "subsystem", None) == "cancelled_test"
        for record in caplog.records
    )

