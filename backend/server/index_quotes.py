"""Provider-aware ticker-strip quote collection."""

from __future__ import annotations

import time


class IndexQuoteFetcher:
    def __init__(self, state, market_data, market_api):
        self.state = state
        self.market_data = market_data
        self.market_api = market_api
        self.warnings = {}

    @staticmethod
    def _float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _map_market(self, entry):
        if not entry:
            return None
        return {"spot": self._float(entry.get("Last Price")), "spotChange": self._float(entry.get("Change")), "spotChgPct": self._float(entry.get("% Change"))}

    def public_nse(self):
        try:
            rows = self.market_api.fetch_all_indices([self.market_api.NSE_INDEX])
            return {row["Symbol"]: quote for row in rows.to_dict("records") if row.get("Symbol") and (quote := self._map_market(row))}
        except Exception as exc:
            print(f"[index-quote] NSE fetch failed: {exc}", flush=True)
            return {}

    def public_bse(self, symbol):
        try:
            return self._map_market(self.market_api.fetch_bse_index_quote(symbol))
        except Exception as exc:
            print(f"[index-quote] {symbol} failed: {exc}", flush=True)
            return None

    def provider(self):
        state = self.state()
        source, out = state["data_source"], {}
        if source in {"UPSTOX", "SHOONYA", "KITE", "BREEZE", "KOTAK"}:
            targets = [("NIFTY", "NIFTY"), ("BANKNIFTY", "BANKNIFTY"), ("MIDCPNIFTY", "MIDCPNIFTY"), ("INDIA VIX", "INDIAVIX" if source == "UPSTOX" else "INDIA VIX"), ("SENSEX", "SENSEX")]
            for key, lookup in targets:
                try:
                    row = self.market_data.get_spot_quote(lookup)
                except Exception as exc:
                    if time.monotonic() - self.warnings.get(f"{source}:{key}", 0) >= 60:
                        self.warnings[f"{source}:{key}"] = time.monotonic()
                        print(f"[index-quote] {source.lower()} {key} failed: {exc}", flush=True)
                    continue
                if row and row.get("ltp") is not None:
                    ltp, close = row["ltp"], row.get("close")
                    change = round(ltp - close, 2) if close else 0.0
                    out[key] = {"Symbol": key, "Last Price": ltp, "Change": change, "% Change": round(change / close * 100, 2) if close else 0.0}
            return out
        if source == "NSE_BSE":
            return out
        tokens = self.market_data.index_tokens()
        pairs = [(name, tokens[name]["token"]) for name in ("NIFTY", "BANKNIFTY", "MIDCPNIFTY") if name in tokens]
        pairs.append((state["vix_symbol"], state["vix_token"]))
        try:
            raw = self.market_data.get_batch_quotes_by_token("NSE", pairs, mode="FULL")
        except Exception as exc:
            print(f"[index-quote] smartapi NSE batch failed: {exc}", flush=True)
            raw = {}
        for name, token in pairs:
            if row := raw.get(str(token)):
                out["INDIA VIX" if name == state["vix_symbol"] else name] = {"spot": self._float(row.get("ltp")), "spotChange": self._float(row.get("netChange")), "spotChgPct": self._float(row.get("percentChange"))}
        return out
