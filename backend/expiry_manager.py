"""
expiry_manager.py
─────────────────
Classifies the NSE expiryDates list (already present in mTerminals.json)
into four named expiry slots:

    CURRENT   — the active expiry you are trading (nearest from today)
    NEAR      — next weekly after current (rollover target)
    MONTHLY   — last weekly expiry of the nearest calendar month
                beyond current (NSE NIFTY/BANKNIFTY monthly)
    FAR       — last weekly expiry of the calendar month after monthly

No NSE API calls — works entirely off the expiryDates list returned by
the existing NSE option chain response.

Usage
─────
    from expiry_manager import ExpiryManager

    # em accepts the raw expiryDates list from NSE / mTerminals.json
    em = ExpiryManager(expiry_dates=["30-Jun-2026", "07-Jul-2026", ...])

    ctx = em.context()
    # → ExpiryContext with .current, .near, .monthly, .far slots
    # Each slot: ExpirySlot(label, date_str, date, dte, tag)

    # Inject into mTerminals_json payload:
    payload["expiryContext"] = ctx.to_dict()
    payload["expiryDates"]   = em.all_str       # unchanged full list

    # Switch active expiry (e.g. user selects "NEAR" in UI):
    em.set_active("NEAR")
    active_str = em.active_str     # "07-Jul-2026"
    active_dte = em.active_dte     # 9
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple


_FMT = "%d-%b-%Y"   # NSE date format: "30-Jun-2026"


# ── Types ─────────────────────────────────────────────────────────────────────

@dataclass
class ExpirySlot:
    tag:      str        # "CURRENT" | "NEAR" | "MONTHLY" | "FAR"
    label:    str        # human-readable: "Current (30-Jun)", "Near (07-Jul)" …
    date_str: str        # "30-Jun-2026"
    date:     date       # python date object
    dte:      int        # calendar days from today (0 = expiry day)
    is_monthly: bool     # True if this is the last expiry of its calendar month

    def to_dict(self) -> dict:
        return {
            "tag":       self.tag,
            "label":     self.label,
            "dateStr":   self.date_str,
            "dte":       self.dte,
            "isMonthly": self.is_monthly,
        }


@dataclass
class ExpiryContext:
    current: ExpirySlot
    near:    Optional[ExpirySlot]
    monthly: Optional[ExpirySlot]
    far:     Optional[ExpirySlot]
    today:   date

    def to_dict(self) -> dict:
        return {
            "today":   self.today.strftime(_FMT),
            "current": self.current.to_dict(),
            "near":    self.near.to_dict()    if self.near    else None,
            "monthly": self.monthly.to_dict() if self.monthly else None,
            "far":     self.far.to_dict()     if self.far     else None,
            # convenience flat list for UI expiry-picker tabs
            "slots":   [s.to_dict() for s in self._active_slots()],
        }

    def _active_slots(self) -> List[ExpirySlot]:
        out = [self.current]
        for s in (self.near, self.monthly, self.far):
            if s is not None:
                out.append(s)
        return out

    def slot_by_tag(self, tag: str) -> Optional[ExpirySlot]:
        for s in (self.current, self.near, self.monthly, self.far):
            if s is not None and s.tag == tag:
                return s
        return None


# ── Manager ───────────────────────────────────────────────────────────────────

class ExpiryManager:
    """
    Parameters
    ----------
    expiry_dates : list of "DD-Mon-YYYY" strings (from NSE optionChain.records.expiryDates)
    today        : override today's date (default: date.today()); pass for testing
    active_tag   : which slot is currently selected ("CURRENT" default)
    """

    def __init__(
        self,
        expiry_dates: List[str],
        today: Optional[date] = None,
        active_tag: str = "CURRENT",
    ):
        self._today      = today or date.today()
        self._raw        = expiry_dates                         # original order kept
        self._parsed     = self._parse(expiry_dates)            # [(str, date)] sorted
        self._monthly_set= self._find_monthly_dates()          # set of date objects
        self._ctx        = self._classify()
        self._active_tag = active_tag

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def all_str(self) -> List[str]:
        """Full unmodified list — pass straight to expiryDates in JSON."""
        return self._raw

    @property
    def context(self) -> ExpiryContext:
        return self._ctx

    @property
    def active_slot(self) -> ExpirySlot:
        s = self._ctx.slot_by_tag(self._active_tag)
        return s if s is not None else self._ctx.current

    @property
    def active_str(self) -> str:
        return self.active_slot.date_str

    @property
    def active_dte(self) -> int:
        return self.active_slot.dte

    def set_active(self, tag: str):
        """Switch selected expiry: "CURRENT" | "NEAR" | "MONTHLY" | "FAR"."""
        if tag not in ("CURRENT", "NEAR", "MONTHLY", "FAR"):
            raise ValueError(f"Unknown tag '{tag}'. Use CURRENT/NEAR/MONTHLY/FAR.")
        if self._ctx.slot_by_tag(tag) is None:
            raise ValueError(f"Tag '{tag}' not available for this expiry list.")
        self._active_tag = tag

    def dte_for(self, date_str: str) -> int:
        """Return DTE (calendar days) for any date string in the list."""
        d = datetime.strptime(date_str, _FMT).date()
        return max(0, (d - self._today).days)

    def to_json_payload(self) -> dict:
        """
        Returns the block to merge into the mTerminals JSON payload.

            payload.update(em.to_json_payload())
        """
        ctx = self._ctx
        return {
            "expiry":        self.active_str,
            "dte":           self.active_dte,
            "expiryDates":   self.all_str,
            "expiryContext": ctx.to_dict(),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse(raw: List[str]) -> List[Tuple[str, date]]:
        parsed = []
        for s in raw:
            try:
                parsed.append((s, datetime.strptime(s, _FMT).date()))
            except ValueError:
                pass   # skip malformed entries
        return sorted(parsed, key=lambda x: x[1])

    def _find_monthly_dates(self) -> set:
        """
        Last expiry of each calendar month = monthly expiry.
        NSE NIFTY/BANKNIFTY monthly = last Thursday of month, but we derive
        it structurally from the expiry list itself so it works for any symbol.
        """
        by_month: dict = {}
        for s, d in self._parsed:
            key = (d.year, d.month)
            if key not in by_month or d > by_month[key][1]:
                by_month[key] = (s, d)
        return {d for _, d in by_month.values()}

    def _dte(self, d: date) -> int:
        return max(0, (d - self._today).days)

    def _make_slot(self, tag: str, s: str, d: date) -> ExpirySlot:
        is_m = d in self._monthly_set
        month_abbr = d.strftime("%b")
        day = d.day

        tag_labels = {
            "CURRENT": f"Current ({day}-{month_abbr})",
            "NEAR":    f"Near ({day}-{month_abbr})",
            "MONTHLY": f"Monthly ({day}-{month_abbr})",
            "FAR":     f"Far Monthly ({day}-{month_abbr})",
        }
        return ExpirySlot(
            tag=tag,
            label=tag_labels[tag],
            date_str=s,
            date=d,
            dte=self._dte(d),
            is_monthly=is_m,
        )

    def _classify(self) -> ExpiryContext:
        today = self._today
        future = [(s, d) for s, d in self._parsed if d >= today]

        if not future:
            raise ValueError("No valid future expiry dates found in the list.")

        # ── CURRENT: nearest expiry on-or-after today ─────────────────────────
        cur_s, cur_d = future[0]
        current = self._make_slot("CURRENT", cur_s, cur_d)

        after_current = [(s, d) for s, d in future if d > cur_d]

        # ── NEAR: next expiry immediately after current ────────────────────────
        near: Optional[ExpirySlot] = None
        if after_current:
            ns, nd = after_current[0]
            near = self._make_slot("NEAR", ns, nd)

        # ── MONTHLY: last expiry of the nearest calendar month beyond current ──
        #    If current IS a monthly expiry → look one month further forward
        monthly: Optional[ExpirySlot] = None
        target_month_start = cur_d + timedelta(days=1)

        # Gather all monthly-flagged dates strictly after current
        future_monthlies = [
            (s, d) for s, d in after_current
            if d in self._monthly_set
        ]
        if future_monthlies:
            ms, md = future_monthlies[0]
            monthly = self._make_slot("MONTHLY", ms, md)

        # ── FAR: monthly expiry of the calendar month after "monthly" ──────────
        far: Optional[ExpirySlot] = None
        if monthly is not None:
            far_candidates = [
                (s, d) for s, d in future_monthlies
                if d > monthly.date
            ]
            if far_candidates:
                fs, fd = far_candidates[0]
                far = self._make_slot("FAR", fs, fd)

        return ExpiryContext(
            current=current,
            near=near,
            monthly=monthly,
            far=far,
            today=today,
        )


# ── Convenience factory ───────────────────────────────────────────────────────

def make_expiry_manager(
    nse_expiry_dates: List[str],
    active_tag: str = "CURRENT",
    today: Optional[date] = None,
) -> ExpiryManager:
    """One-liner factory for use in mTerminals_json.export_dashboard_json()."""
    return ExpiryManager(nse_expiry_dates, today=today, active_tag=active_tag)
