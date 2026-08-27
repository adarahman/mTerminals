"""
backfill_eod.py
----------------
One-off backfill for backend/nse_eod_fetch.py's three EOD datasets
(fao_participant_oi, fao_participant_vol, combine_oi) — populates enough
trading-day history for analytics/fii_dii_sentiment.py to have a
source_date/compare_date pair immediately, instead of waiting for the live
pipeline's daily post-close fetch to accumulate it naturally.

Usage (run from anywhere — DATA_DIR is anchored to PROJECT_ROOT, not cwd):
    python backfill_eod.py                # last 10 trading days
    python backfill_eod.py --days 20       # last 20 trading days
    python backfill_eod.py --start 2026-07-01 --end 2026-07-31

Writes to <PROJECT_ROOT>/data/eod/<dataset>/<dataset>_<yyyymmdd>.parquet —
the same location fii_dii_sentiment.py and the live server/app.py EOD
job both read/write via nse_eod_fetch.DATA_DIR, so this is immediately
visible to the running dashboard without needing a restart (the next 5s
poll tick re-reads via analytics/fii_dii_sentiment.py's own cache, which is
keyed by calendar day).

NSE's archive URLs are date-parameterized (fao_participant_oi_{ddmmyyyy}.csv
etc.), so arbitrary past trading days work the same way "today" does in the
live pipeline — this isn't a special backfill API, just fetch_all_eod()
called in a loop with explicit past dates.

NOTE: NSE's nsearchives.nseindia.com host may only retain a limited lookback
window (observed to vary; hasn't been verified against a fixed cutoff here).
If a date 404s or returns non-CSV content, fetch_participant_oi/vol/
combine_oi already return None gracefully (see nse_eod_fetch.py's own
docstrings) rather than raising — this script just reports which dates
failed so you can see how far back the archive actually goes.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta

from nse_eod_fetch import fetch_all_eod, is_trading_day, DATA_DIR


def _daterange_trading_days(start: datetime, end: datetime):
    """Yield each trading day from start to end, inclusive, oldest first."""
    d = start
    while d <= end:
        if is_trading_day(d):
            yield d
        d += timedelta(days=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=10,
                         help="Backfill the last N trading days ending yesterday (default 10). Ignored if --start/--end given.")
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end", type=str, default=None, help="YYYY-MM-DD, inclusive (default: yesterday)")
    parser.add_argument("--sleep", type=float, default=2.0,
                         help="Seconds to sleep between trading days, to avoid hammering NSE (default 2.0)")
    args = parser.parse_args()

    if args.start:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d") if args.end else (datetime.now() - timedelta(days=1))
        trading_days = list(_daterange_trading_days(start, end))
    else:
        # Walk backward from yesterday collecting the last N trading days.
        trading_days = []
        d = datetime.now() - timedelta(days=1)
        while len(trading_days) < args.days:
            if is_trading_day(d):
                trading_days.append(d)
            d -= timedelta(days=1)
        trading_days.reverse()  # oldest first, so the log reads chronologically

    print(f"Backfilling {len(trading_days)} trading day(s) into {DATA_DIR}")
    print(f"Range: {trading_days[0].strftime('%Y-%m-%d')} .. {trading_days[-1].strftime('%Y-%m-%d')}\n")

    results = []
    for i, d in enumerate(trading_days):
        date_str = d.strftime("%Y-%m-%d")
        print(f"[{i+1}/{len(trading_days)}] {date_str} ...", end=" ", flush=True)
        try:
            out = fetch_all_eod(d, save=True, skip_non_trading_days=True)
        except Exception as exc:
            print(f"ERROR: {exc}")
            results.append((date_str, {"error": str(exc)}))
            continue

        status = {name: ("OK" if df is not None else "MISSING") for name, df in out.items()}
        print(", ".join(f"{name}={s}" for name, s in status.items()))
        results.append((date_str, status))

        if i < len(trading_days) - 1:
            time.sleep(args.sleep)

    print("\n--- Summary ---")
    for date_str, status in results:
        if "error" in status:
            print(f"  {date_str}: ERROR - {status['error']}")
        else:
            missing = [k for k, v in status.items() if v == "MISSING"]
            print(f"  {date_str}: {'all OK' if not missing else 'missing ' + ', '.join(missing)}")

    ok_participant_oi = sum(1 for _, s in results if s.get("fao_participant_oi") == "OK")
    print(f"\nfao_participant_oi: {ok_participant_oi}/{len(trading_days)} days fetched successfully.")
    if ok_participant_oi >= 2:
        print("That's enough (2+) for fii_dii_sentiment.py's source_date/compare_date pair — "
              "the FII/DII card should populate on the next poll tick.")
    elif ok_participant_oi == 1:
        print("Only 1 day succeeded — fii_dii_sentiment.py needs a second, earlier day to compare "
              "against. Try increasing --days or check NSE's archive retention for this range.")
    else:
        print("0 days succeeded — check network access to nsearchives.nseindia.com, or that the "
              "date range only contains days too recent/too old for NSE's archive to have published yet.")

    sys.exit(0 if ok_participant_oi >= 2 else 1)


if __name__ == "__main__":
    from logging_config import configure_logging
    configure_logging()
    main()
