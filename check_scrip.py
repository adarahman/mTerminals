import json, sys
path = sys.argv[1] if len(sys.argv) > 1 else "instrument_master_2026-08-18.json"
with open(path, "rb") as f:
    data = json.load(f)
for name in ("NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX"):
    rows = [r for r in data if r.get("instrumenttype") == "AMXIDX" and (r.get("name") or "").upper() == name]
    print(f"{name}: {len(rows)} AMXIDX row(s)")
    for r in rows:
        print("   ", r)
