"""Tolerant matching of user-typed symbols to instrument-master `name`s.

The Dashboard's symbol picker accepts free-form input (its "Other…" prompt),
so a user commonly types a full company name — "ZYDUS LIFESCIENCES LTD" —
while a given broker master stores either the exchange ticker ("ZYDUSLIFE",
Angel) or the full company name ("ZYDUS LIFESCIENCES LTD", Upstox). Exact
name lookups then miss and every expiry/chain/quote call returns empty.

`canonicalize_underlying()` maps a request to the exact key a master uses,
given that master's own set of keys. Pure function — no broker imports, so
any module can call it. It never guesses: ambiguous or unresolvable requests
return None (the caller then falls back to the exact-match behavior, which
fails loudly with a useful error instead of silently routing to the wrong
underlying).
"""

import re

# Corporate designators stripped from the end of a typed company name before
# comparing, so "ZYDUS LIFESCIENCES LTD" and "MARUTI SUZUKI INDIA LTD." both
# condense to their meaningful core. Trailing punctuation (a stray "." after
# "LTD.", as Upstox's master emits) is allowed so the two spellings collapse.
_SUFFIX_RE = re.compile(
    r"(?:\s*(?:LTD|LIMITED|LT|PLC|INC|CORP|CORPORATION|CO|INDIA|INDUSTRIES"
    r"|HOLDINGS|HOLDING|LABS|LABORATORIES|&?\s*CO))*[\.\s]*$"
)

_PUNCT_RE = re.compile(r"[^A-Z0-9]+")

# Minimum length for a ticker-ish alias seed. Short seeds like "LT" match too
# many companies (L&T, L&T Finance, Laxmi, ...) and can only be resolved from
# a trading_symbol match, not name-prefix matching — so refuse rather than
# misroute.
_MIN_SEED_LEN = 4

# Well-known full-company-name -> exchange-ticker pairs that a prefix/condense
# heuristic cannot derive (the ticker isn't a prefix of the condensed name,
# e.g. "INFY" vs "INFOSYSSERVICESLIMITED"). Used as a LAST-RESORT alias table
# so the Dashboard's free-text "Other…" prompt still resolves common stocks on
# every broker master — Angel's master only stores the ticker, so a full
# company name can't be reverse-engineered from it at all. Keys are the most
# common public spellings; wrong answers here corrupt the symbol, so only
# include entries that are effectively unambiguous.
_COMMON_UNDERLYING_ALIASES = {
    "INFOSYS LIMITED": "INFY",
    "INFOSYS LTD": "INFY",
    "TATA CONSULTANCY SERVICES": "TCS",
    "TATA CONSULTANCY SERV LTD": "TCS",
    "ICICI BANK LTD": "ICICIBANK",
    "ICICI BANK LIMITED": "ICICIBANK",
    "HDFC BANK LTD": "HDFCBANK",
    "HDFC BANK LIMITED": "HDFCBANK",
    "HINDUSTAN UNILEVER LTD": "HINDUNILVR",
    "HUL LTD": "HINDUNILVR",
    "KOTAK MAHINDRA BANK LTD": "KOTAKBANK",
    "WIPRO LTD": "WIPRO",
    "WIPRO LIMITED": "WIPRO",
    "ADANI ENERGY SOLUTION LTD": "ADANIENSOL",
    "ADANI ENERGY SOLUTIONS LTD": "ADANIENSOL",
    "ADANI ENERGY SOLUTIONS LIMITED": "ADANIENSOL",
    # Truncated/garbled master names still reach this point un-condensable by
    # the prefix heuristic (e.g. a copy-pasted full name that lost its middle
    # tokens). Map the recognizable leading fragment to the exchange ticker.
    "NIPPON LIFE INDIA AMERICAN REINSURANCE LTD": "INDNIPPON",
    "NIPPON L I A M LTD": "INDNIPPON",
    "NIPPON LIFE INDIA AMERICAN REINSURANCE": "INDNIPPON",
    "NIPPON L I A M": "INDNIPPON",
}


def _condense(value: str) -> str:
    """Uppercase, strip trailing corporate designators, drop punctuation."""
    value = (value or "").upper()
    value = _SUFFIX_RE.sub("", value)
    return _PUNCT_RE.sub("", value)


def canonicalize_underlying(underlying, known_keys) -> "str | None":
    """Return the exact master key for a user-typed underlying, or None.

    `known_keys` is either an iterable of the master's own keys (uppercased
    `name` values, or trading symbols for symbol-keyed lookups), or a dict
    of {key: canonical} — when a dict is given the CANONICAL VALUE is
    returned for the matched key (use the dict form to map aliases, e.g.
    Upstox option-row tickers back to that master's company name).

    A candidate name matches the request when its condensed form relates to
    the condensed request by ONE of:
      - equality (exact / suffix-normalized: "ZYDUSLIFE"=="ZYDUSLIFE"),
      - name-condensed being a PREFIX of the request-condensed (user typed a
        fuller name: "ZYDUS LIFESCIENCES LTD" -> "ZYDUSLIFE" is a prefix), or
      - the request-condensed being a PREFIX of the name-condensed (user typed
        a ticker: "TATAMOTORS" is a prefix of "TATAMOTORSLIMITED").
    Only returns when the match is UNIQUE — several candidates means the
    request is ambiguous and we refuse rather than guess.
    """
    req = (underlying or "").strip().upper()
    if not req:
        return None

    if isinstance(known_keys, dict):
        mapping = {k.upper().strip(): v for k, v in known_keys.items() if k}
        keys = list(mapping.keys())
    else:
        mapping = None
        keys = [k.upper().strip() for k in known_keys if k]

    # 1. exact request (already canonical).
    if req in keys:
        return mapping[req] if mapping is not None else req

    condensed = _condense(req)
    if not condensed:
        return None

    pairs = ((k, _condense(k)) for k in keys)
    # Track the CANONICAL value matched (not the raw key), so a canonical
    # bound to several alias keys (e.g. Upstox's "HDFC BANK LTD" reachable
    # both via its full name and via the "HDFCBANK" ticker alias) still
    # counts as a single match instead of being treated as ambiguous.
    seen = set()
    for k, c in pairs:
        if not c:
            continue
        matched = False
        # 2. condensed equality (e.g. "TATA STEEL LTD" vs "TATA STEEL LIMITED").
        if c == condensed:
            matched = True
        # 3. full name -> ticker: the ticker (shorter) is a prefix of the
        #    fuller condensed name ("ZYDUSLIFE" is prefix of "ZYDUSLIFESCIENCES").
        elif c.startswith(condensed) and len(condensed) >= _MIN_SEED_LEN:
            matched = True
        # 4. ticker -> full name: the ticker (request) is a prefix of the
        #    fuller condensed name ("TATAMOTORS" is prefix of "TATAMOTORSLIMITED").
        elif condensed.startswith(c) and len(c) >= _MIN_SEED_LEN:
            matched = True
        if matched:
            canon = mapping[k] if mapping is not None else k
            seen.add(canon)

    if len(seen) == 1:
        return seen.pop()
    return None
