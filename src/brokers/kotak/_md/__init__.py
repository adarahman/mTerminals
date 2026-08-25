"""Internal subpackage for Kotak Neo market-data implementation.

Split out of the original ``brokers/kotak/market_data.py`` monolith so the
900-line module is decomposed into focused pieces (constants, scrip master,
contracts, quotes, symbols). The public surface remains the
``brokers.kotak.market_data`` *module* (a sibling of every other broker's
``market_data.py``), which re-exports from here and defines the
monkeypatch-sensitive orchestration functions.
"""
