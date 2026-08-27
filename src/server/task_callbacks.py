"""Completion callbacks for fire-and-forget server tasks."""

from __future__ import annotations

import asyncio
import traceback


def report_failed_task(task: asyncio.Task, tag: str) -> bool:
    """Report a background task failure and return whether it succeeded."""
    if task.cancelled():
        return False
    exc = task.exception()
    if exc is not None:
        print(f"[{tag}] FAILED: {exc!r}", flush=True)
        traceback.print_exception(type(exc), exc, exc.__traceback__)
        return False
    return True


def eod_task_done(task: asyncio.Task) -> None:
    """Report completion of the participant-OI end-of-day fetch."""
    if report_failed_task(task, "eod"):
        print("[eod] fetch_all_eod completed successfully", flush=True)


def flow_task_done(task: asyncio.Task) -> None:
    """Report completion of the cash-market FII/DII flow fetch."""
    if report_failed_task(task, "flow"):
        ok = task.result()
        print(
            f"[flow] record_today_flow "
            f"{'succeeded' if ok else 'returned False (no data yet)'}",
            flush=True,
        )
