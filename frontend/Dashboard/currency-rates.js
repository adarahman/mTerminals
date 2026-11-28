class CurrencyRatesView {
  constructor() {
    this.symbol = 'USDINR';
    this.timer = null;
    this.chainTimer = null;
    this.loading = false;
    this.chainLoading = false;
    this.expiry = '';
    this.futuresExpiry = 'NEAR';
    this.latestQuote = null;
    this.latestChain = null;
    this.quoteError = '';
    this.chainError = '';
  }

  open(symbol = this.symbol) {
    this.symbol = String(symbol || 'USDINR').toUpperCase();
    if (this.timer !== null) window.clearInterval(this.timer);
    if (this.chainTimer !== null) window.clearInterval(this.chainTimer);
    this.refresh();
    this.refreshChain();
    this.timer = window.setInterval(() => this.refresh(), 2000);
    this.chainTimer = window.setInterval(() => this.refreshChain(), 10000);
  }

  close() {
    if (this.timer !== null) window.clearInterval(this.timer);
    if (this.chainTimer !== null) window.clearInterval(this.chainTimer);
    this.timer = null;
    this.chainTimer = null;
  }

  select(symbol) {
    this.symbol = String(symbol || 'USDINR').toUpperCase();
    this.expiry = '';
    this.latestQuote = null;
    this.latestChain = null;
    this.quoteError = '';
    this.chainError = '';
    try { localStorage.setItem('dashboard.currencySymbol', this.symbol); } catch (_) {}
    this.refresh();
    this.refreshChain();
    this.refreshDashboard();
  }

  selectExpiry(expiry) {
    this.expiry = String(expiry || '');
    this.refreshChain();
  }

  selectFutureExpiry(which) {
    const next = String(which || 'NEAR').toUpperCase();
    if (!['NEAR', 'NEXT', 'FAR'].includes(next) || next === this.futuresExpiry) return;
    this.futuresExpiry = next;
    this.latestQuote = null;
    this.latestChain = null;
    this.quoteError = '';
    this.chainError = '';
    this.refresh();
    this.refreshChain();
    this.refreshDashboard();
  }

  projectPayload(base = {}) {
    const quote = this.latestQuote || {};
    const chainData = this.latestChain?.chain || {};
    const rows = Array.isArray(chainData.rows) ? chainData.rows : [];
    // CDS option strikes and the displayed currency price are referenced to
    // the selected futures contract; there is no separate spot quote here.
    const spot = Number(quote.LTP ?? chainData.spot);
    const finite = value => value === null || value === undefined || value === ''
      ? null
      : Number.isFinite(Number(value)) ? Number(value) : null;
    const atm = rows.length && Number.isFinite(spot)
      ? rows.reduce((best, row) => Math.abs(Number(row.strike) - spot) < Math.abs(Number(best.strike) - spot) ? row : best, rows[0])
      : null;
    const pct = finite(quote.PctChange);
    const projectedChain = rows.map(row => {
      const call = row.call || {};
      const put = row.put || {};
      return {
        strike: Number(row.strike),
        atm: Boolean(atm && Number(row.strike) === Number(atm.strike)),
        ceLTP: finite(call.ltp), peLTP: finite(put.ltp),
        ceOI: finite(call.oi), peOI: finite(put.oi),
        ceVol: finite(call.volume), peVol: finite(put.volume),
        ceChgOI: null, peChgOI: null,
        ceChg: finite(call.change), peChg: finite(put.change),
        ceChgPct: finite(call.pct_change), peChgPct: finite(put.pct_change),
        ceIV: null, peIV: null, ceSignal: '', peSignal: '',
      };
    });
    const callPremium = finite(atm?.call?.ltp);
    const putPremium = finite(atm?.put?.ltp);
    const expiry = chainData.expiry || this.expiry || '';

    // Start from the regular snapshot for fields the shared renderer expects,
    // then explicitly remove Equity-only evidence and calculations.
    return {
      // Whitelist shared presentation metadata. Carrying the whole websocket
      // snapshot here would also carry its NIFTY-only signals, participant
      // flows, Greeks, and cached price-chart context into the currency view.
      dataSources: Array.isArray(base.dataSources) ? base.dataSources : [],
      dataSource: base.dataSource || '',
      refreshTime: base.refreshTime || '',
      symbol: this.symbol,
      symbolName: this.symbol,
      instrument: 'CDS_FUT',
      exchange: 'CDS',
      spot: Number.isFinite(spot) ? spot : null,
      spotChange: finite(quote.Change),
      spotChgPct: pct,
      future: finite(quote.LTP),
      futureChange: finite(quote.Change),
      futureChgPct: pct,
      futuresExpiry: this.futuresExpiry,
      basis: 0,
      lotSize: finite(quote.LotSize),
      expiry,
      expiryDates: chainData.expiries || (expiry ? [expiry] : []),
      dte: null,
      chain: projectedChain,
      chains: {},
      atm: atm ? Number(atm.strike) : null,
      callPremium,
      putPremium,
      totalPCR: null,
      maxPain: null,
      ceWall: null,
      peWall: null,
      greeks: [],
      atmIV: null,
      atmDelta: null,
      atmGamma: null,
      atmTheta: null,
      atmVega: null,
      oiVelocity: [],
      signals: [],
      contributors: [],
      futSignal: '',
      marketRegime: { regime: 'Indeterminate', description: 'Currency regime model unavailable.' },
      smartMoneySummary: { bias: 'Neutral', summary: '', capitalConfirms: null },
      risk: {},
      futuresPositioning: {},
      allIndices: [],
      indexQuotes: {},
      indiaVix: null,
      indiaVixChgPct: null,
      decision: {
        bias: 'NEUTRAL', biasStrength: '', confidence: 0,
        action: 'WAIT', actionNote: 'Currency instrument data only; no CDS signal model is available.',
        tradeability: 'NO_TRADE', tradeabilityReason: 'Currency signal model unavailable',
        activeSignals: [], verdicts: {}, contributors: [], evidenceCoverage: 0,
        // Keep the shared short-horizon panel present in currency mode. The
        // CDS feed currently supplies live quotes but no minute-history
        // signal inputs, so report a neutral reading until those exist.
        shortHorizon: {
          horizon: '2-3m', state: 'NEUTRAL', direction: 'NEUTRAL',
          probability: 0, probabilityCalibrated: false, score: 0,
          price: { move1mPct: 0, move2mPct: 0, move3mPct: 0,
            velocityPctPerMin: 0, accelerationPctPerMin2: 0 },
          oi: { direction: 'NEUTRAL', confirmed: false, score: 0 },
          futures: { direction: 'NEUTRAL', confirmed: false, score: 0,
            ltp: 0, vwap: 0, priceVsVwapPct: 0 },
          compression: { active: false, expanding: false,
            range3mPct: 0, previousRange3mPct: 0 },
          evidence: ['Currency minute-history signal unavailable'],
        },
      },
      compositeBias: 'NEUTRAL',
      optionsUnavailable: !rows.length,
      quoteAvailable: Boolean(quote.LTP),
      quoteError: this.quoteError || this.chainError,
      _currencyPayload: true,
    };
  }

  refreshDashboard() {
    if (window.dashboardInstrumentMode !== 'currency' || typeof app === 'undefined' || !app.chain) return;
    const current = window.AppState?.wsState;
    if (current) app.chain.renderDashboard(current);
  }

  async refresh() {
    if (this.loading || !window.Config?.api?.currencyQuote) return;
    this.loading = true;
    const requestSymbol = this.symbol;
    const requestExpiry = this.futuresExpiry;
    try {
      const query = new URLSearchParams({ symbol: requestSymbol, which: requestExpiry });
      const response = await fetch(`${Config.api.currencyQuote}?${query}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Quote unavailable (${response.status})`);
      if (requestSymbol !== this.symbol || requestExpiry !== this.futuresExpiry) return;
      this.latestQuote = payload.quote || null;
      this.quoteError = '';
      const ltp = Number(this.latestQuote?.LTP);
      const chartContext = {
        symbol: this.symbol,
        instrument: 'CDS_FUT',
        expiry: this.futuresExpiry,
      };
      if (window.priceChart && Number.isFinite(ltp) && ltp > 0) {
        window.priceChart.addTick(ltp, Date.now(), null, chartContext);
      }
      const priceChartModal = document.getElementById('price-chart-modal');
      const modalManager = typeof app !== 'undefined' && app.modal;
      if (priceChartModal?.classList.contains('open') && modalManager) {
        modalManager.updatePriceChartLiveContext(
          this.projectPayload(window.AppState?.wsState || {})
        );
      }
      this.refreshDashboard();
    } catch (error) {
      console.warn('[currency] quote refresh failed:', error?.message || error);
      if (requestSymbol === this.symbol && requestExpiry === this.futuresExpiry) {
        this.quoteError = error?.message || 'Currency quote unavailable';
        this.refreshDashboard();
      }
    } finally { this.loading = false; }
  }

  async refreshChain() {
    if (this.chainLoading || !window.Config?.api?.currencyOptionChain) return;
    this.chainLoading = true;
    const requestSymbol = this.symbol;
    const requestExpiry = this.expiry;
    const requestFuturesExpiry = this.futuresExpiry;
    try {
      const query = new URLSearchParams({ symbol: requestSymbol, which: requestFuturesExpiry });
      if (requestExpiry) query.set('expiry', requestExpiry);
      const response = await fetch(`${Config.api.currencyOptionChain}?${query}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Option chain unavailable (${response.status})`);
      if (requestSymbol !== this.symbol || requestExpiry !== this.expiry
          || requestFuturesExpiry !== this.futuresExpiry) return;
      this.latestChain = payload;
      this.expiry = payload.chain?.expiry || this.expiry;
      this.chainError = '';
      this.refreshDashboard();
    } catch (error) {
      console.warn('[currency] option-chain refresh failed:', error?.message || error);
      if (requestSymbol === this.symbol && requestExpiry === this.expiry
          && requestFuturesExpiry === this.futuresExpiry) {
        this.chainError = error?.message || 'Currency option chain unavailable';
        this.refreshDashboard();
      }
    } finally { this.chainLoading = false; }
  }
}

window.currencyRates = new CurrencyRatesView();
