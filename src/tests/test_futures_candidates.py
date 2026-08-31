from datetime import datetime

from application.market_pipeline.futures import _futures_candidates


def _parse_expiry(row):
    try:
        return datetime.strptime(row["expiry"], "%Y-%m-%d")
    except (KeyError, TypeError, ValueError):
        return None


def test_futures_candidates_filters_and_sorts_provider_rows():
    rows = [
        {"name": "NIFTY", "kind": "FUT", "exchange": "NFO", "expiry": "2026-10-29"},
        {"name": "NIFTY", "kind": "OPT", "exchange": "NFO", "expiry": "2026-09-24"},
        {"name": "BANKNIFTY", "kind": "FUT", "exchange": "NFO", "expiry": "2026-09-24"},
        {"name": "NIFTY", "kind": "FUT", "exchange": "BFO", "expiry": "2026-09-24"},
        {"name": "nifty", "kind": "FUT", "exchange": "NFO", "expiry": "2026-09-24"},
        {"name": "NIFTY", "kind": "FUT", "exchange": "NFO", "expiry": "bad"},
    ]

    candidates = _futures_candidates(
        rows,
        underlying="NIFTY",
        instrument_type_key="kind",
        instrument_types={"FUT"},
        parse_expiry=_parse_expiry,
        exchange_key="exchange",
        exchange="NFO",
    )

    assert [row["expiry"] for row, _expiry in candidates] == [
        "2026-09-24",
        "2026-10-29",
    ]


def test_futures_candidates_allows_pre_scoped_provider_dump():
    rows = [{"name": "NIFTY", "kind": "FUT", "expiry": "2026-09-24"}]

    candidates = _futures_candidates(
        rows,
        underlying="nifty",
        instrument_type_key="kind",
        instrument_types={"FUT"},
        parse_expiry=_parse_expiry,
    )

    assert candidates[0][0] is rows[0]
