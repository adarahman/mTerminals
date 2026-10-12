from datetime import datetime

from application.cas_indicative import (
    compute_nifty_cas,
    extract_indicative_closes,
    parse_cash_scrip_master,
)


def test_cash_scrip_master_resolves_only_eq_series_and_rejects_ambiguity():
    csv_text = (
        "pSymbolName,pTrdSymbol,pSymbol,pSeries\n"
        "RELIANCE,RELIANCE-EQ,2885,EQ\n"
        "RELIANCE,RELIANCE26JUNFUT,9911,XX\n"
        "TCS,TCS-EQ,11536,EQ\n"
        "TCS,TCS-EQ-DUP,11537,EQ\n"
    )

    assert parse_cash_scrip_master(csv_text, {"RELIANCE", "TCS"}) == {
        "RELIANCE": "2885"
    }


def test_raw_market_picture_exposes_indicative_close_and_auction_flag():
    from neo_api_client.websocket.feed.protocol import (
        _HEADER,
        _MP_BODY,
        MSG_MARKET_PICTURE,
    )

    body_values = list(_MP_BODY.unpack(bytes(_MP_BODY.size)))
    body_values[0] = 2885
    body_values[13] = 2350123
    body = _MP_BODY.pack(*body_values)
    packet_size = _HEADER.size + len(body)
    packet = _HEADER.pack(packet_size, MSG_MARKET_PICTURE, 1, 3, 1, 0, 0) + body

    assert extract_indicative_closes(packet, {1: 100}) == [
        ("2885", 23501.23, True)
    ]
    assert extract_indicative_closes(packet, {1: 100})[0][2] is True


def _nifty_rows():
    rows = [
        {
            "Index": "NIFTY 50",
            "Symbol": "NIFTY",
            "Series": "",
            "Prev Close": 25000,
        }
    ]
    for i in range(50):
        rows.append(
            {
                "Index": "NIFTY 50",
                "Symbol": f"STOCK{i}",
                "Series": "EQ",
                "Last Price": 100 + i,
                "Prev Close": 100 + i,
                "ffmc": (100 + i) * (i + 1) * 1_000_000,
            }
        )
    return rows


def test_cas_index_uses_float_market_cap_weights_and_requires_coverage():
    now = datetime.fromisoformat("2026-06-11T15:22:00+05:30")
    timestamp = now.timestamp()
    rows = _nifty_rows()
    prices = {f"STOCK{i}": (101 + i, timestamp) for i in range(50)}

    result = compute_nifty_cas(rows, prices, now=now)

    assert result["status"] == "live"
    assert result["value"] > 25000
    assert result["coverage"] == {"available": 50, "total": 50}

    del prices["STOCK49"]
    del prices["STOCK48"]
    assert compute_nifty_cas(rows, prices, now=now)["status"] == "unavailable"


def test_cas_index_rejects_stale_ieps():
    now = datetime.fromisoformat("2026-06-11T15:22:00+05:30")
    rows = _nifty_rows()
    prices = {f"STOCK{i}": (101 + i, now.timestamp() - 60) for i in range(50)}

    result = compute_nifty_cas(rows, prices, now=now)

    assert result["status"] == "unavailable"
    assert result["coverage"]["available"] == 0
