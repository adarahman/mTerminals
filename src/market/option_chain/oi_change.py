"""Previous-close OI anchoring and intraday OI-change calculation."""

import logging
import time
from collections.abc import Callable
from datetime import date


class PreviousCloseOiTracker:
    def __init__(
        self,
        load_public_frame: Callable[[str, str], object],
        *,
        retry_cooldown_seconds: float = 30,
        logger: logging.Logger | None = None,
    ):
        self._load_public_frame = load_public_frame
        self.retry_cooldown_seconds = retry_cooldown_seconds
        self.logger = logger or logging.getLogger(__name__)
        self.anchors: dict[tuple, tuple] = {}
        self.seed_state: dict[tuple, dict] = {}

    def seed(self, underlying: str, expiry: str) -> None:
        today = date.today()
        seed_key = (underlying, expiry, today)
        state = self.seed_state.get(seed_key)
        if state and state["complete"]:
            return
        now = time.monotonic()
        if state and now - state["last_attempt"] < self.retry_cooldown_seconds:
            return
        self.seed_state[seed_key] = {"complete": False, "last_attempt": now}

        try:
            frame = self._load_public_frame(underlying, expiry)
        except Exception as error:
            self.logger.warning(
                "NSE seed fetch failed for %s %s (%s); ChgOI will anchor on "
                "first tick until retry in %ss",
                underlying,
                expiry,
                error,
                self.retry_cooldown_seconds,
            )
            return

        seeded = 0
        skipped = {"CE": 0, "PE": 0}
        for row in frame.to_dict("records"):
            strike = row.get("StrikePrice")
            if strike is None:
                continue
            for side, oi_column, change_column in (
                ("CE", "CE_OI", "CE_ChgOI"),
                ("PE", "PE_OI", "PE_ChgOI"),
            ):
                current_oi = row.get(oi_column)
                change_oi = row.get(change_column)
                if current_oi is None or change_oi is None:
                    skipped[side] += 1
                    continue
                self.anchors[(underlying, expiry, strike, side)] = (
                    today,
                    float(current_oi) - float(change_oi),
                )
                seeded += 1

        total_skipped = skipped["CE"] + skipped["PE"]
        self.seed_state[seed_key] = {
            "complete": total_skipped == 0,
            "last_attempt": now,
        }
        skew_note = ""
        if skipped["CE"] != skipped["PE"]:
            skew_note = (
                f" — CE/PE SKEW: {skipped['CE']} CE vs {skipped['PE']} PE "
                "strikes unseeded"
            )
        self.logger.info(
            "Seeded ChgOI anchor for %s %s from NSE: %s strike/side entries "
            "(%s CE / %s PE skipped)%s",
            underlying,
            expiry,
            seeded,
            skipped["CE"],
            skipped["PE"],
            skew_note,
        )

    def change(
        self,
        underlying: str,
        expiry: str,
        strike: float,
        side: str,
        current_oi,
        *,
        seed: Callable[[str, str], None] | None = None,
    ) -> float:
        key = (underlying, expiry, strike, side)
        current = float(current_oi or 0.0)
        today = date.today()
        entry = self.anchors.get(key)
        if entry is None or entry[0] != today:
            (seed or self.seed)(underlying, expiry)
            entry = self.anchors.get(key)
        if entry is None or entry[0] != today:
            self.anchors[key] = (today, current)
            return 0.0
        return current - entry[1]
