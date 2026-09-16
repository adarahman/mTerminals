"""Completion callbacks for fire-and-forget server tasks."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


def report_failed_task(task: asyncio.Task, tag: str) -> bool:
    """Report a background task failure and return whether it succeeded."""
    if task.cancelled():
        return False
    exc = task.exception()
    if exc is not None:
        logger.error(f"[{tag}] FAILED: {exc!r}", exc_info=exc)
        return False
    return True


def eod_task_done(task: asyncio.Task) -> None:
    """Report completion of the participant-OI end-of-day fetch."""
    if report_failed_task(task, "eod"):
        logger.info("[eod] fetch_all_eod completed successfully")


def flow_task_done(task: asyncio.Task) -> None:
    """Report completion of the cash-market FII/DII flow fetch."""
    if report_failed_task(task, "flow"):
        ok = task.result()
        logger.info(
            f"[flow] record_today_flow "
            f"{'succeeded' if ok else 'returned False (no data yet)'}"
        )
