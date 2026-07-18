"""
Run this from the same directory/venv as ws_server_live.py:

    python3 diag_lotsize.py

It will NOT hit the network if today's cache file already exists
(.angelone_cache/instrument_master_<today>.json) — it reads whatever
smartapi_instruments.InstrumentResolver would read.

Lot sizes are resolved from FUTSTK/FUTIDX only (not CE/PE options).
"""
from smartapi_instruments import InstrumentResolver

resolver = InstrumentResolver().load()

print(f"Total instruments in master: {len(resolver._instruments)}")
print(f"FUT-derived lot sizes indexed: {len(resolver._lot_size_map)}")

# 1) Spot-check key underlyings
for sym in [
    "NIFTY", "BANKNIFTY", "MIDCPNIFTY", "SENSEX", "FINNIFTY",
    "BANKEX", "SENSEX50", "SBIN", "RELIANCE", "TCS", "PNB",
]:
    print(f"\n--- {sym} ---")
    try:
        lot = resolver.get_lot_size(sym)
        print(f"  get_lot_size({sym!r}) -> {lot}  (from FUTSTK/FUTIDX)")
    except Exception as e:
        print(f"  get_lot_size({sym!r}) raised: {e}")

    # Cross-check first option contract lotsize matches FUT (sanity)
    expiries = resolver.get_available_expiries(sym)
    if expiries:
        nearest = expiries[0]
        chain = resolver.get_option_chain_tokens(sym, nearest)
        if chain:
            try:
                opt_lot = int(float(chain[0].get("lotsize") or 0))
            except (TypeError, ValueError):
                opt_lot = None
            print(f"  nearest OPT expiry {nearest}: {len(chain)} contracts, "
                  f"first OPT lotsize={opt_lot}")
            if opt_lot is not None and lot is not None and opt_lot != lot:
                print(f"  WARNING: FUT lot {lot} != OPT lot {opt_lot}")

# 2) Sample of stock F&O underlyings
print("\n=== sample stock lot sizes (first 15 alpha) ===")
stocks = sorted(
    s for s in resolver._lot_size_map
    if s not in {
        "NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50",
        "SENSEX", "BANKEX", "SENSEX50",
    }
)
for s in stocks[:15]:
    print(f"  {s:16s} -> {resolver._lot_size_map[s]}")

