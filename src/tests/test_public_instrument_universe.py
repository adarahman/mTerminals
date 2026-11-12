from brokers.smartapi.instruments import InstrumentResolver


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


def test_commodity_contract_resolver_selects_nearest_live_mcx_future():
    resolver = InstrumentResolver()
    resolver._instruments = [
        {
            "instrumenttype": "FUTCOM",
            "exch_seg": "MCX",
            "name": "CRUDEOIL",
            "expiry": "31DEC2099",
            "symbol": "CRUDEOILFUT",
            "token": "1",
        },
        {
            "instrumenttype": "FUTCOM",
            "exch_seg": "MCX",
            "name": "CRUDEOIL",
            "expiry": "31JAN2099",
            "symbol": "CRUDEOILFUT",
            "token": "2",
        },
        {
            "instrumenttype": "FUTCOM",
            "exch_seg": "NFO",
            "name": "CRUDEOIL",
            "expiry": "31DEC2099",
            "symbol": "WRONGEXCHANGE",
            "token": "3",
        },
    ]

    assert resolver.get_commodity_futures_contract("CRUDEOIL")["token"] == "2"
    assert resolver.get_commodity_futures_contract("NOTACOMMODITY") is None


def test_commodity_option_resolver_indexes_live_mcX_contracts():
    resolver = InstrumentResolver()
    resolver._instruments = [
        {
            "instrumenttype": "OPTFUT",
            "exch_seg": "MCX",
            "name": "CRUDEOIL",
            "expiry": "31DEC2099",
            "symbol": "CRUDEOIL31DECFUT6000CE",
            "strike": "600000.000000",
            "token": "10",
        },
        {
            "instrumenttype": "OPTFUT",
            "exch_seg": "MCX",
            "name": "CRUDEOIL",
            "expiry": "31DEC2099",
            "symbol": "CRUDEOIL31DECFUT6000PE",
            "strike": "600000.000000",
            "token": "11",
        },
        {
            "instrumenttype": "OPTIDX",
            "exch_seg": "NFO",
            "name": "CRUDEOIL",
            "expiry": "31DEC2099",
            "symbol": "WRONGEXCHANGE6000CE",
            "strike": "600000.000000",
            "token": "12",
        },
    ]

    resolver._build_indexes()

    assert resolver.get_commodity_option_expiries("CRUDEOIL") == ["31DEC2099"]
    assert resolver.get_commodity_option_contracts(
        "CRUDEOIL", "31DEC2099", reference_price=6000
    ) == [
        {
            "strike": 6000.0,
            "type": "CE",
            "symbol": "CRUDEOIL31DECFUT6000CE",
            "token": "10",
        },
        {
            "strike": 6000.0,
            "type": "PE",
            "symbol": "CRUDEOIL31DECFUT6000PE",
            "token": "11",
        },
    ]
