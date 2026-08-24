"""Greeks-table dashboard row construction."""

from .common import integer, nullable_rounded_number


def build_greeks_rows(greeks_table):
    if greeks_table is None or getattr(greeks_table, "empty", True):
        return []
    rows = []
    for row in greeks_table.to_dict("records"):
        rows.append(
            {
                "strike": integer(row.get("Strike", row.get("strike", 0))),
                "iv": nullable_rounded_number(row.get("iv"), 2),
                "cDelta": nullable_rounded_number(row.get("cDelta"), 4),
                "cGamma": nullable_rounded_number(row.get("cGamma"), 4),
                "cTheta": nullable_rounded_number(row.get("cTheta"), 4),
                "cVega": nullable_rounded_number(row.get("cVega"), 4),
                "pDelta": nullable_rounded_number(row.get("pDelta"), 4),
                "pGamma": nullable_rounded_number(row.get("pGamma"), 4),
                "pTheta": nullable_rounded_number(row.get("pTheta"), 4),
                "pVega": nullable_rounded_number(row.get("pVega"), 4),
                "netGEX": nullable_rounded_number(row.get("netGEX"), 4),
            }
        )
    return rows
