"""Kotak Neo index tokens and F&O underlying universe."""
from __future__ import annotations

from .constants import _INDEX_NAMES, _BSE_INDEX_NAMES
from .scrip_master import _load_fo_scrips


def get_fno_underlyings(force_refresh=False):
    """Indices + F&O stocks derived from the NFO scrip master."""
    del force_refresh

    rows = _load_fo_scrips()

    if not rows:
        return {
            "indices": list(_INDEX_NAMES),
            "stocks": [],
        }

    names = sorted({r["name"] for r in rows})

    indices = sorted(
        n for n in names
        if n in _INDEX_NAMES
    )

    stocks = [
        n for n in names
        if n not in _INDEX_NAMES
    ]

    return {
        "indices": indices or list(_INDEX_NAMES),
        "stocks": stocks,
    }


def index_tokens():
    out = {}

    for name, token in _INDEX_NAMES.items():
        out[name] = {
            "token": token,
            "exchange": "NSE",
        }

    for name, token in _BSE_INDEX_NAMES.items():
        out[name] = {
            "token": token,
            "exchange": "BSE",
        }

    return out
