// PDS-03: canonical, single-strike investigation report.
// The live chain row is the base record; Greeks only enrich it when present.
class StrikeDetailReportView {
  constructor() { this.selectedStrike = null; }

  render(strike) {
    const n = Number(strike != null ? strike : this.selectedStrike);
    const root = document.getElementById('strike-detail-report-content');
    if (!root || !Number.isFinite(n)) return false;
    this.selectedStrike = n;

    const d = _data || {};
    const row = (d.chain || []).find((r) => Number(r.strike) === n);
    if (!row) {
      root.innerHTML = '<div class="sdr-empty">The selected strike is not available in the current live chain.</div>';
      return false;
    }

    const greek = (d.greeks || []).find((g) => Number(g.strike) === n) || null;
    const velocity = this._velocity(d, n);
    const spot = this._number(d.spot);
    const atm = this._number(d.atm);
    const distancePct = spot ? ((n - spot) / spot) * 100 : null;
    const moneyness = distancePct == null ? '—' : (Math.abs(distancePct) < 0.05 ? 'ATM' : `${Math.abs(distancePct).toFixed(1)}% ${distancePct > 0 ? 'above' : 'below'} spot`);
    const score = this._number(row.footprintScore);
    const fs = (window.AppState && AppState.feedState) || {};
    const feedLabel = this._feedLabel(fs);
    const feedQualification = this._feedQualification(fs, greek);
    const stamp = d.lastUpdated ? new Date(d.lastUpdated) : (fs.lastMessageAt ? new Date(fs.lastMessageAt) : null);
    const asOf = stamp && !Number.isNaN(stamp.getTime())
      ? stamp.toLocaleString('en-IN', {day:'2-digit', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit', second:'2-digit'})
      : '—';
    const ceIv = row.ceIV != null ? row.ceIV : (greek && greek.iv != null ? greek.iv * 100 : null);
    const peIv = row.peIV != null ? row.peIV : (greek && greek.iv != null ? greek.iv * 100 : null);

    root.innerHTML = `
      <header class="sdr-hero" aria-labelledby="sdr-report-title">
        <div><div class="sdr-kicker">${this._escape(d.symbol || '—')} · ${this._escape(d.expiry || '—')}</div>
          <h2 id="sdr-report-title">Strike ${this._fmt(n, 0)}</h2><div class="sdr-sub">${moneyness}${atm === n ? ' · ATM' : ''}</div></div>
        <div class="sdr-live"><strong>${this._escape(feedLabel)}</strong><span>Timestamp ${this._escape(asOf)}</span><span>Spot ${this._fmt(spot, 2)}</span></div>
      </header>
      ${feedQualification ? `<div class="sdr-feed-note" role="status">${this._escape(feedQualification)}</div>` : ''}
      <section class="sdr-section"><h3>Strike Summary</h3><div class="sdr-grid sdr-grid-4">
        ${this._metric('Call state', row.ceSignal)}${this._metric('Put state', row.peSignal)}
        ${this._metric('Strike PCR', row.ceOI ? row.peOI / row.ceOI : null, 2)}
        ${this._metric('Importance', score == null ? null : score + ' / 100')}
      </div></section>
      <section class="sdr-section"><h3>Positioning</h3><div class="sdr-legs">
        ${this._leg('CALL', row, 'ce', ceIv)}${this._leg('PUT', row, 'pe', peIv)}
      </div></section>
      <section class="sdr-section"><h3>Greeks <span class="sdr-qualifier">Live enrichment</span></h3><div class="sdr-legs">
        ${this._greekLeg('CALL', greek, 'c', ceIv)}${this._greekLeg('PUT', greek, 'p', peIv)}
      </div></section>
      <section class="sdr-section"><h3>Capital</h3><p class="sdr-unit-note">₹ quantity terms · Delta is spot-notional exposure · Gamma is the OI × Γ × Spot² sensitivity proxy. Stage-2 exposures require verified live Greeks.</p><div class="sdr-legs">
        ${this._capitalLeg('CALL', row, 'ce')}${this._capitalLeg('PUT', row, 'pe')}
      </div></section>
      <section class="sdr-section"><h3>Why this strike matters</h3>
        ${this._importanceNarrative(row, score)}
        ${this._factorList(row.footprintFactors)}
        <div class="sdr-grid sdr-grid-3">
        ${this._metric('Footprint score', score == null ? null : score + ' / 100')}
        ${this._metric('OI concentration', this._dominant(row.ceOI, row.peOI))}
        ${this._metric('Capital direction', this._dominant(row.ceCapitalFlow, row.peCapitalFlow))}
      </div></section>
      <section class="sdr-section"><h3>Flow</h3><div class="sdr-legs">
        ${this._flowLeg('CALL', row, 'ce', velocity.ce)}${this._flowLeg('PUT', row, 'pe', velocity.pe)}
      </div></section>`;
    return true;
  }

  refresh() { if (this.selectedStrike != null) this.render(this.selectedStrike); }
  clear() { this.selectedStrike = null; }

  _leg(label, r, p, iv) {
    const oi = r[p + 'OI'], chg = r[p + 'ChgOI'], vol = r[p + 'Vol'];
    const ratio = oi ? this._number(vol) / this._number(oi) : null;
    return `<div class="sdr-leg"><h4>${label}</h4><div class="sdr-grid">${this._metric('OI', oi, 0)}${this._metric('ΔOI today', chg, 0)}${this._metric('Volume', vol, 0)}${this._metric('Vol / OI', ratio, 2)}${this._metric('LTP', r[p + 'LTP'], 2)}${this._metric('Live IV', iv, 2, '%')}</div></div>`;
  }
  _greekLeg(label, g, p, iv) {
    return `<div class="sdr-leg"><h4>${label}</h4><div class="sdr-grid">${this._metric('Delta', g && g[p + 'Delta'], 4)}${this._metric('Gamma', g && g[p + 'Gamma'], 6)}${this._metric('Theta', g && g[p + 'Theta'], 4)}${this._metric('Vega', g && g[p + 'Vega'], 4)}${this._metric('IV', iv, 2, '%')}</div></div>`;
  }
  _capitalLeg(label, r, p) {
    return `<div class="sdr-leg"><h4>${label}</h4><div class="sdr-grid">${this._metric('Premium locked', r[p + 'PremiumLocked'], 0, '₹')}${this._metric('Premium turnover', r[p + 'PremiumTurnover'], 0, '₹')}${this._metric('Strike notional', r[p + 'NotionalExposure'], 0, '₹')}${this._metric('Capital flow', r[p + 'CapitalFlow'], 0, '₹')}${this._metric('Delta spot-notional', r[p + 'DeltaExposure'], 0, '₹')}${this._metric('Gamma sensitivity', r[p + 'GammaExposure'], 0, '₹')}</div></div>`;
  }
  _flowLeg(label, r, p, velocity) {
    const directional = this._flowState(r[p + 'ChgOI'], r[p + 'Chg']);
    return `<div class="sdr-leg"><h4>${label}</h4><div class="sdr-flow-read">${this._escape(this._flowInterpretation(label, directional))}</div><div class="sdr-grid">${this._metric('Build / unwind', directional)}${this._metric('5m OI velocity', velocity[5], 0)}${this._metric('15m OI velocity', velocity[15], 0)}${this._metric('30m OI velocity', velocity[30], 0)}${this._metric('Volume change', r[p + 'VolChg'], 0)}</div></div>`;
  }
  _velocity(d, strike) {
    const out = {ce:{5:null,15:null,30:null}, pe:{5:null,15:null,30:null}};
    (d.oiVelocity || []).forEach((windowBlock) => {
      const win = Number(windowBlock.window);
      if (![5,15,30].includes(win)) return;
      const row = (windowBlock.rows || []).find((r) => Number(r.strike) === strike);
      if (row) { out.ce[win] = row.ceDOI; out.pe[win] = row.peDOI; }
    });
    return out;
  }
  _flowState(oiChg, priceChg) {
    const oi = this._number(oiChg), price = this._number(priceChg);
    if (oi == null) return null;
    if (oi >= 0) return price == null ? 'Build' : (price >= 0 ? 'Long build' : 'Short build');
    return price == null ? 'Unwind' : (price >= 0 ? 'Short covering' : 'Long unwind');
  }
  _flowInterpretation(label, state) {
    if (!state) return `${label} flow is unavailable.`;
    const meaning = {
      'Long build':'price and OI rising together',
      'Short build':'OI rising while price falls',
      'Short covering':'OI falling while price rises',
      'Long unwind':'price and OI falling together',
      'Build':'OI is being added', 'Unwind':'OI is being removed'
    }[state] || state.toLowerCase();
    return `${state}: ${meaning}.`;
  }
  _feedQualification(fs, greek) {
    const notes = [];
    if (fs.quality === 'PARTIAL') notes.push(`Partial feed${fs.missing && fs.missing.length ? `; missing ${fs.missing.join(', ')}` : ''}`);
    if (fs.status && !['LIVE','—'].includes(fs.status)) notes.push(`Feed status: ${fs.status}`);
    if (!greek) notes.push('Greeks unavailable; core chain, capital and flow remain live');
    return notes.join(' · ');
  }
  _feedLabel(fs) {
    const raw = fs.marketSession && fs.marketSession !== 'UNKNOWN' ? fs.marketSession : fs.status;
    return raw ? String(raw).replaceAll('_', ' ') : '—';
  }
  _importanceNarrative(r, score) {
    const side = this._dominant(Math.abs(this._number(r.ceCapitalFlow) || 0), Math.abs(this._number(r.peCapitalFlow) || 0));
    const level = score == null ? 'unranked' : score >= 70 ? 'high-ranked' : score >= 40 ? 'mid-ranked' : 'lower-ranked';
    const activity = side === 'Call-led' ? 'call-side capital activity leads' : side === 'Put-led' ? 'put-side capital activity leads' : 'capital activity is balanced';
    return `<p class="sdr-why">This is a <strong>${level}</strong> institutional footprint where ${activity}. The score is relative to the currently visible chain, not an absolute signal or trade recommendation.</p>`;
  }
  _factorList(factors) {
    const labels = {capitalActivity:'Capital flow',oiChangeActivity:'OI change',turnoverActivity:'Premium turnover',gammaActivity:'Gamma exposure',deltaActivity:'Delta exposure',writingActivity:'Option writing'};
    const ranked = Object.entries(factors || {}).map(([key,value]) => ({key,value:this._number(value)})).filter((x) => x.value != null && labels[x.key]).sort((a,b) => b.value-a.value);
    if (!ranked.length) return '<p class="sdr-factor-empty">Contributing factor ranks are unavailable in this feed.</p>';
    return `<ol class="sdr-factors" aria-label="Footprint score contributors">${ranked.map((x) => `<li><span>${labels[x.key]}</span><strong>${this._fmt(x.value,1)}th percentile</strong></li>`).join('')}</ol>`;
  }
  _dominant(ce, pe) {
    ce = this._number(ce); pe = this._number(pe);
    if (ce == null || pe == null) return null;
    if (ce === pe) return 'Balanced';
    return ce > pe ? 'Call-led' : 'Put-led';
  }
  _metric(label, value, decimals, suffix) {
    const numeric = typeof value === 'number';
    const shown = value == null || value === '' ? '—' : (numeric ? this._fmt(value, decimals == null ? 2 : decimals) + (suffix || '') : this._escape(value));
    return `<div class="sdr-metric"><span>${this._escape(label)}</span><strong>${shown}</strong></div>`;
  }
  _number(v) { const n = Number(v); return v == null || v === '' || !Number.isFinite(n) ? null : n; }
  _fmt(v, decimals) { const n = this._number(v); return n == null ? '—' : n.toLocaleString('en-IN', {minimumFractionDigits: decimals, maximumFractionDigits: decimals}); }
  _escape(v) { return escapeHtml(v); }
}
