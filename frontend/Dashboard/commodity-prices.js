class CommodityPricesView {
  constructor() {
    this.symbol = 'CRUDEOIL';
    this.expiry = '';
    this.futureExpiry = '';
    this.futureExpiries = [];
    this.futureExpiryCache = new Map();
    this.futureExpiryLoads = new Map();
    this.timer = null;
    this.chainTimer = null;
    this.chainRetryTimer = null;
    this.newsTimer = null;
    this.inventoryTimer = null;
    this.inventoryLoading = false;
    this.quoteLoadingSymbols = new Set();
    this.quoteRefreshPendingSymbols = new Set();
    this.chainLoading = false;
    this.chainRefreshPendingSymbol = '';
    this.chainLoads = new Map();
    this.newsLoading = false;
    this.newsRefreshPending = false;
    this.latestQuote = null;
    this.lastLiveQuoteAt = 0;
    this.latestQuoteError = '';
    this.latestProvider = '';
    this.latestChainPayload = null;
    this.latestAnalysis = {};
    this.quoteCache = new Map();
    this.chainCache = new Map();
    this.optionsUnavailableUntil = new Map();
  }

  open(symbol) {
    if (symbol) {
      const nextSymbol = String(symbol).toUpperCase();
      if (nextSymbol !== this.symbol) this._resetForSymbol(nextSymbol);
      this.symbol = nextSymbol;
      this.expiry = '';
    }
    window.commodityActiveSymbol = this.symbol;
    this._restoreCachedSymbolView(this.symbol);
    this.renderDashboard();
    if (this.timer !== null) window.clearInterval(this.timer);
    if (this.chainTimer !== null) window.clearInterval(this.chainTimer);
    if (this.chainRetryTimer !== null) window.clearTimeout(this.chainRetryTimer);
    this.chainRetryTimer = null;
    if (this.newsTimer !== null) window.clearInterval(this.newsTimer);
    if (this.inventoryTimer !== null) window.clearInterval(this.inventoryTimer);
    this.refresh();
    this.timer = window.setInterval(() => this._refreshQuote(this.symbol), 1000);
    this.chainTimer = window.setInterval(() => this._refreshOptionChain(this.symbol), 10000);
    this.refreshNews();
    this.newsTimer = window.setInterval(() => this.refreshNews(), 300000);
    this.refreshInventory();
    this.inventoryTimer = window.setInterval(() => this.refreshInventory(), 1800000);
  }

  close() {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    if (this.chainTimer !== null) {
      window.clearInterval(this.chainTimer);
      this.chainTimer = null;
    }
    if (this.chainRetryTimer !== null) {
      window.clearTimeout(this.chainRetryTimer);
      this.chainRetryTimer = null;
    }
    if (this.newsTimer !== null) {
      window.clearInterval(this.newsTimer);
      this.newsTimer = null;
    }
    if (this.inventoryTimer !== null) {
      window.clearInterval(this.inventoryTimer);
      this.inventoryTimer = null;
    }
    // Rescue the persistent expiry pickers before the equity top-bar
    // rebuild. They only exist as single DOM nodes (created once in
    // DashboardPro.html inside the hidden holders); while in commodity mode
    // they live inside #sec-topbar, and the equity render path replaces that
    // element via outerHTML — destroying the <select> nodes outright. Once
    // destroyed they can never be re-inserted until a full page reload.
    const expiryPicker = document.getElementById('commodity-expiry');
    const expiryHolder = document.getElementById('commodity-expiry-holder');
    if (expiryPicker && expiryHolder) expiryHolder.appendChild(expiryPicker);
    const futurePicker = document.getElementById('commodity-futures-expiry');
    const futureHolder = document.getElementById('commodity-futures-expiry-holder');
    if (futurePicker && futureHolder) futureHolder.appendChild(futurePicker);
    // Return the stats card to its home inside the dashboard slot so the
    // equity render doesn't inherit a stray #dashboard child.
    const priceCard = document.querySelector('.commodity-price-card:not(#equity-momentum-stats)');
    const pricesContent = document.querySelector('.commodity-prices-content');
    if (priceCard && pricesContent && !pricesContent.contains(priceCard)) {
      pricesContent.prepend(priceCard);
    }
  }

  select(symbol) {
    const nextSymbol = String(symbol || '').toUpperCase();
    if (nextSymbol !== this.symbol) this._resetForSymbol(nextSymbol);
    this.symbol = nextSymbol;
    window.commodityActiveSymbol = nextSymbol;
    this._restoreCachedSymbolView(nextSymbol);
    this.renderDashboard();
    this.refresh();
    this.refreshNews();
    this.refreshInventory();
  }

  selectExpiry(expiry) {
    this.expiry = String(expiry || '').toUpperCase();
    this._refreshOptionChain(this.symbol);
  }

  selectFutureExpiry(expiry) {
    const selected = String(expiry || '');
    if (!selected || selected === this.futureExpiry) return;
    this.futureExpiry = selected;
    this._refreshQuote(this.symbol);
  }

  _setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  }

  _markSelectedCommodityFeedFresh(symbol) {
    if (typeof app !== 'undefined' && app.data
        && typeof app.data.markExternalCommodityQuote === 'function') {
      app.data.markExternalCommodityQuote(symbol);
    }
  }

  _resetForSymbol(symbol) {
    this.latestQuote = null;
    this.lastLiveQuoteAt = 0;
    this.latestQuoteError = '';
    this.latestProvider = '';
    this.latestChainPayload = null;
    this.latestAnalysis = {};
    this.futureExpiry = '';
    this.futureExpiries = [];
    const cached = this.quoteCache.get(symbol);
    const cachedQuoteIsFresh = cached && Date.now() - cached.receivedAt < 30000;
    this.latestQuote = cachedQuoteIsFresh ? { ...cached.quote } : null;
    this.latestProvider = cachedQuoteIsFresh ? cached.provider : '';
    this._setText('commodity-price-ltp', cachedQuoteIsFresh ? this._format(cached.quote.LTP) : '—');
    const ltp = document.getElementById('commodity-price-ltp');
    if (ltp) ltp.classList.remove('up', 'down');
    const change = document.getElementById('commodity-price-change');
    if (change) {
      const amount = Number(cachedQuoteIsFresh && cached.quote.Change);
      const percent = Number(cachedQuoteIsFresh && cached.quote.PctChange);
      change.textContent = Number.isFinite(amount) && Number.isFinite(percent)
        ? `${amount >= 0 ? '+' : ''}${this._format(amount)} ${percent >= 0 ? '▲' : '▼'} ${this._format(Math.abs(percent))}%`
        : '—';
      change.classList.remove('up', 'down');
      change.classList.toggle('up', Number.isFinite(percent) && percent >= 0);
      change.classList.toggle('down', Number.isFinite(percent) && percent < 0);
    }
    if (ltp && cachedQuoteIsFresh) {
      const percent = Number(cached.quote.PctChange);
      ltp.classList.toggle('up', Number.isFinite(percent) && percent >= 0);
      ltp.classList.toggle('down', Number.isFinite(percent) && percent < 0);
    }
    [
      'commodity-price-change-5m', 'commodity-price-oi-change-5m',
      'commodity-price-volume-change-5m',
    ].forEach((id) => this._setText(id, '—'));
    this._setText('commodity-price-lot-size', cachedQuoteIsFresh
      ? this._format(cached.quote.LotSize, 0) : '—');
    const priceStatus = document.getElementById('commodity-price-status');
    if (priceStatus) {
      priceStatus.dataset.tone = cachedQuoteIsFresh ? 'stale' : '';
      priceStatus.dataset.status = cachedQuoteIsFresh ? 'stale' : 'connecting';
      priceStatus.textContent = cachedQuoteIsFresh ? 'UPDATING' : 'CONNECTING';
      priceStatus.title = cachedQuoteIsFresh
        ? `Showing the latest ${symbol} quote while refreshing the feed.`
        : `Waiting for ${symbol} quote.`;
    }
    this._setText('commodity-updated-time', cachedQuoteIsFresh
      ? new Date(cached.receivedAt).toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit',
      }) : '—');
    const expiryPicker = document.getElementById('commodity-expiry');
    if (expiryPicker) expiryPicker.replaceChildren();
    const futureExpiryPicker = document.getElementById('commodity-futures-expiry');
    if (futureExpiryPicker) {
      futureExpiryPicker.replaceChildren(new Option('Loading futures…', ''));
      futureExpiryPicker.disabled = true;
    }
    const chainStatus = document.getElementById('commodity-chain-status');
    if (chainStatus) {
      chainStatus.dataset.tone = '';
      chainStatus.textContent = `Refreshing ${symbol} option chain…`;
    }
  }

  _restoreCachedSymbolView(symbol) {
    const cached = this.chainCache.get(symbol);
    const isUsable = cached && Date.now() - cached.receivedAt < 300000;
    if (!isUsable) {
      this.latestChainPayload = null;
      this.latestAnalysis = {};
      this.expiry = '';
      this._clearOptionChain();
      return;
    }
    this.latestChainPayload = cached.payload;
    this.latestAnalysis = cached.payload.analysis || {};
    this.expiry = cached.payload.expiry || '';
    this._renderSharedOptionChain(cached.payload);
    this._renderAnalysis(this.latestAnalysis);
    const status = document.getElementById('commodity-chain-status');
    if (status) {
      status.dataset.tone = 'stale';
      status.textContent = `Updating ${symbol} option chain…`;
    }
  }

  _clearOptionChain() {
    this.latestChainPayload = null;
    const host = document.getElementById('commodity-chain-shared');
    if (host) host.replaceChildren();
    this._renderSharedOptionChain(null);
    this._renderAnalysis({});
  }

  _showOptionsUnavailable(symbol) {
    this.optionsUnavailableUntil.set(symbol, Date.now() + 30000);
    if (this.chainRetryTimer !== null) {
      window.clearTimeout(this.chainRetryTimer);
      this.chainRetryTimer = null;
    }
    this.latestChainPayload = null;
    this.latestAnalysis = {};
    this.expiry = '';
    const picker = document.getElementById('commodity-expiry');
    if (picker) {
      picker.replaceChildren(new Option('Options unavailable', ''));
      picker.disabled = true;
    }
    this._clearOptionChain();
    this._renderAnalysis({ optionsUnavailable: true });
    const status = document.getElementById('commodity-chain-status');
    if (status) {
      status.dataset.tone = 'unavailable';
      status.textContent = `Options unavailable for ${symbol}.`;
    }
  }

  _quoteMatchesSelectedSymbol(quote) {
    return this._quoteMatchesSymbol(quote, this.symbol);
  }

  _quoteMatchesSymbol(quote, symbol) {
    if (!quote || typeof quote !== 'object') return false;
    symbol = String(symbol || '').trim().toUpperCase();
    if (!symbol) return false;
    const underlying = String(quote.Underlying || '').trim().toUpperCase();
    if (underlying && underlying !== symbol) return false;
    const contract = String(quote.Contract || '').trim().toUpperCase();
    if (contract) {
      const rest = contract.slice(symbol.length);
      if (!contract.startsWith(symbol) || !rest || !/^[-\d]/.test(rest)) return false;
    }
    return true;
  }

  _format(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number)
      ? number.toLocaleString('en-IN', { maximumFractionDigits: digits })
      : '—';
  }

  // Reject foreign-instrument prints (seen live as NATURALGAS flashing
  // ~143k-154k — real SILVER ticks leaking through Angel's flaky feed).
  // Three layers, mirroring the equity _sanitizeSpotTick guard:
  //  1. Identity — a quote whose own Underlying/Contract names a different
  //     commodity is rejected outright (REST rows carry the true symbol).
  //  2. Stateless PrevClose sanity — >10x away is impossible for an MCX
  //     future, catches bad prints before any baseline exists.
  //  3. Deviation >5% vs last accepted must persist ~2.5s at the same
  //     level (real gap) — foreign ticks vary or get replaced, and an
  //     instantly-accepted bad first tick can no longer poison the
  //     baseline so the whole wrong instrument's range stays accepted.
  _quoteGuardKey() {
    return `${this.symbol}|${this._normalizeExpiry(this.futureExpiry) || 'default'}`;
  }

  _acceptQuoteTick(quote) {
    const ltp = Number(quote && quote.LTP);
    if (!Number.isFinite(ltp) || ltp <= 0) return false;
    const underlying = String(quote.Underlying || '').trim().toUpperCase();
    if (underlying && underlying !== this.symbol) return false;
    const contract = String(quote.Contract || '').trim().toUpperCase();
    if (contract) {
      // 'NATURALGAS28DEC26FUT' matches 'NATURALGAS'; 'GOLDM30NOV26FUT'
      // must NOT match 'GOLD' — require a digit boundary after the symbol.
      const rest = contract.slice(this.symbol.length);
      if (!contract.startsWith(this.symbol) || !/^\d/.test(rest)) return false;
    }
    const prevClose = Number(quote.PrevClose);
    if (Number.isFinite(prevClose) && prevClose > 0
        && (ltp > prevClose * 10 || ltp < prevClose / 10)) {
      return false;
    }
    const key = this._quoteGuardKey();
    const guard = this._quoteGuard = this._quoteGuard || { last: {}, cand: {} };
    const prev = Number(guard.last[key]);
    const deviates = Number.isFinite(prev) && prev > 0
      && Math.abs(ltp - prev) / prev * 100 > 5;
    if (!deviates) {
      guard.cand[key] = null;
      guard.last[key] = ltp;
      return true;
    }
    const now = Date.now();
    const cand = guard.cand[key];
    const sameLevel = !!cand && Math.abs(ltp - cand.ltp) / cand.ltp * 100 <= 1;
    if (sameLevel && now - cand.firstSeen >= 2500) {
      guard.cand[key] = null;
      guard.last[key] = ltp;
      return true;
    }
    guard.cand[key] = sameLevel ? cand : { ltp, firstSeen: now };
    return false;
  }

  applyLiveQuote(payload) {
    if (window.dashboardInstrumentMode !== 'commodity' || !payload) return;
    const quote = payload.quote || {};
    const symbol = String(payload.symbol || '').trim().toUpperCase();
    const ltp = Number(quote.LTP);
    if (!COMMODITY_SYMBOLS.includes(symbol) || !Number.isFinite(ltp) || ltp <= 0
        || !this._quoteMatchesSymbol(quote, symbol)) return;
    if (symbol !== this.symbol) {
      this.quoteCache.set(symbol, {
        quote: { ...quote },
        provider: payload.provider || 'SMARTAPI',
        receivedAt: Date.now(),
      });
      return;
    }
    if (this.futureExpiry
        && this._normalizeExpiry(quote.Expiry) !== this._normalizeExpiry(this.futureExpiry)) return;
    // SMARTAPI_WS ticks bypass the REST getMarketData identity check —
    // Angel's WS feed can attach a foreign instrument's LTP to our token.
    // The quote's Underlying/Contract are OUR metadata here, so identity
    // can't catch it; cross-validate against the last REST-accepted LTP
    // instead (REST polls every 1s and is identity-checked end to end).
    if (quote.FutSource === 'SMARTAPI_WS') {
      const wsLtp = Number(quote.LTP);
      const restLtp = Number(((this._restLtpByKey) || {})[this._quoteGuardKey()]);
      if (Number.isFinite(wsLtp) && wsLtp > 0 && Number.isFinite(restLtp) && restLtp > 0
          && Math.abs(wsLtp - restLtp) / restLtp * 100 > 5) return;
    }
    if (!this._acceptQuoteTick(quote)) return;
    this.quoteCache.set(symbol, {
      quote: { ...quote },
      provider: payload.provider || 'SMARTAPI',
      receivedAt: Date.now(),
    });
    this._markSelectedCommodityFeedFresh(this.symbol);
    this.lastLiveQuoteAt = Date.now();
    // Copy: the WS store merges every symbol's tick into the same object, so
    // keeping a reference lets a later COPPER/GOLD tick overwrite this quote.
    this.latestQuote = { ...quote };
    this.latestQuoteError = '';
    this.latestProvider = payload.provider || 'SMARTAPI';
    this._syncContractLabel(quote);
    this._setText('commodity-price-ltp', this._format(quote.LTP));
    const change = quote.Change == null ? NaN : Number(quote.Change);
    const percent = quote.PctChange == null ? NaN : Number(quote.PctChange);
    const ltpElement = document.getElementById('commodity-price-ltp');
    if (ltpElement) {
      ltpElement.classList.toggle('up', Number.isFinite(percent) && percent >= 0);
      ltpElement.classList.toggle('down', Number.isFinite(percent) && percent < 0);
    }
    const changeElement = document.getElementById('commodity-price-change');
    if (changeElement) {
      changeElement.textContent = Number.isFinite(change) && Number.isFinite(percent)
        ? `${change >= 0 ? '+' : ''}${this._format(change)} ${percent >= 0 ? '▲' : '▼'} ${this._format(Math.abs(percent))}%`
        : '—';
      changeElement.classList.toggle('up', Number.isFinite(percent) && percent >= 0);
      changeElement.classList.toggle('down', Number.isFinite(percent) && percent < 0);
    }
    this._setText('commodity-price-lot-size', this._format(quote.LotSize, 0));
    const status = document.getElementById('commodity-price-status');
    if (status) {
      status.dataset.tone = 'live';
      status.dataset.status = 'live';
      status.textContent = 'LIVE';
      status.title = `Live MCX quote via ${this.latestProvider}`;
    }
    this._setText('commodity-updated-time', new Date().toLocaleTimeString('en-IN', {
      timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit',
    }));
    this._syncSharedQuoteContext();
  }

  _syncSharedQuoteContext() {
    if (window.dashboardInstrumentMode !== 'commodity') return;
    const sharedData = this._toSharedDashboardData();
    if (typeof _data !== 'undefined') _data = sharedData;
    const chainView = typeof app !== 'undefined' && app.chain;
    if (chainView && typeof chainView.patchCommodityMiniChart === 'function') {
      chainView.patchCommodityMiniChart(sharedData);
    }
    if (window.priceChart && Number.isFinite(sharedData.spot) && sharedData.spot > 0) {
      window.priceChart.addTick(sharedData.spot, Date.now(), null, {
        symbol: this.symbol,
        instrument: 'MCX_FUT',
        expiry: this.futureExpiry,
      });
    }
    const priceChartModal = document.getElementById('price-chart-modal');
    const modalManager = typeof app !== 'undefined' && app.modal;
    if (priceChartModal?.classList.contains('open')
        && modalManager && typeof modalManager.updatePriceChartLiveContext === 'function') {
      modalManager.updatePriceChartLiveContext(sharedData);
    }
    const modal = document.getElementById('option-chain-modal');
    if (modal?.classList.contains('open') && typeof app !== 'undefined' && app.modal) {
      app.modal.renderOptionChainContext(sharedData);
    }
  }

  async refresh() {
    if (window.dashboardInstrumentMode !== 'commodity') return;
    const requestedSymbol = this.symbol;
    // Start the chain immediately. Waiting for the quote request first adds
    // the full broker round-trip to every symbol change; the chain adapter
    // can resolve ATM from its own provider data while both requests run.
    this._refreshFutureExpiries(requestedSymbol);
    this._refreshOptionChain(requestedSymbol);
    this._refreshQuote(requestedSymbol);
  }

  _normalizeExpiry(expiry) {
    const value = String(expiry || '').trim().toUpperCase();
    const match = /^(\d{1,2})-?([A-Z]{3})-?(\d{4})$/.exec(value);
    return match ? `${match[1].padStart(2, '0')}${match[2]}${match[3]}` : value;
  }

  _expiryTime(expiry) {
    const normalized = this._normalizeExpiry(expiry);
    const match = /^(\d{2})([A-Z]{3})(\d{4})$/.exec(normalized);
    if (!match) return Number.POSITIVE_INFINITY;
    const month = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC']
      .indexOf(match[2]);
    return month < 0
      ? Number.POSITIVE_INFINITY
      : Date.UTC(Number(match[3]), month, Number(match[1]));
  }

  _futureSlot(expiry = this.futureExpiry) {
    const contract = this.futureExpiries.find((item) =>
      this._normalizeExpiry(item.expiry) === this._normalizeExpiry(expiry)
    );
    return contract ? contract.which : 'NEAR';
  }

  async _refreshFutureExpiries(symbol) {
    const cached = this.futureExpiryCache.get(symbol);
    if (cached && Date.now() - cached.loadedAt < 300000) {
      this._applyFutureExpiries(symbol, cached.contracts);
      return;
    }
    if (this.futureExpiryLoads.has(symbol)) return this.futureExpiryLoads.get(symbol);

    const load = Promise.all(['NEAR', 'NEXT', 'FAR'].map(async (which) => {
      const query = new URLSearchParams({ symbol, which });
      const response = await fetch(`/api/commodities/quote?${query}`, { cache: 'no-store' });
      if (response.status === 404) return null;
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Futures expiry request failed (${response.status}).`);
      }
      const expiry = payload.quote && payload.quote.Expiry;
      return expiry ? { expiry, which } : null;
    })).then((results) => {
      const unique = new Map();
      results.filter(Boolean).forEach((contract) => {
        const key = this._normalizeExpiry(contract.expiry);
        if (!unique.has(key)) unique.set(key, contract);
      });
      const contracts = Array.from(unique.values())
        .sort((a, b) => this._expiryTime(a.expiry) - this._expiryTime(b.expiry));
      if (!contracts.length) throw new Error(`No live MCX futures expiries found for ${symbol}.`);
      this.futureExpiryCache.set(symbol, { loadedAt: Date.now(), contracts });
      this._applyFutureExpiries(symbol, contracts);
      return contracts;
    }).catch((error) => {
      if (symbol === this.symbol) {
        const picker = document.getElementById('commodity-futures-expiry');
        if (picker) {
          picker.replaceChildren(new Option('Futures unavailable', ''));
          picker.disabled = true;
          picker.title = error.message || 'Unable to load MCX futures expiries.';
        }
      }
      return [];
    }).finally(() => this.futureExpiryLoads.delete(symbol));
    this.futureExpiryLoads.set(symbol, load);
    return load;
  }

  _applyFutureExpiries(symbol, contracts) {
    if (window.dashboardInstrumentMode !== 'commodity' || symbol !== this.symbol) return;
    const picker = document.getElementById('commodity-futures-expiry');
    if (!picker) return;
    const previous = this.futureExpiry;
    this.futureExpiries = contracts;
    const selected = contracts.find((item) =>
      this._normalizeExpiry(item.expiry) === this._normalizeExpiry(previous)
    ) || contracts[0];
    this.futureExpiry = selected.expiry;
    picker.replaceChildren(...contracts.map((item) => {
      const option = new Option(this._formatFutureContractLabel(item), item.expiry);
      option.title = `${item.which} · ${this._formatExpiry(item.expiry)}`;
      option.selected = item === selected;
      return option;
    }));
    picker.disabled = false;
    if (this._normalizeExpiry(previous) !== this._normalizeExpiry(this.futureExpiry)) {
      this._refreshQuote(symbol);
    }
  }

  async refreshNews() {
    if (window.dashboardInstrumentMode !== 'commodity') return;
    if (this.newsLoading) {
      this.newsRefreshPending = true;
      return;
    }
    this.newsLoading = true;
    const symbol = this.symbol;
    const status = document.getElementById('commodity-news-status');
    if (status) status.textContent = `Loading recent ${symbol} headlines…`;
    try {
      const response = await fetch(
        `/api/commodities/news?symbol=${encodeURIComponent(symbol)}`
      );
      const payload = await response.json();
      if (window.dashboardInstrumentMode !== 'commodity' || symbol !== this.symbol) return;
      if (!response.ok) {
        throw new Error(payload.error || `News request failed (${response.status}).`);
      }
      this._renderNews(payload);
    } catch (error) {
      if (window.dashboardInstrumentMode !== 'commodity' || symbol !== this.symbol) return;
      const list = document.getElementById('commodity-news-list');
      if (list) list.replaceChildren();
      if (status) status.textContent = 'Headlines are unavailable right now. Try again later.';
    } finally {
      this.newsLoading = false;
      if (this.newsRefreshPending && window.dashboardInstrumentMode === 'commodity') {
        this.newsRefreshPending = false;
        this.refreshNews();
      } else if (window.dashboardInstrumentMode !== 'commodity') {
        this.newsRefreshPending = false;
      }
    }
  }

  async refreshInventory() {
    if (window.dashboardInstrumentMode !== 'commodity') return;
    const card = document.getElementById('commodity-inventory-outlook');
    if (!card) return;
    const symbol = this.symbol;
    if (!['CRUDEOIL', 'NATURALGAS'].includes(symbol)) {
      card.hidden = true;
      return;
    }
    if (this.inventoryLoading) {
      if (this.inventoryLoadingSymbol === symbol) return;
      this.inventoryRefreshPendingSymbol = symbol;
      return;
    }
    this.inventoryLoading = true;
    this.inventoryLoadingSymbol = symbol;
    card.hidden = false;
    const summary = document.getElementById('commodity-inventory-summary');
    const detail = document.getElementById('commodity-inventory-detail');
    if (summary && !summary.textContent.trim()) summary.textContent = 'Loading latest report…';
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(
        `/api/commodities/inventory?symbol=${encodeURIComponent(symbol)}`,
        { cache: 'no-store', signal: controller.signal }
      );
      const payload = await response.json();
      if (window.dashboardInstrumentMode !== 'commodity' || this.symbol !== symbol) return;
      const naturalGas = symbol === 'NATURALGAS';
      const fmt = (value) => value != null && value !== '' && Number.isFinite(Number(value))
        ? (naturalGas
          ? `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(1)} Bcf`
          : `${Number(value) > 0 ? '+' : ''}${(Number(value) / 1000).toFixed(1)}M`)
        : '—';
      const fullFmt = (value) => {
        const compact = fmt(value);
        return compact === '—' ? compact : naturalGas ? compact : `${compact} barrels`;
      };
      const outlook = payload.outlook || 'Inventory report unavailable';
      const hasReport = payload.actual != null;
      if (summary) {
        const surprise = payload.surprise == null ? null : Number(payload.surprise);
        const actual = Number(payload.actual);
        const volume = surprise == null ? actual : surprise;
        const absVolume = Math.abs(volume);
        const strength = absVolume >= (naturalGas ? 100 : 5000) ? 'Strong'
          : absVolume >= (naturalGas ? 40 : 2000) ? 'Mild' : '';
        const direction = surprise == null
          ? (volume < 0 ? 'Positive' : volume > 0 ? 'Negative' : 'Neutral')
          : surprise < 0 ? 'Positive' : surprise > 0 ? 'Negative' : 'Neutral';
        summary.textContent = hasReport
          ? `${strength ? `${strength} ` : ''}${direction}`
          : 'Report unavailable';
        summary.dataset.tone = direction === 'Positive' ? 'bullish'
          : direction === 'Negative' ? 'bearish' : 'neutral';
        summary.title = outlook;
      }
      if (detail) {
        const previousReport = payload.previous_date
          ? `P (${payload.previous_date}): ${fullFmt(payload.previous)}`
          : `P: ${fullFmt(payload.previous)}`;
        detail.replaceChildren();
        const lines = hasReport
          ? [`A ${fullFmt(payload.actual)} · F ${fullFmt(payload.expected)} · ${previousReport}`]
          : [];
        if (hasReport && payload.report_date) lines.push(`Report week: ${payload.report_date}`);
        const sourceLine = [
          payload.source ? (payload.source.includes('Energy Information Administration') ? 'EIA' : payload.source) : '',
          payload.expected_source ? `Forecast: ${payload.expected_source}` : '',
        ].filter(Boolean);
        if (sourceLine.length) lines.push(sourceLine.join(' · '));
        if (payload.stale) lines.push('Cached report');
        if (payload.availability_note) lines.push(payload.availability_note);
        else if (payload.error) lines.push(payload.error);
        detail.textContent = lines.join('\n');
        if (payload.source_url) {
          const link = document.createElement('a');
          link.href = payload.source_url;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = ' Open EIA report';
          detail.appendChild(link);
        }
      }
      if (!response.ok) throw new Error(payload.error || 'Inventory unavailable');
    } catch (error) {
      const message = error.name === 'AbortError'
        ? 'Inventory request timed out. Check backend internet access and try again.'
        : error.message || 'Inventory report unavailable.';
      if (summary) summary.textContent = 'Report unavailable';
      if (detail) detail.textContent = message;
    } finally {
      window.clearTimeout(timeout);
      this.inventoryLoading = false;
      this.inventoryLoadingSymbol = '';
      if (this.inventoryRefreshPendingSymbol) {
        this.inventoryRefreshPendingSymbol = '';
        this.refreshInventory();
      }
    }
  }

  _renderNews(payload) {
    const status = document.getElementById('commodity-news-status');
    const list = document.getElementById('commodity-news-list');
    const items = Array.isArray(payload.items) ? payload.items : [];
    const fragment = document.createDocumentFragment();
    items.forEach((item) => {
      const row = document.createElement('li');
      const link = document.createElement('a');
      link.href = item.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = item.title || 'Open news article';
      row.appendChild(link);

      const meta = document.createElement('small');
      const date = item.published_at
        ? new Date(item.published_at).toLocaleString('en-IN', {
          timeZone: 'Asia/Kolkata',
          day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
        })
        : '';
      meta.textContent = [item.source, date].filter(Boolean).join(' · ');
      row.appendChild(meta);
      fragment.appendChild(row);
    });
    if (list) list.replaceChildren(fragment);

    if (status) {
      if (!items.length) {
        status.textContent = `No matching headlines in the last 24 hours for ${payload.symbol || this.symbol}.`;
      } else {
        const updated = Number(payload.updated_at);
        const updatedText = Number.isFinite(updated)
          ? new Date(updated * 1000).toLocaleTimeString('en-IN', {
            timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit',
          })
          : '';
        status.textContent = `${items.length} recent stories · ${payload.source || 'News'}${payload.stale ? ' · cached' : ''}${updatedText ? ` · checked ${updatedText}` : ''}`;
      }
    }
  }

  async _refreshQuote(symbol) {
    if (window.dashboardInstrumentMode !== 'commodity' || symbol !== this.symbol) return;
    if (this.quoteLoadingSymbols.has(symbol)) {
      this.quoteRefreshPendingSymbols.add(symbol);
      return;
    }
    this.quoteLoadingSymbols.add(symbol);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    const requestedAt = Date.now();
    const requestedExpiry = this.futureExpiry;
    const which = this._futureSlot(requestedExpiry);
    const status = document.getElementById('commodity-price-status');
    try {
      const response = await fetch(
        `/api/commodities/quote?symbol=${encodeURIComponent(symbol)}&which=${which}`,
        { cache: 'no-store', signal: controller.signal }
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Quote request failed (${response.status}).`);
      }
      if (window.dashboardInstrumentMode !== 'commodity'
          || symbol !== this.symbol || requestedExpiry !== this.futureExpiry) return;
      const quote = payload.quote || {};
      if (!this._quoteMatchesSelectedSymbol(quote)) return;
      if (this.futureExpiry
          && this._normalizeExpiry(quote.Expiry) !== this._normalizeExpiry(this.futureExpiry)) return;
      if (!this._acceptQuoteTick(quote)) return;
      this.quoteCache.set(symbol, {
        quote: { ...quote },
        provider: payload.provider || '',
        receivedAt: Date.now(),
      });
      this._markSelectedCommodityFeedFresh(symbol);
      // The REST quote carries minute-bucketed price/OI/volume history even
      // when a fresher WebSocket tick owns the displayed live price. Render
      // this independent context before the live-tick guard can return.
      this._renderFiveMinuteContext(quote.FiveMinute);
      // REST getMarketData rows carry the true instrument name and are
      // identity-checked — record this LTP as the trusted reference the
      // SMARTAPI_WS tick stream is validated against in applyLiveQuote,
      // even when the fresh-WS early return below keeps it off latestQuote.
      (this._restLtpByKey = this._restLtpByKey || {})[this._quoteGuardKey()] = Number(quote.LTP);
      const liveQuoteIsFresh = this.lastLiveQuoteAt
        && Date.now() - this.lastLiveQuoteAt < 10000;
      const liveQuoteMatchesExpiry = !this.futureExpiry
        || this._normalizeExpiry(this.latestQuote && this.latestQuote.Expiry)
          === this._normalizeExpiry(this.futureExpiry);
      if (liveQuoteMatchesExpiry && (this.lastLiveQuoteAt > requestedAt || liveQuoteIsFresh)) return;
      this.latestQuote = quote;
      this.latestQuoteError = '';
      this.latestProvider = payload.provider || '';
      const topBar = document.getElementById('sec-topbar');
      if (!topBar || !topBar.classList.contains('commodity-topbar')) this.renderDashboard();
      const sourceSelect = document.getElementById('dataSourceSelect');
      if (sourceSelect && Array.from(sourceSelect.options).some(option => option.value === this.latestProvider)) {
        sourceSelect.value = this.latestProvider;
      }
      this._syncContractLabel(quote);
      this._setText('commodity-price-ltp', this._format(quote.LTP));
      const change = quote.Change == null ? NaN : Number(quote.Change);
      const percent = quote.PctChange == null ? NaN : Number(quote.PctChange);
      const changeElement = document.getElementById('commodity-price-change');
      const ltpElement = document.getElementById('commodity-price-ltp');
      if (ltpElement) {
        ltpElement.classList.toggle('up', Number.isFinite(percent) && percent >= 0);
        ltpElement.classList.toggle('down', Number.isFinite(percent) && percent < 0);
      }
      if (changeElement) {
        changeElement.textContent = Number.isFinite(change) && Number.isFinite(percent)
          ? `${change >= 0 ? '+' : ''}${this._format(change)} ${percent >= 0 ? '▲' : '▼'} ${this._format(Math.abs(percent))}%`
          : '—';
        changeElement.classList.toggle('up', percent >= 0);
        changeElement.classList.toggle('down', percent < 0);
      }
      this._setText(
        'commodity-price-lot-size',
        Number.isFinite(Number(quote.LotSize)) && Number(quote.LotSize) > 0
          ? Number(quote.LotSize).toLocaleString('en-IN')
          : 'Unavailable'
      );
      if (status) {
        status.dataset.tone = 'live';
        status.dataset.status = 'live';
        status.textContent = 'LIVE';
        status.title = `Live MCX quote via ${payload.provider || 'active provider'}`;
      }
      const sourceStatus = document.querySelector('.data-source-status-pill');
      if (sourceStatus) {
        sourceStatus.textContent = 'AVAILABLE';
        sourceStatus.dataset.status = 'available';
        sourceStatus.title = `MCX quote available via ${payload.provider || 'active provider'}`;
      }
      this._setText('commodity-updated-time', new Date().toLocaleTimeString('en-IN', {
        timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit',
      }));
      this._syncSharedQuoteContext();
    } catch (error) {
      if (window.dashboardInstrumentMode === 'commodity' && symbol === this.symbol && status) {
        this.latestQuoteError = error.message || 'MCX quote unavailable.';
        const hasLastGoodQuote = !!this.latestQuote;
        status.dataset.tone = hasLastGoodQuote ? 'stale' : 'error';
        status.dataset.status = hasLastGoodQuote ? 'stale' : 'unavailable';
        status.textContent = hasLastGoodQuote ? 'STALE' : 'UNAVAILABLE';
        status.title = this.latestQuoteError;
        const sourceStatus = document.querySelector('.data-source-status-pill');
        if (sourceStatus) {
          sourceStatus.textContent = hasLastGoodQuote ? 'STALE' : 'UNAVAILABLE';
          sourceStatus.dataset.status = hasLastGoodQuote ? 'stale' : 'api-unavailable';
          sourceStatus.title = this.latestQuoteError;
        }
        if (!hasLastGoodQuote) {
          this._setText('commodity-price-ltp', '—');
          const ltp = document.getElementById('commodity-price-ltp');
          if (ltp) ltp.classList.remove('up', 'down');
          this._setText('commodity-price-lot-size', '—');
          this._renderFiveMinuteContext(null);
          const change = document.getElementById('commodity-price-change');
          if (change) {
            change.textContent = '—';
            change.classList.remove('up', 'down');
          }
        }
      }
    }
    finally {
      window.clearTimeout(timeout);
      this.quoteLoadingSymbols.delete(symbol);
      if (this.quoteRefreshPendingSymbols.delete(symbol)
          && window.dashboardInstrumentMode === 'commodity'
          && symbol === this.symbol) {
        this._refreshQuote(symbol);
      }
    }
  }

  async _refreshOptionChain(symbol) {
    if (window.dashboardInstrumentMode !== 'commodity' || symbol !== this.symbol) return;
    if ((this.optionsUnavailableUntil.get(symbol) || 0) > Date.now()) return;
    const requestedExpiry = this.expiry;
    const loadKey = `${symbol}:${requestedExpiry || 'NEAR'}`;
    const existing = this.chainLoads.get(loadKey);
    if (existing) return existing;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15000);
    this.chainLoading = true;
    const status = document.getElementById('commodity-chain-status');
    if (status && !document.querySelector('#commodity-chain-shared .oc-ledger-row')) {
      status.dataset.tone = '';
      status.textContent = 'Loading option chain…';
    }
    const query = new URLSearchParams({ symbol });
    if (requestedExpiry) query.set('expiry', requestedExpiry);
    const load = (async () => { try {
      const response = await fetch(`/api/commodities/option-chain?${query}`, {
        cache: 'no-store', signal: controller.signal,
      });
      const payload = await response.json();
      if (!response.ok) {
        if (response.status === 404 && /no MCX option expiries/i.test(payload.error || '')) {
          if (window.dashboardInstrumentMode === 'commodity' && symbol === this.symbol
              && requestedExpiry === this.expiry) {
            this._showOptionsUnavailable(symbol);
          }
          return;
        }
        throw new Error(payload.error || `Option-chain request failed (${response.status}).`);
      }
      if (window.dashboardInstrumentMode !== 'commodity'
          || symbol !== this.symbol
          || (requestedExpiry && requestedExpiry !== this.expiry)) return;
      if (String(payload.symbol || '').trim().toUpperCase() !== symbol) return;
      this.optionsUnavailableUntil.delete(symbol);
      this.latestChainPayload = payload;
      this.latestAnalysis = payload.analysis || {};
      if (!(payload.chain?.rows || []).length) {
        throw new Error('No option quotes are available right now. Retrying…');
      }
      this.chainCache.set(symbol, { payload, receivedAt: Date.now() });
      if (!requestedExpiry) this.expiry = payload.expiry;
      const expiryPicker = document.getElementById('commodity-expiry');
      if (expiryPicker) {
        expiryPicker.replaceChildren(...(payload.expiries || []).map((expiry) => {
          const option = document.createElement('option');
          option.value = expiry;
          option.textContent = this._formatExpiry(expiry);
          option.selected = expiry === this.expiry;
          return option;
        }));
      }
      this._renderSharedOptionChain(payload);
      this._renderAnalysis(payload.analysis || {});
      this._renderCommodityAnalytics();
      this._syncSharedQuoteContext();
      if (status) {
        status.dataset.tone = 'live';
        status.textContent = payload.chain.rows && payload.chain.rows.length
          ? `${payload.provider} · ATM ${this._format(payload.chain.atm_strike, 2)}`
          : 'No option quotes available for this expiry.';
      }
    } catch (error) {
      if (window.dashboardInstrumentMode === 'commodity'
          && symbol === this.symbol && requestedExpiry === this.expiry && status) {
        status.dataset.tone = 'error';
        status.textContent = error.message || 'Unable to load commodity option chain.';
        this._clearOptionChain();
        if (this.chainRetryTimer !== null) window.clearTimeout(this.chainRetryTimer);
        this.chainRetryTimer = window.setTimeout(() => {
          this.chainRetryTimer = null;
          if (window.dashboardInstrumentMode === 'commodity' && symbol === this.symbol
              && requestedExpiry === this.expiry) this._refreshOptionChain(symbol);
        }, 2500);
      }
    }
    finally {
      window.clearTimeout(timeout);
      this.chainLoads.delete(loadKey);
      this.chainLoading = this.chainLoads.size > 0;
    } })();
    this.chainLoads.set(loadKey, load);
    return load;
  }

  _formatExpiry(expiry) {
    const match = /^(\d{2})([A-Z]{3})(\d{4})$/.exec(String(expiry || ''));
    return match ? `${match[1]}-${match[2][0]}${match[2].slice(1).toLowerCase()}-${match[3]}` : expiry;
  }

  // The futures picker now renders where the contract name used to sit
  // under the price. Option labels are composed contract-style strings;
  // once a live quote arrives, overwrite the selected option's label with
  // the broker's exact Contract symbol (e.g. NATURALGAS24NOV26FUT).
  _syncContractLabel(quote) {
    const picker = document.getElementById('commodity-futures-expiry');
    if (!picker || !quote || !quote.Contract) return;
    const selected = Array.from(picker.options).find((option) =>
      this._normalizeExpiry(option.value) === this._normalizeExpiry(quote.Expiry));
    if (selected) selected.textContent = quote.Contract;
  }

  // Contract-style label for the futures picker (e.g. NATURALGAS24NOV26FUT)
  // — the picker now sits where the contract name used to render under the
  // price, so its options read like broker contract symbols.
  _formatFutureContractLabel(item) {
    const match = /^(\d{2})([A-Z]{3})(\d{4})$/.exec(this._normalizeExpiry(item.expiry));
    if (!match) return this._formatExpiry(item.expiry);
    return `${this.symbol}${match[1]}${match[2]}${match[3].slice(2)}FUT`;
  }

  _renderFiveMinuteContext(context) {
    const pending = context && context.window_minutes
      ? `Waiting for ${Math.max(1, 5 - context.window_minutes)}m more`
      : 'Collecting 5m baseline';
    if (!context || !context.available) {
      this._setText('commodity-price-change-5m', pending);
      this._setText('commodity-price-oi-change-5m', pending);
      this._setText('commodity-price-volume-change-5m', pending);
      return;
    }
    const signed = (value, digits = 0) => {
      const number = Number(value);
      return Number.isFinite(number)
        ? `${number > 0 ? '+' : ''}${this._format(number, digits)}`
        : 'Unavailable';
    };
    const priceChange = Number(context.price_change);
    const pricePct = Number(context.price_change_pct);
    this._setText(
      'commodity-price-change-5m',
      Number.isFinite(priceChange) && Number.isFinite(pricePct)
        ? `${signed(priceChange, 2)} (${signed(pricePct, 2)}%)`
        : 'Unavailable'
    );
    this._setText('commodity-price-oi-change-5m', signed(context.oi_change));
    this._setText(
      'commodity-price-volume-change-5m',
      context.volume_change == null ? 'Unavailable' : `+${fmtCrLK(context.volume_change)}`
    );
  }

  _renderSharedOptionChain(payload = this.latestChainPayload) {
    if (window.dashboardInstrumentMode !== 'commodity') return;
    const host = document.getElementById('commodity-chain-shared');
    const chainView = typeof app !== 'undefined' && app.chain;
    if (!host || !chainView || typeof chainView.buildChainSummaryHtml !== 'function') return;
    const data = this._toSharedDashboardData();
    if (typeof _data !== 'undefined') _data = data;
    const template = document.createElement('template');
    template.innerHTML = chainView.buildChainSummaryHtml(data).trim();
    const freshTable = template.content.querySelector('#option-chain-table');
    const modal = document.getElementById('option-chain-modal');
    const modalBody = document.getElementById('option-chain-modal-body');
    const modalTable = modalBody && modalBody.querySelector('#option-chain-table');
    if (modal && modal.classList.contains('open') && modalTable && freshTable) {
      freshTable.hidden = false;
      modalTable.replaceWith(freshTable.cloneNode(true));
      freshTable.remove();
    }
    host.replaceChildren(template.content);

    const flowHost = document.getElementById('commodity-capital-flow');
    if (flowHost) {
      const buildFlowHtml = () => buildOiFlowSummaryHtml(
        data.chain, data.atm, {}, data.oiVelocity, data,
      );
      const currentFlowCard = flowHost.querySelector('#oi-flow-summary-card');
      if (currentFlowCard) {
        patchOuterHtmlIfChanged('oi-flow-summary-card', buildFlowHtml);
      } else {
        const flowHtml = buildFlowHtml();
        flowHost.innerHTML = flowHtml;
        const flowCard = flowHost.querySelector('#oi-flow-summary-card');
        if (flowCard) flowCard.dataset.lastHtml = flowHtml;
      }
    }
  }

  _renderAnalysis(analysis) {
    this.latestAnalysis = analysis || {};
    this._renderCommodityAnalytics();
    const decision = document.getElementById('sec-decision');
    const chainView = typeof app !== 'undefined' && app.chain;
    if (decision && chainView && typeof chainView.refreshDecisionBoxGuarded === 'function') {
      chainView.refreshDecisionBoxGuarded(this._toSharedDashboardData());
    } else {
      this.renderDashboard();
    }
  }

  _renderCommodityAnalytics() {
    const set = (id, value) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value == null || value === '' ? '—' : String(value);
    };
    const blocks = this.latestChainPayload?.chain?.oi_velocity || [];
    const sharedData = this._toSharedDashboardData();
    const visibleChain = typeof getFilteredChain === 'function'
      ? getFilteredChain(sharedData)
      : sharedData.chain || [];
    const visibleStrikes = new Set(visibleChain.map((row) => Number(row.strike)));
    [5, 15, 30].forEach((windowMinutes) => {
      const block = blocks.find((item) => Number(item.window) === windowMinutes);
      const rows = (block?.rows || []).filter((row) =>
        visibleStrikes.has(Number(row.strike))
      );
      const ce = rows.reduce((sum, row) => sum + (Number(row.ceDOI) || 0), 0);
      const pe = rows.reduce((sum, row) => sum + (Number(row.peDOI) || 0), 0);
      const format = (value) => `${value > 0 ? '+' : ''}${Math.round(value).toLocaleString('en-IN')}`;
      const valueElement = document.getElementById(`commodity-oi-velocity-${windowMinutes}`);
      if (valueElement) {
        valueElement.textContent = rows.length
          ? `${format(ce)} CE · ${format(pe)} PE`
          : 'Collecting history';
        const periodLabel = valueElement.parentElement?.querySelector('span');
        if (periodLabel) {
          periodLabel.textContent = rows.length
            ? `${windowMinutes} minute · Net PE−CE ${format(pe - ce)}`
            : `${windowMinutes} minute`;
        }
      }
    });
    set('commodity-analytics-reason', this.latestChainPayload?.chain?.oi_velocity?.some((item) => item.rows?.length)
      ? `Selected strike range · same PE−CE calculation as Market OI Flow · ${this._formatExpiry(this.expiry)}.`
      : 'Collecting option-chain snapshots; horizon totals appear after enough history builds.');
  }

  _toSharedDashboardData() {
    const optionsUnavailable = !!this.latestAnalysis?.optionsUnavailable;
    const quoted = this.latestQuote || {};
    const quote = this._quoteMatchesSelectedSymbol(quoted) ? quoted : {};
    const chainPayload = this.latestChainPayload || {};
    const payloadSymbol = String(
      chainPayload.symbol
      || chainPayload.chain?.symbol
      || chainPayload.chain?.underlying
      || ''
    ).trim().toUpperCase();
    const payload = payloadSymbol && payloadSymbol === this.symbol
      ? chainPayload
      : {};
    const rawChain = payload.chain || {};
    const analysis = this.latestAnalysis || {};
    const rowsByStrike = new Map();
    // Stateless sanity bound: an option premium can never exceed the
    // underlying (CE) or its own strike (PE). Angel's batch feed can key
    // a foreign FUTURES row (~143k) under an option token — that can
    // never be a real premium, so reject it even with no baseline.
    const spotBound = Number(this.latestQuote && this.latestQuote.LTP)
      || Number((payload.chain || {}).spot) || 0;
    (rawChain.rows || []).forEach((contract) => {
      const row = rowsByStrike.get(Number(contract.strike)) || {
        strike: Number(contract.strike), ceOI: 0, peOI: 0,
        ceLTP: null, peLTP: null, ceVol: 0, peVol: 0,
        ceIV: null, peIV: null, ceChgOI: null, peChgOI: null,
        ceChg: null, peChg: null, ceCapitalFlow: null, peCapitalFlow: null,
        cePremiumLocked: null, pePremiumLocked: null,
      };
      const side = contract.type === 'CE' ? 'ce' : 'pe';
      row[`${side}OI`] = Number(contract.oi) || 0;
      const rawLtp = contract.ltp == null ? null : Number(contract.ltp);
      const premiumBound = side === 'ce'
        ? spotBound * 1.2
        : Number(contract.strike) * 1.2;
      const ltp = Number.isFinite(rawLtp) && rawLtp > 0
          && Number.isFinite(premiumBound) && premiumBound > 0
          && rawLtp > premiumBound
        ? null
        : rawLtp;
      row[`${side}LTP`] = ltp;
      const netChange = contract.net_change == null ? null : Number(contract.net_change);
      const percentChange = contract.pct_change == null ? null : Number(contract.pct_change);
      row[`${side}Chg`] = Number.isFinite(netChange) ? netChange : null;
      row[`${side}ChgPct`] = Number.isFinite(percentChange) ? percentChange : null;
      row[`${side}Vol`] = Number(contract.volume) || 0;
      row[`${side}IV`] = contract.iv == null ? null : Number(contract.iv);
      const oiChange = contract.oi_change == null ? null : Number(contract.oi_change);
      row[`${side}ChgOI`] = Number.isFinite(oiChange) ? oiChange : null;
      row[`${side}CapitalFlow`] = Number.isFinite(oiChange) && Number.isFinite(ltp)
        ? oiChange * ltp
        : null;
      row[`${side}PremiumLocked`] = Number.isFinite(Number(contract.oi))
        && Number.isFinite(ltp)
        ? Number(contract.oi) * ltp
        : null;
      rowsByStrike.set(row.strike, row);
    });
    const chain = Array.from(rowsByStrike.values()).sort((a, b) => a.strike - b.strike);
    const hasCeOiChangeData = chain.some(row => row.ceChgOI != null);
    const hasPeOiChangeData = chain.some(row => row.peChgOI != null);
    const totalCeOiChange = chain.reduce(
      (sum, row) => sum + (Number(row.ceChgOI) || 0), 0
    );
    const totalPeOiChange = chain.reduce(
      (sum, row) => sum + (Number(row.peChgOI) || 0), 0
    );
    const oiChgPCR = hasCeOiChangeData && hasPeOiChangeData
        && Number.isFinite(totalCeOiChange) && Number.isFinite(totalPeOiChange)
        && totalCeOiChange > 0
      ? totalPeOiChange / totalCeOiChange
      : undefined;
    const steps = chain.slice(1).map((row, index) => row.strike - chain[index].strike)
      .filter((step) => Number.isFinite(step) && step > 0);
    const bias = optionsUnavailable
      ? 'UNAVAILABLE'
      : ['BULLISH', 'BEARISH', 'NEUTRAL', 'WAIT'].includes(analysis.verdict)
        ? analysis.verdict : 'WAIT';
    const short = analysis.short_horizon || null;
    const contributors = (analysis.contributors || []).map((item) => ({
      ...item,
      weightedContribution: item.weightedContribution,
    }));
    const missingInputs = (analysis.missing_inputs || [])
      .filter((input) => input !== 'engine_bias');
    const pcr = analysis.put_call_oi_ratio;
    const expiry = payload.expiry || quote.Expiry || '';
    const atmIV = rawChain.atm_iv == null || rawChain.atm_iv === ''
      ? null
      : Number(rawChain.atm_iv);
    const expiryParts = /^(\d{2})([A-Z]{3})(\d{4})$/.exec(String(expiry).toUpperCase());
    const monthIndex = expiryParts
      ? ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'].indexOf(expiryParts[2])
      : -1;
    const today = new Date();
    const expiryUtc = monthIndex >= 0
      ? Date.UTC(Number(expiryParts[3]), monthIndex, Number(expiryParts[1]))
      : NaN;
    const todayUtc = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate());
    const dte = Number.isFinite(expiryUtc)
      ? Math.max(0, Math.ceil((expiryUtc - todayUtc) / 86400000)) : 0;
    const data = {
      symbol: this.symbol,
      symbolName: this.symbol,
      instrument: 'MCX_FUT',
      futureExpiry: this.futureExpiry,
      futureContract: quote.Contract || '',
      capitalFlowBaseline: rawChain.flow_baseline || '',
      capitalFlowBaselineStatus: rawChain.flow_baseline_status || 'pending',
      spot: Number(quote.LTP) || Number(rawChain.spot) || 0,
      spotChange: Number(quote.Change) || 0,
      spotChgPct: Number(quote.PctChange) || 0,
      // Exact futures-session OHLC + volume/OI — lets the mini chart show
      // the same values the stats card does instead of deriving approximations.
      dayOpen: quote.Open ?? null,
      dayHigh: quote.High ?? null,
      dayLow: quote.Low ?? null,
      quoteVolume: quote.Volume ?? null,
      quoteOI: quote.OI ?? null,
      quoteAvailable: !!this.latestQuote,
      quoteError: this.latestQuoteError,
      quoteProvider: this.latestProvider,
      dataSources: Array.isArray(window.AppState?.wsState?.dataSources)
        ? window.AppState.wsState.dataSources : [],
      dataSource: window.AppState?.wsState?.dataSource || this.latestProvider || '',
      expiry,
      dte,
      atm: Number(rawChain.atm_strike) || 0,
      strikeStep: Number(rawChain.strike_step) || Math.min(...steps, 200),
      chain,
      totalPCR: undefined,
      oiChgPCR,
      greeks: Array.isArray(rawChain.greeks) ? rawChain.greeks : [],
      atmIV: Number.isFinite(atmIV) ? atmIV : undefined,
      oiVelocity: Array.isArray(rawChain.oi_velocity) ? rawChain.oi_velocity : [],
      compositeBias: bias,
      optionsUnavailable,
      maxPain: analysis.visible_window_max_pain,
      ceWall: analysis.call_oi_wall,
      peWall: analysis.put_oi_wall,
      risk: {
        tradeGrade: 'Not rated',
        ivRegime: Number.isFinite(atmIV) ? `${atmIV.toFixed(2)}%` : 'Unavailable',
        trapWarn: 'none',
      },
      decision: {
        bias,
        unavailableReason: optionsUnavailable
          ? `Options are not listed for ${this.symbol}.`
          : '',
        biasStrength: analysis.bias_strength || '',
        confidence: Number(analysis.confidence) || 0,
        evidenceCoverage: Number(analysis.evidence_coverage) || 0,
        contributors,
        missingInputs,
        degraded: missingInputs.length > 0,
        conflictFlag: analysis.verdict === 'NEUTRAL',
        action: '',
        actionNote: 'Informational view · commodity order execution is not enabled here',
        tradeability: 'NO_TRADE',
        tradeabilityReason: analysis.reason || 'Waiting for commodity option-chain evidence.',
        strategyCaution: 'MCX analysis is informational; verify contract liquidity and broker execution.',
        verdicts: {
          pcr: pcr == null ? '—' : `${pcr} — Put / call open interest`,
          vix: 'Unavailable — not applicable to MCX contracts',
          atmIV: Number.isFinite(atmIV) ? `${atmIV.toFixed(2)}%` : undefined,
        },
        activeSignals: [],
        shortHorizon: short ? {
          horizon: '2–3m',
          direction: short.direction || 'NEUTRAL',
          state: short.state || 'NEUTRAL',
          probability: short.probability,
          probabilityCalibrated: false,
          score: short.score,
          price: {}, oi: {}, futures: {}, compression: {},
        } : null,
      },
    };
    if (typeof computeRangeChainTotals === 'function') {
      data.totalPCR = computeRangeChainTotals(getFilteredChain(data)).pcr;
    }
    return data;
  }

  renderDashboard() {
    if (window.dashboardInstrumentMode !== 'commodity') return;
    const dashboard = document.getElementById('dashboard');
    const slot = document.getElementById('commodity-dashboard-slot');
    const chainView = typeof app !== 'undefined' && app.chain;
    if (!dashboard || !slot || !chainView) return;
    const legacyLoader = document.getElementById('loader');
    if (legacyLoader) legacyLoader.style.display = 'none';
    const data = this._toSharedDashboardData();
    if (typeof _data !== 'undefined') _data = data;
    const existingTopBar = dashboard.querySelector('#sec-topbar');
    const hasCommodityTopBar = existingTopBar?.classList.contains('commodity-topbar');
    const existingDecision = dashboard.querySelector('#sec-decision');
    const expiryPicker = document.getElementById('commodity-expiry');
    const expiryHolder = document.getElementById('commodity-expiry-holder');
    if (expiryPicker && expiryHolder && expiryPicker.parentNode !== expiryHolder) {
      expiryHolder.appendChild(expiryPicker);
    }
    const futureExpiryPicker = document.getElementById('commodity-futures-expiry');
    const futureExpiryHolder = document.getElementById('commodity-futures-expiry-holder');
    if (futureExpiryPicker && futureExpiryHolder
        && futureExpiryPicker.parentNode !== futureExpiryHolder) {
      futureExpiryHolder.appendChild(futureExpiryPicker);
    }
    dashboard.style.display = 'block';
    // Quote polling runs independently from option-chain polling. Keep the
    // live quote DOM while rebuilding the decision view so a chain update
    // cannot reset the displayed price to the template's placeholder.
    if (hasCommodityTopBar) {
      if (existingDecision) {
        existingDecision.outerHTML = chainView.renderDecisionBoxHtml(data);
      } else {
        dashboard.insertAdjacentHTML('beforeend', chainView.renderDecisionBoxHtml(data));
      }
      // The top-bar (and its broker picker) stays mounted across polls, so
      // push live provider statuses into it when the WS snapshot's
      // dataSources actually change — otherwise the picker keeps whatever
      // options the first render produced.
      if (data.dataSources !== this._lastDataSources
          && typeof chainView.patchDataSourcePicker === 'function') {
        this._lastDataSources = data.dataSources;
        chainView.patchDataSourcePicker(data);
      }
    } else {
      dashboard.innerHTML = `${chainView.renderTopBarHtml(data, data.compositeBias === 'BEARISH')}${chainView.renderDecisionBoxHtml(data)}`;
      this._lastDataSources = data.dataSources;
    }
    slot.hidden = false;
    dashboard.append(slot);
    // Pin the quote/stats card directly under the top bar (above the
    // decision panel) — it ships inside the lower dashboard slot but is
    // more useful at-a-glance up top. It's a #dashboard sibling, so the
    // per-tick outerHTML swaps of #sec-topbar/#sec-decision don't touch it.
    const priceCard = dashboard.querySelector('.commodity-price-card:not(#equity-momentum-stats)');
    const decisionAnchor = dashboard.querySelector('#sec-decision');
    if (priceCard && decisionAnchor && priceCard.nextElementSibling !== decisionAnchor) {
      decisionAnchor.before(priceCard);
    }
    const expirySlot = document.getElementById('commodity-expiry-slot');
    if (expiryPicker && expirySlot) expirySlot.appendChild(expiryPicker);
    const futuresExpirySlot = document.getElementById('commodity-futures-expiry-slot');
    if (futureExpiryPicker && futuresExpirySlot) futuresExpirySlot.appendChild(futureExpiryPicker);
    this._renderSharedOptionChain(this.latestChainPayload);
    chainView._bindDecisionDetailGuard();
    const ui = typeof app !== 'undefined' && app.ui;
    if (ui && typeof ui.switchDashboardDestination === 'function') {
      ui.switchDashboardDestination(document.body.dataset.dashboardDestination || 'home');
    }
  }

  _renderShortHorizon(shortHorizon) {
    const horizon = shortHorizon && typeof shortHorizon === 'object' ? shortHorizon : {};
    const direction = String(horizon.direction || 'NEUTRAL').toUpperCase();
    const insufficientHistory = !Array.isArray(horizon.evidence)
      || horizon.evidence.includes('Insufficient 1-minute history');
    const rawState = String(horizon.state || 'NEUTRAL').replaceAll('_', ' ');
    const state = insufficientHistory
      ? 'Insufficient history'
      : direction === 'NEUTRAL' && rawState === 'NEUTRAL'
        ? 'No strong pressure'
        : direction !== 'NEUTRAL' && rawState === 'NEUTRAL'
          ? 'Early directional lean'
        : rawState;
    const directionElement = document.getElementById('commodity-short-direction');
    if (directionElement) {
      directionElement.textContent = insufficientHistory ? 'WAIT' : direction;
      directionElement.dataset.direction = direction;
    }
    this._setText('commodity-short-state', state);
    const probability = Number(horizon.probability);
    this._setText(
      'commodity-short-probability',
      Number.isFinite(probability) && !insufficientHistory
        ? `${probability}%`
        : '—%'
    );
    const evidenceItems = Array.isArray(horizon.evidence) ? horizon.evidence : [];
    const hasUpEvidence = evidenceItems.some((item) => /upside|upward/i.test(item));
    const hasDownEvidence = evidenceItems.some((item) => /downside|downward/i.test(item));
    const evidence = insufficientHistory
      ? 'Collecting minute-by-minute futures history.'
      : direction === 'NEUTRAL' && hasUpEvidence && hasDownEvidence
        ? 'Mixed short-period signals; no clear directional edge.'
        : evidenceItems.join(' · ') || 'No strong short-period pressure.';
    this._setText('commodity-short-evidence', evidence);
  }

}

window.commodityPrices = new CommodityPricesView();
