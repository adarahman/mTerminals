# Angel One → Dashboard Live Wiring

## What's actually live vs. not

| Panel | Source | Live? |
|---|---|---|
| Index ticker (NIFTY/BANK NIFTY/VIX) | Angel One SmartAPI WebSocket | Yes, tick-by-tick |
| Sector heatmap stock prices | Angel One SmartAPI WebSocket | Yes, tick-by-tick |
| FII/DII net flow | NSE `fiidiiTradeReact` report | EOD only — NSE publishes once/day, ~5:30–6:30pm IST. Not a broker feed. |
| Participant OI (FII/PRO/CLIENT/DII) | NSE `fao_participant_oi_*.csv` | EOD only, same schedule |
| FII long-short ratio | Derived from the participant OI file above | EOD only |
| IV skew | Not wired | Needs option-chain LTPs (Angel One can give you these) run through a Black-Scholes solver — say the word if you want this added |

Angel One's Market Feeds API is a broker/exchange data feed — it has no concept of FII/DII institutional flow or participant-type OI splits. Those numbers only exist in NSE's own end-of-day disclosure reports.

## Setup

```bash
cd relay
npm install
cp .env.example .env
```

Fill in `.env`:
1. **ANGEL_CLIENT_CODE** — your Angel One trading account ID.
2. **ANGEL_MPIN** — your trading PIN (current SmartAPI login uses this, not your web password).
3. **ANGEL_TOTP_SECRET** — visit https://smartapi.angelbroking.com/enable-totp, log in, and it shows a QR + a text secret. Save the text secret here (not a 6-digit code — the code changes every 30s, the secret doesn't).
4. **ANGEL_API_KEY** — create an app at https://smartapi.angelbroking.com, choose "Market Feeds API" as the type, copy the API key.

Then get the real instrument tokens for the stocks in `WATCHLIST` inside `server.js` — the ones in there now are placeholders. Pull the full instrument list once from:
```
https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
```
and match each symbol's `token` field.

Run it:
```bash
node server.js
```

You should see:
```
[auth] logged in, session established
[angelone] websocket connected
[relay] listening on ws://localhost:8081
[nse] refreshed EOD participant/FII-DII data
```

## Connecting the dashboard

The dashboard HTML already has a `connectRelay()` block pointed at `ws://localhost:8081`. Open the dashboard HTML file **directly in a browser on the same machine running the relay** (or serve both from the same host) — most browsers block a `wss://` page from opening a plain `ws://` (non-secure) connection to localhost due to mixed-content rules, so if you're hosting the dashboard over HTTPS somewhere, either:
- run the relay behind a reverse proxy with a real TLS cert and switch `RELAY_URL` to `wss://your-domain:8081`, or
- serve the dashboard itself locally over plain `http://` during development.

## Known rough edges to watch for

- **Price scaling**: Angel One ticks are commonly paise-scaled (price × 100); `PRICE_DIVISOR = 100` in `server.js` reflects that. First time you run it, sanity-check one LTP against the actual quote and adjust if it's off by 100x.
- **NSE anti-bot blocking**: the participant-OI/FII-DII fetch bootstraps a session cookie against nseindia.com first, same as any scraper has to. If NSE tightens their bot detection, this can start failing — the console will log why. A paid data vendor (e.g. via the SmartAPI Data marketplace or a service like Sensibull) is the reliable long-term source if this becomes a maintenance headache.
- **Sector "buildup" tags** (Long Buildup / Short Covering etc.) need OI-change classification (price up + OI up = long buildup, price up + OI down = short covering, etc.) — the relay currently only computes price % change for the heatmap. Say the word if you want the full 4-quadrant buildup logic added; it needs the previous session's OI per stock to compare against.
