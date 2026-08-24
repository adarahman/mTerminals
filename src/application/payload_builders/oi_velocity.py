"""OI-velocity dashboard window construction."""

from .common import integer, rounded_number

WINDOWS = (5, 15, 30)


def build_oi_velocity(engine_result=None):
    velocity = [{"window": window, "rows": []} for window in WINDOWS]
    if engine_result is None:
        return velocity

    frame = getattr(engine_result, "vel_df", None)
    if frame is None or frame.empty:
        return velocity

    for window_block in velocity:
        window = window_block["window"]
        subset = frame[frame["window"] == window] if "window" in frame.columns else frame
        window_block["rows"] = [
            {
                "strike": integer(row.get("strike", 0)),
                "ceNow": integer(row.get("ceNow", row.get("ce_oi", 0))),
                "ceDOI": integer(row.get("ceDOI", row.get("ce_doi", 0))),
                "ceLTP": rounded_number(row.get("ceLTP", row.get("ce_ltp", 0)), 1),
                "peNow": integer(row.get("peNow", row.get("pe_oi", 0))),
                "peDOI": integer(row.get("peDOI", row.get("pe_doi", 0))),
                "peLTP": rounded_number(row.get("peLTP", row.get("pe_ltp", 0)), 1),
                "signal": str(row.get("signal", "")),
            }
            for row in subset.to_dict("records")
        ]
    return velocity
