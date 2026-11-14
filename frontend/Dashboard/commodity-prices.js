class CommodityPricesView {
  constructor() {
    this.symbol = 'CRUDEOIL';
    this.expiry = '';
    this.timer = null;
    this.chainTimer = null;
    this.newsTimer = null;
    this.quoteLoading = false;
    this.quoteRefreshPendingSymbol = '';
    this.chainLoading = false;
    this.newsLoading = false;
    this.newsRefreshPending = false;
    this.latestQuote = null;
    this.latestQuoteError = '';
    this.latestProvider = '';
    this.latestChainPayload = null;
    this.latestAnalysis = {};
  }

  open(symbol) {
    if (symbol) {
      const nextSymbol = String(symbol).toUpperCase();
      if (nextSymbol !== this.symbol) this._resetForSymbol(nextSymbol);
      this.symbol = nextSymbol;
      this.expiry = '';
    }
    window.commodityActiveSymbol = this.symbol;
    this.renderDashboard();
    if (this.timer !== null) window.clearInterval(this.timer);
    if (this.chainTimer !== null) window.clearInterval(this.chainTimer);
    if (this.newsTimer !== null) window.clearInterval(this.newsTimer);
    this.refresh();
    this.timer = window.setInterval(() => this._refreshQuote(this.symbol), 3000);
    this.chainTimer = window.setInterval(() => this._refreshOptionChain(this.symbol), 10000);
    this.refreshNews();
    this.newsTimer = window.setInterval(() => this.refreshNews(), 300000);
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
    if (this.newsTimer !== null) {
      window.clearInterval(this.newsTimer);
      this.newsTimer = null;
    }
  }

  select(symbol) {
    const nextSymbol = String(symbol || '').toUpperCase();
    if (nextSymbol !== this.symbol) this._resetForSymbol(nextSymbol);
    this.symbol = nextSymbol;
    window.commodityActiveSymbol = nextSymbol;
    this.expiry = '';
    this.refresh();
    this.refreshNews();
  }

  selectExpiry(expiry) {
    this.expiry = String(expiry || '').toUpperCase();
    this._refreshOptionChain(this.symbol);
  }

  _setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  }

  _resetForSymbol(symbol) {
    this.latestQuote = null;
    this.latestQuoteError = '';
    this.latestProvider = '';
    this.latestChainPayload = null;
    this.latestAnalysis = {};
    this._setText('commodity-price-contract', 'Loading contract…');
    this._setText('commodity-price-ltp', '—');
    const ltp = document.getElementById('commodity-price-ltp');
    if (ltp) ltp.classList.remove('up', 'down');
    const change = document.getElementById('commodity-price-change');
    if (change) {
      change.textContent = '—';
      change.classList.remove('up', 'down');
    }
    [
      'commodity-price-open', 'commodity-price-high',
      'commodity-price-low', 'commodity-price-volume', 'commodity-price-oi',
      'commodity-price-change-5m', 'commodity-price-oi-change-5m',
      'commodity-price-volume-change-5m',
    ].forEach((id) => this._setText(id, '—'));
    this._setText('commodity-price-lot-size', '—');
    const priceStatus = document.getElementById('commodity-price-status');
    if (priceStatus) {
      priceStatus.dataset.tone = '';
      priceStatus.dataset.status = 'connecting';
      priceStatus.textContent = 'CONNECTING';
    }
    const sourcePrice = document.getElementById('data-source-indicative-price');
    if (sourcePrice) sourcePrice.textContent = '₹—';
    this._setText('commodity-updated-time', '—');
    const expiryPicker = document.getElementById('commodity-expiry');
    if (expiryPicker) expiryPicker.replaceChildren();
    const chainStatus = document.getElementById('commodity-chain-status');
    if (chainStatus) {
      chainStatus.dataset.tone = '';
      chainStatus.textContent = `Loading ${symbol} option chain…`;
    }
    const chainHost = document.getElementById('commodity-chain-shared');
    if (chainHost) chainHost.replaceChildren();
    this._renderAnalysis({});
  }

  _clearOptionChain() {
    this.latestChainPayload = null;
    const host = document.getElementById('commodity-chain-shared');
    if (host) host.replaceChildren();
    this._renderSharedOptionChain(null);
    this._renderAnalysis({});
  }

  _format(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number)
      ? number.toLocaleString('en-IN', { maximumFractionDigits: digits })
      : '—';
  }

  async refresh() {
    const requestedSymbol = this.symbol;
    this._refreshQuote(requestedSymbol);
    this._refreshOptionChain(requestedSymbol);
  }

  async refreshNews() {
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
      if (symbol !== this.symbol) return;
      if (!response.ok) {
        throw new Error(payload.error || `News request failed (${response.status}).`);
      }
      this._renderNews(payload);
    } catch (error) {
      if (symbol !== this.symbol) return;
      const list = document.getElementById('commodity-news-list');
      if (list) list.replaceChildren();
      if (status) status.textContent = 'Headlines are unavailable right now. Try again later.';
    } finally {
      this.newsLoading = false;
      if (this.newsRefreshPending) {
        this.newsRefreshPending = false;
        this.refreshNews();
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
    if (this.quoteLoading) {
      // Keep the newest requested instrument. A slow response for the prior
      // symbol must not make the newly selected quote wait for another timer
      // tick (or overwrite it when it eventually completes).
      this.quoteRefreshPendingSymbol = symbol;
      return;
    }
    this.quoteLoading = true;
    const status = document.getElementById('commodity-price-status');
    if (status && document.getElementById('commodity-price-ltp')?.textContent === '—') {
      status.dataset.tone = '';
      status.textContent = 'Loading live MCX quote…';
    }
    try {
      const response = await fetch(
        `/api/commodities/quote?symbol=${encodeURIComponent(symbol)}`,
        { cache: 'no-store' }
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Quote request failed (${response.status}).`);
      }
      if (symbol !== this.symbol) return;
      const quote = payload.quote || {};
      this.latestQuote = quote;
      this.latestQuoteError = '';
      this.latestProvider = payload.provider || '';
      if (!document.getElementById('sec-topbar')) this.renderDashboard();
      const sourceSelect = document.getElementById('dataSourceSelect');
      if (sourceSelect && Array.from(sourceSelect.options).some(option => option.value === this.latestProvider)) {
        sourceSelect.value = this.latestProvider;
      }
      this._setText('commodity-price-contract', quote.Contract || 'MCX Futures');
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
          ? `${change >= 0 ? '+' : ''}${this._format(change)} (${percent >= 0 ? '+' : ''}${this._format(percent)}%)`
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
      this._setText('commodity-price-open', this._format(quote.Open));
      this._setText('commodity-price-high', this._format(quote.High));
      this._setText('commodity-price-low', this._format(quote.Low));
      this._setText('commodity-price-volume', fmtCrLK(quote.Volume));
      this._setText('commodity-price-oi', fmtCrLK(quote.OI));
      this._renderFiveMinuteContext(quote.FiveMinute);
      if (status) {
        status.dataset.tone = 'live';
        status.dataset.status = 'live';
        status.textContent = 'LIVE';
        status.title = `Live MCX quote via ${payload.provider || 'active provider'}`;
      }
      const sourcePrice = document.getElementById('data-source-indicative-price');
      if (sourcePrice) {
        sourcePrice.textContent = `₹${this._format(quote.LTP)}`;
        sourcePrice.title = `MCX futures price via ${payload.provider || 'active provider'}`;
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
    } catch (error) {
      if (symbol === this.symbol && status) {
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
          this._setText('commodity-price-contract', 'MCX quote unavailable');
          this._setText('commodity-price-ltp', '—');
          const ltp = document.getElementById('commodity-price-ltp');
          if (ltp) ltp.classList.remove('up', 'down');
          this._setText('commodity-price-lot-size', '—');
          this._setText('commodity-price-open', '—');
          this._setText('commodity-price-high', '—');
          this._setText('commodity-price-low', '—');
          this._setText('commodity-price-volume', '—');
          this._setText('commodity-price-oi', '—');
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
      this.quoteLoading = false;
      const pendingSymbol = this.quoteRefreshPendingSymbol;
      this.quoteRefreshPendingSymbol = '';
      if (pendingSymbol && pendingSymbol === this.symbol) {
        this._refreshQuote(pendingSymbol);
      }
    }
  }

  async _refreshOptionChain(symbol) {
    if (this.chainLoading) return;
    this.chainLoading = true;
    const status = document.getElementById('commodity-chain-status');
    if (status && !document.querySelector('#commodity-chain-shared .oc-ledger-row')) {
      status.dataset.tone = '';
      status.textContent = 'Loading option chain…';
    }
    const requestedExpiry = this.expiry;
    const query = new URLSearchParams({ symbol });
    if (requestedExpiry) query.set('expiry', requestedExpiry);
    try {
      const response = await fetch(`/api/commodities/option-chain?${query}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Option-chain request failed (${response.status}).`);
      }
      if (symbol !== this.symbol || (requestedExpiry && requestedExpiry !== this.expiry)) return;
      this.latestChainPayload = payload;
      this.latestAnalysis = payload.analysis || {};
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
      if (status) {
        status.dataset.tone = '';
        status.textContent = payload.chain.rows && payload.chain.rows.length
          ? `${payload.provider} · ATM ${this._format(payload.chain.atm_strike, 2)}`
          : 'No option quotes available for this expiry.';
      }
    } catch (error) {
      if (symbol === this.symbol && requestedExpiry === this.expiry && status) {
        status.dataset.tone = 'error';
        status.textContent = error.message || 'Unable to load commodity option chain.';
        this._clearOptionChain();
      }
    }
    finally {
      this.chainLoading = false;
    }
  }

  _formatExpiry(expiry) {
    const match = /^(\d{2})([A-Z]{3})(\d{4})$/.exec(String(expiry || ''));
    return match ? `${match[1]}-${match[2][0]}${match[2].slice(1).toLowerCase()}-${match[3]}` : expiry;
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
  }

  _renderAnalysis(analysis) {
    this.latestAnalysis = analysis || {};
    const decision = document.getElementById('sec-decision');
    const chainView = typeof app !== 'undefined' && app.chain;
    if (decision && chainView && typeof chainView.refreshDecisionBoxGuarded === 'function') {
      chainView.refreshDecisionBoxGuarded(this._toSharedDashboardData());
    } else {
      this.renderDashboard();
    }
  }

  _toSharedDashboardData() {
    const quote = this.latestQuote || {};
    const payload = this.latestChainPayload || {};
    const rawChain = payload.chain || {};
    const analysis = this.latestAnalysis || {};
    const rowsByStrike = new Map();
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
      row[`${side}LTP`] = contract.ltp == null ? null : Number(contract.ltp);
      row[`${side}Vol`] = Number(contract.volume) || 0;
      row[`${side}IV`] = contract.iv == null ? null : Number(contract.iv);
      rowsByStrike.set(row.strike, row);
    });
    const chain = Array.from(rowsByStrike.values()).sort((a, b) => a.strike - b.strike);
    const steps = chain.slice(1).map((row, index) => row.strike - chain[index].strike)
      .filter((step) => Number.isFinite(step) && step > 0);
    const bias = ['BULLISH', 'BEARISH', 'NEUTRAL', 'WAIT'].includes(analysis.verdict)
      ? analysis.verdict : 'WAIT';
    const short = analysis.short_horizon || null;
    const contributors = (analysis.contributors || []).map((item) => ({
      ...item,
      weightedContribution: item.weightedContribution,
    }));
    const pcr = analysis.put_call_oi_ratio;
    const expiry = payload.expiry || quote.Expiry || '';
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
      spot: Number(quote.LTP) || Number(rawChain.spot) || 0,
      spotChange: Number(quote.Change) || 0,
      spotChgPct: Number(quote.PctChange) || 0,
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
      oiChgPCR: undefined,
      greeks: [],
      oiVelocity: [],
      compositeBias: bias,
      maxPain: analysis.visible_window_max_pain,
      ceWall: analysis.call_oi_wall,
      peWall: analysis.put_oi_wall,
      risk: { tradeGrade: '—', ivRegime: 'Unavailable', trapWarn: 'none' },
      decision: {
        bias,
        biasStrength: analysis.bias_strength || '',
        confidence: Number(analysis.confidence) || 0,
        evidenceCoverage: Number(analysis.evidence_coverage) || 0,
        contributors,
        missingInputs: analysis.missing_inputs || [],
        degraded: !!(analysis.missing_inputs || []).length,
        conflictFlag: analysis.verdict === 'NEUTRAL',
        action: 'Informational view · commodity order execution is not enabled here',
        tradeability: 'NO_TRADE',
        tradeabilityReason: analysis.reason || 'Waiting for commodity option-chain evidence.',
        strategyCaution: 'MCX analysis is informational; verify contract liquidity and broker execution.',
        verdicts: {
          pcr: pcr == null ? '—' : `${pcr} — Put / call open interest`,
          vix: 'Unavailable — not applicable to MCX contracts',
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
    if (typeof _data !== 'undefined') _data = data;
    return data;
  }

  renderDashboard() {
    const dashboard = document.getElementById('dashboard');
    const slot = document.getElementById('commodity-dashboard-slot');
    const chainView = typeof app !== 'undefined' && app.chain;
    if (!dashboard || !slot || !chainView) return;
    const data = this._toSharedDashboardData();
    const expiryPicker = document.getElementById('commodity-expiry');
    const expiryHolder = document.getElementById('commodity-expiry-holder');
    if (expiryPicker && expiryHolder && expiryPicker.parentNode !== expiryHolder) {
      expiryHolder.appendChild(expiryPicker);
    }
    dashboard.style.display = 'block';
    dashboard.innerHTML = `${chainView.renderTopBarHtml(data, data.compositeBias === 'BEARISH')}${chainView.renderDecisionBoxHtml(data)}`;
    slot.hidden = false;
    dashboard.append(slot);
    const expirySlot = document.getElementById('commodity-expiry-slot');
    if (expiryPicker && expirySlot) expirySlot.appendChild(expiryPicker);
    this._renderSharedOptionChain(this.latestChainPayload);
    chainView._bindDecisionDetailGuard();
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

  _renderEvidenceWeights(weights) {
    const container = document.getElementById('commodity-analysis-weightage-rows');
    if (!container) return;

    const rows = Array.isArray(weights) ? weights : [];
    const fragment = document.createDocumentFragment();
    rows.forEach((item) => {
      const row = document.createElement('div');
      row.className = 'commodity-analysis-weightage-row';

      const label = document.createElement('span');
      label.textContent = item.label || item.key || 'Evidence';

      const direction = document.createElement('span');
      const directionValue = item.available === false
        ? 'UNAVAILABLE'
        : String(item.direction || 'UNAVAILABLE').toUpperCase();
      const directionClass = ['BULLISH', 'BEARISH', 'NEUTRAL'].includes(directionValue)
        ? directionValue.toLowerCase()
        : 'unavailable';
      direction.className = `commodity-analysis-weightage-direction ${directionClass}`;
      direction.textContent = directionValue === 'UNAVAILABLE'
        ? 'Unavailable'
        : directionValue;

      const weight = document.createElement('strong');
      const numericWeight = Number(item.weight);
      const contribution = Number(item.weightedContribution);
      weight.textContent = Number.isFinite(numericWeight)
        ? `${numericWeight}%${Number.isFinite(contribution) ? ` · ${contribution >= 0 ? '+' : ''}${contribution.toFixed(3)}` : ''}`
        : '—';

      row.append(label, direction, weight);
      fragment.appendChild(row);
    });
    container.replaceChildren(fragment);
  }
}

window.commodityPrices = new CommodityPricesView();
