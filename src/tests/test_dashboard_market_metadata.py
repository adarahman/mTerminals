from application import dashboard_market_metadata as metadata


def test_symbol_display_name_has_stable_ticker_fallback(monkeypatch):
    metadata._SYMBOL_DISPLAY_NAMES.clear()
    monkeypatch.setitem(__import__("sys").modules, "brokers.upstox.client", None)

    assert metadata.get_symbol_display_name(" custom ") == "CUSTOM"


def test_data_source_quotes_are_enriched_for_the_current_symbol(monkeypatch):
    from application import provider_indicative_quotes
    from brokers import market_data_registry

    monkeypatch.setattr(
        market_data_registry,
        "provider_status",
        lambda: [{"id": "KITE", "label": "ZERODHA", "status": "AVAILABLE"}],
    )
    provider_indicative_quotes.store_provider_spot_quotes(
        "NIFTY",
        {"KITE": {"price": 25123.45, "asOf": "2026-11-09T10:00:00+05:30"}},
    )

    assert metadata.data_sources_payload("NIFTY") == [
        {
            "id": "KITE",
            "label": "ZERODHA",
            "status": "AVAILABLE",
            "indicativePrice": 25123.45,
            "indicativeAsOf": "2026-11-09T10:00:00+05:30",
        }
    ]
    assert metadata.data_sources_payload("BANKNIFTY")[0].get("indicativePrice") is None


def test_provider_metadata_has_safe_fallback(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "brokers.market_data_registry", None)

    assert metadata.active_data_source() == "SMARTAPI"
    assert metadata.data_sources_payload() == []
