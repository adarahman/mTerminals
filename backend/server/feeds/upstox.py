"""Upstox option-feed subscription resolution."""


def resolve_chain_tokens(symbol, strikes, expiry, *, is_bse, parse_expiry, report):
    from brokers.upstox_client import INDEX_KEYS, get_atm_chain, list_expiries

    symbol = symbol.upper()
    expiries = list_expiries(symbol, exchange="BFO" if is_bse(symbol) else "NFO")
    if not expiries:
        report(f"[upstox] No expiries found for {symbol}, skipping feed")
        return None
    resolved = expiries[0]
    if expiry:
        wanted = parse_expiry(expiry)
        resolved = next((value for value in expiries if parse_expiry(value) == wanted), expiries[0])
    chain = get_atm_chain(symbol, resolved, strikes, exchange="BFO" if is_bse(symbol) else "NFO")
    if not chain:
        report(f"[upstox] Could not build ATM chain for {symbol}, skipping feed")
        return None
    tokens = {row["instrument_key"]: {"strike": row["strike"], "option_type": row["type"]}
              for row in chain["rows"] if row.get("instrument_key")}
    index_key = INDEX_KEYS.get(symbol)
    if index_key:
        tokens[index_key] = {"strike": None, "option_type": "INDEX"}
    else:
        report(f"[upstox] No INDEX_KEYS entry for {symbol}; spot remains REST-polled")
    return tokens, resolved, index_key
