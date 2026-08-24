from market.quotes.vix_service import resolve_vix


def _safe_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def test_resolve_vix_prefers_broker_quote_without_public_call():
    public_calls = []

    result = resolve_vix(
        {"ltp": 15, "close": 12},
        public_loader=lambda: public_calls.append(True),
        safe_number=_safe_number,
        warn=lambda *_args: None,
    )

    assert result == (15.0, 25.0)
    assert public_calls == []


def test_resolve_vix_uses_public_fallback_and_warns():
    warnings = []

    result = resolve_vix(
        None,
        public_loader=lambda: (14.5, -1.2, {}),
        safe_number=_safe_number,
        warn=lambda key, message: warnings.append((key, message)),
    )

    assert result == (14.5, -1.2)
    assert warnings[0][0] == "vix:public-fallback"


def test_resolve_vix_returns_missing_contract_when_both_sources_fail():
    warnings = []

    result = resolve_vix(
        None,
        public_loader=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
        safe_number=_safe_number,
        warn=lambda key, message: warnings.append(key),
    )

    assert result == (None, 0.0)
    assert warnings == ["vix:public-fallback", "vix:missing"]
