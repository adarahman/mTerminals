import asyncio

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


def test_report_failed_task_surfaces_exception(capsys):
    task = _finished_task(error=RuntimeError("boom"))

    assert report_failed_task(task, "worker") is False
    captured = capsys.readouterr()
    assert "[worker] FAILED: RuntimeError('boom')" in captured.out
    assert "RuntimeError: boom" in captured.err


def test_eod_task_done_reports_success(capsys):
    eod_task_done(_finished_task())

    assert "[eod] fetch_all_eod completed successfully" in capsys.readouterr().out


def test_flow_task_done_distinguishes_result(capsys):
    flow_task_done(_finished_task(result=True))
    flow_task_done(_finished_task(result=False))

    output = capsys.readouterr().out
    assert "[flow] record_today_flow succeeded" in output
    assert "[flow] record_today_flow returned False (no data yet)" in output
