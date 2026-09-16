import asyncio
import logging

from server.task_callbacks import eod_task_done, flow_task_done, report_failed_task


def _finished_task(result=None, error=None):
    async def run():
        if error is not None:
            raise error
        return result

    async def build():
        task = asyncio.create_task(run())
        await asyncio.wait({task})
        return task

    return asyncio.run(build())


def test_report_failed_task_surfaces_exception(caplog):
    task = _finished_task(error=RuntimeError("boom"))

    assert report_failed_task(task, "worker") is False
    assert "[worker] FAILED: RuntimeError('boom')" in caplog.text
    assert "RuntimeError: boom" in caplog.text


def test_eod_task_done_reports_success(caplog):
    caplog.set_level(logging.INFO)
    eod_task_done(_finished_task())

    assert "[eod] fetch_all_eod completed successfully" in caplog.text


def test_flow_task_done_distinguishes_result(caplog):
    caplog.set_level(logging.INFO)
    flow_task_done(_finished_task(result=True))
    flow_task_done(_finished_task(result=False))

    assert "[flow] record_today_flow succeeded" in caplog.text
    assert "[flow] record_today_flow returned False (no data yet)" in caplog.text
