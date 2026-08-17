from brokers.smartapi_instruments import InstrumentResolver


def test_public_master_splits_full_fno_universe_by_future_type(tmp_path):
    resolver = InstrumentResolver(cache_dir=tmp_path)
    resolver._instruments = [
        {"instrumenttype": "FUTIDX", "exch_seg": "NFO", "name": "NIFTY", "lotsize": "65"},
        {"instrumenttype": "FUTIDX", "exch_seg": "BFO", "name": "SENSEX", "lotsize": "20"},
        {"instrumenttype": "FUTSTK", "exch_seg": "NFO", "name": "RELIANCE", "lotsize": "500"},
        {"instrumenttype": "FUTSTK", "exch_seg": "NFO", "name": "SBIN", "lotsize": "750"},
        {"instrumenttype": "FUTIDX", "exch_seg": "NFO", "name": "011NSETEST", "lotsize": "1"},
        {"instrumenttype": "FUTSTK", "exch_seg": "MCX", "name": "GOLD", "lotsize": "1"},
    ]

    resolver._build_indexes()

    assert resolver.get_fno_underlyings() == {
        "indices": ["NIFTY", "SENSEX"],
        "stocks": ["RELIANCE", "SBIN"],
    }
