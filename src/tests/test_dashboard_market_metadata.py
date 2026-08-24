from application import dashboard_market_metadata as metadata


def test_symbol_display_name_has_stable_ticker_fallback(monkeypatch):
    metadata._SYMBOL_DISPLAY_NAMES.clear()
    monkeypatch.setitem(__import__("sys").modules, "brokers.upstox.client", None)

    assert metadata.get_symbol_display_name(" custom ") == "CUSTOM"


def test_provider_metadata_has_safe_fallback(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "brokers.market_data_registry", None)

    assert metadata.active_data_source() == "SMARTAPI"
    assert metadata.data_sources_payload() == []
