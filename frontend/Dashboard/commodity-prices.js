class CommodityPricesView {
  constructor() {
    this.symbol = 'CRUDEOIL';
    this.expiry = '';
    this.timer = null;
    this.loading = false;
    this.refreshPending = false;
  }

  open() {
    const picker = document.getElementById('commodity-symbol');
    if (picker) picker.value = this.symbol;
    if (this.timer !== null) window.clearInterval(this.timer);
    this.refresh();
    this.timer = window.setInterval(() => this.refresh(), 10000);
  }

  close() {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  select(symbol) {
    this.symbol = String(symbol || '').toUpperCase();
    this.expiry = '';
    this.refresh();
  }

  selectExpiry(expiry) {
    this.expiry = String(expiry || '').toUpperCase();
    this.refresh();
  }

  _setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  }

  _format(value, digits = 2) {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number)
      ? number.toLocaleString('en-IN', { maximumFractionDigits: digits })
      : '—';
  }

  async refresh() {
    if (this.loading) {
      this.refreshPending = true;
      return;
    }
    this.loading = true;
    this.refreshPending = false;
    const requestedSymbol = this.symbol;
    try {
      await Promise.all([
        this._refreshQuote(requestedSymbol),
        this._refreshOptionChain(requestedSymbol),
      ]);
    } finally {
      this.loading = false;
      if (this.refreshPending) this.refresh();
    }
  }

  async _refreshQuote(symbol) {
    const status = document.getElementById('commodity-price-status');
    if (status) {
      status.dataset.tone = '';
      status.textContent = 'Loading live MCX quote…';
    }
    try {
      const response = await fetch(
        `/api/commodities/quote?symbol=${encodeURIComponent(symbol)}`
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Quote request failed (${response.status}).`);
      }
      if (symbol !== this.symbol) return;
      const quote = payload.quote || {};
      this._setText('commodity-price-symbol', payload.symbol);
      this._setText('commodity-price-contract', `${quote.Contract || 'MCX'} · ${payload.provider}`);
      this._setText('commodity-price-ltp', this._format(quote.LTP));
      const change = quote.Change == null ? NaN : Number(quote.Change);
      const percent = quote.PctChange == null ? NaN : Number(quote.PctChange);
      const changeElement = document.getElementById('commodity-price-change');
      if (changeElement) {
        changeElement.textContent = Number.isFinite(change) && Number.isFinite(percent)
          ? `${change >= 0 ? '+' : ''}${this._format(change)} (${percent >= 0 ? '+' : ''}${this._format(percent)}%)`
          : '—';
        changeElement.classList.toggle('up', percent >= 0);
        changeElement.classList.toggle('down', percent < 0);
      }
      this._setText('commodity-price-expiry', quote.Expiry || '—');
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
      if (status) {
        status.dataset.tone = 'live';
        status.textContent = `Quote via ${payload.provider} · MCX`;
      }
    } catch (error) {
      if (symbol === this.symbol && status) {
        status.dataset.tone = 'error';
        status.textContent = error.message || 'Unable to load commodity quote.';
      }
    }
  }

  async _refreshOptionChain(symbol) {
    const status = document.getElementById('commodity-chain-status');
    if (status) {
      status.dataset.tone = '';
      status.textContent = 'Loading option chain…';
    }
    const requestedExpiry = this.expiry;
    const query = new URLSearchParams({ symbol });
    if (requestedExpiry) query.set('expiry', requestedExpiry);
    try {
      const response = await fetch(`/api/commodities/option-chain?${query}`);
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `Option-chain request failed (${response.status}).`);
      }
      if (symbol !== this.symbol || (requestedExpiry && requestedExpiry !== this.expiry)) return;
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
      this._renderOptionRows(payload.chain || {});
      this._renderAnalysis(payload.analysis || {});
      if (status) {
        status.dataset.tone = '';
        status.textContent = payload.chain.rows && payload.chain.rows.length
          ? `${payload.provider} · ${this._formatExpiry(payload.expiry)} · ATM ${this._format(payload.chain.atm_strike, 2)}`
          : `No option quotes available for ${this._formatExpiry(payload.expiry)}.`;
      }
    } catch (error) {
      if (symbol === this.symbol && status) {
        status.dataset.tone = 'error';
        status.textContent = error.message || 'Unable to load commodity option chain.';
      }
    }
  }

  _formatExpiry(expiry) {
    const match = /^(\d{2})([A-Z]{3})(\d{4})$/.exec(String(expiry || ''));
    return match ? `${match[1]}-${match[2][0]}${match[2].slice(1).toLowerCase()}-${match[3]}` : expiry;
  }

  _renderOptionRows(chain) {
    const body = document.getElementById('commodity-chain-rows');
    if (!body) return;
    const strikes = new Map();
    (chain.rows || []).forEach((contract) => {
      const row = strikes.get(contract.strike) || {};
      row[contract.type] = contract;
      strikes.set(contract.strike, row);
    });
    const fragment = document.createDocumentFragment();
    Array.from(strikes.keys()).sort((a, b) => a - b).forEach((strike) => {
      const contracts = strikes.get(strike);
      const row = document.createElement('tr');
      if (Number(strike) === Number(chain.atm_strike)) row.classList.add('atm');
      const values = [
        contracts.CE && contracts.CE.oi,
        contracts.CE && contracts.CE.volume,
        contracts.CE && contracts.CE.ltp,
        strike,
        contracts.PE && contracts.PE.ltp,
        contracts.PE && contracts.PE.volume,
        contracts.PE && contracts.PE.oi,
      ];
      values.forEach((value, index) => {
        const cell = document.createElement('td');
        const digits = [0, 1, 5, 6].includes(index) ? 0 : 2;
        cell.textContent = [0, 1, 5, 6].includes(index)
          ? fmtCrLK(value)
          : this._format(value, digits);
        if (index === 2) cell.classList.add('call-ltp');
        if (index === 4) cell.classList.add('put-ltp');
        row.appendChild(cell);
      });
      fragment.appendChild(row);
    });
    body.replaceChildren(fragment);
  }

  _renderAnalysis(analysis) {
    const verdict = document.getElementById('commodity-analysis-verdict');
    if (verdict) {
      verdict.textContent = analysis.verdict || 'WAIT';
      verdict.dataset.verdict = analysis.verdict || 'WAIT';
    }
    this._setText(
      'commodity-analysis-reason',
      analysis.reason || 'Insufficient option-chain data for a market view.'
    );
    this._setText(
      'commodity-analysis-confidence',
      `Confidence ${Number.isFinite(Number(analysis.confidence)) ? `${analysis.confidence}%` : '—'} · Coverage ${Number.isFinite(Number(analysis.evidence_coverage)) ? `${analysis.evidence_coverage}%` : '—'}`
    );
    this._renderEvidenceWeights(analysis.evidence_weights);
    this._setText(
      'commodity-analysis-pcr',
      this._format(analysis.put_call_oi_ratio, 2)
    );
    this._setText(
      'commodity-analysis-total-oi',
      `${fmtCrLK(analysis.call_oi)} / ${fmtCrLK(analysis.put_oi)}`
    );
    this._setText(
      'commodity-analysis-call-wall',
      this._format(analysis.call_oi_wall, 2)
    );
    this._setText(
      'commodity-analysis-put-wall',
      this._format(analysis.put_oi_wall, 2)
    );
    const futureChange = analysis.futures_change_pct;
    this._setText(
      'commodity-analysis-futures-change',
      futureChange == null
        ? '—'
        : `${Number(futureChange) >= 0 ? '+' : ''}${this._format(futureChange, 2)}%`
    );
    this._setText(
      'commodity-analysis-scope',
      `${analysis.scope || 'Visible strike window only.'} ${analysis.method || ''} Not a trade recommendation.`
    );
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
      weight.textContent = Number.isFinite(numericWeight)
        ? `${numericWeight}%`
        : '—';

      row.append(label, direction, weight);
      fragment.appendChild(row);
    });
    container.replaceChildren(fragment);
  }
}

window.commodityPrices = new CommodityPricesView();
