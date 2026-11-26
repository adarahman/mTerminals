// ============================================================
// data-service.js
// Phase 1 bootstrap cleanup (see master optimization prompt, Task
// "Dashboard bootstrap cleanup"): dashboard.js is meant to hold ONLY app
// init/wiring/coordination now. DataService owns the "state" and
// "websocket" responsibilities (file/paste loading, auto-refresh timer,
// WSManager/MarketStore wiring, and the coalesced-render scheduler) —
// pulled out verbatim into its own file, same treatment ws-manager.js/
// market-store.js already got.
//
// WSManager (ws-manager.js) owns only the socket lifecycle. MarketStore
// (market-store.js) owns the merged state and the full/delta/generic wire
// interpretation — see Phase 1 architecture split, master optimization
// prompt. Both must be loaded before this file, since DataService's
// constructor below does `new WSManager(...)` and `new MarketStore()`.
// This file itself must load before dashboard.js, since dashboard.js's
// App class does `new DataService()` at parse time (via `const app = new
// App()`). See DashboardPro.html script order.
// ============================================================

// ============================================================
// Bad-tick guard for the merged market state's spot price.
//
// Angel's feed intermittently emits a foreign-instrument print for a
// single message (observed live: ~142k Silver quotes surfacing on a
// ~340 Natural Gas request, ~154k variants, each flashing for ~1s).
// wsState.spot feeds EVERYTHING downstream — chart ticks, both modal
// right panes, the top bar, decision engine, chain ATM — so it is
// sanitized once here at ingest rather than in each consumer.
//
// A >5% jump within one message cannot be a real trade, but a genuine
// gap must not be suppressed forever: a deviating level is only
// accepted after persisting ~2.5s at the same price (±1%). Foreign
// prints vary every message, so they can never satisfy that — they are
// rejected indefinitely while the last good quote keeps rendering.
// ============================================================
const _SPOT_MAX_JUMP_PCT = 5.0;
const _SPOT_CONFIRM_MS = 2500;
// spot feeds the chart/top bar/right-pane primary card; future feeds the
// right-pane Futures card, the top-bar futures element and the chain
// futures meta — both arrive in the same WS payload and both can carry
// a foreign-instrument print, so both get the same guard. auxKeys are
// the derived fields restored alongside so they never disagree with the
// price they were computed from.
const _GUARDED_PRICE_FIELDS = {
  spot:   { aux: ['spotChange', 'spotChgPct'] },
  future: { aux: ['futureChange', 'basis'] },
  // Session OHLC ride the same payload and feed the mini-chart caption +
  // day-range band. They are sticky (a real new high repeats every
  // message, so it passes the persistence confirm) while foreign prints
  // vary and get clamped back to the last good session value.
  dayOpen: { aux: [] },
  dayHigh: { aux: [] },
  dayLow:  { aux: [] },
};
const _lastGoodPrice = {};    // field -> {symbol, value, aux:{...}}
const _priceCandidates = {};  // field -> {symbol, value, firstSeen}

function _guardPriceTick(state, field, auxKeys){
  const symbol = state.symbol ? String(state.symbol).toUpperCase() : '';
  const value = Number(state[field]);
  if(!symbol || !Number.isFinite(value) || value <= 0) return;
  const last = (_lastGoodPrice[field]
    && _lastGoodPrice[field].symbol === symbol)
    ? _lastGoodPrice[field] : null;
  const deviates = !!last
    && Math.abs(value - last.value) / last.value * 100 > _SPOT_MAX_JUMP_PCT;
  const aux = {};
  auxKeys.forEach(k => { aux[k] = state[k]; });
  if(!deviates){
    _priceCandidates[field] = null;
    _lastGoodPrice[field] = {symbol, value, aux};
    return;
  }
  const now = Date.now();
  const candidate = _priceCandidates[field];
  const sameLevel = !!candidate
    && candidate.symbol === symbol
    && Math.abs(value - candidate.value) / candidate.value * 100 <= 1.0;
  if(sameLevel && now - candidate.firstSeen >= _SPOT_CONFIRM_MS){
    // The new level held for seconds — a real gap, accept it.
    _priceCandidates[field] = null;
    _lastGoodPrice[field] = {symbol, value, aux};
    return;
  }
  _priceCandidates[field] = sameLevel
    ? candidate
    : {symbol, value, firstSeen: now};
  // Serve the last good quote instead of the bad print.
  state[field] = last.value;
  auxKeys.forEach(k => { state[k] = last.aux[k]; });
}

function _sanitizeSpotTick(state){
  if(!state) return;
  Object.keys(_GUARDED_PRICE_FIELDS).forEach(field => {
    const {aux} = _GUARDED_PRICE_FIELDS[field];
    _guardPriceTick(state, field, aux);
  });
  // Index pills (allIndices) ride the same WS payload and the same
  // foreign-row exposure — guard each pill's Last Price the same way.
  if(Array.isArray(state.allIndices)){
    state.allIndices.forEach(entry => _guardIndexPillTick(entry));
  }
  // indexQuotes rides its own WS message type ({NIFTY:{spot,spotChgPct}})
  // and feeds the non-active index pills — same foreign-row exposure.
  if(state.indexQuotes && typeof state.indexQuotes === 'object'){
    _guardIndexQuotesMap(state.indexQuotes);
  }
  // providerIndicativeQuotes ({symbol, quotes:{PROVIDER:{price}}}) feeds
  // the per-provider indicative prices in the chain panel.
  if(state.providerIndicativeQuotes && typeof state.providerIndicativeQuotes === 'object'){
    _guardProviderQuotes(state.providerIndicativeQuotes);
  }
  // casIndicative.value is the CAS aggregate price shown in the top-bar
  // badge — a computed price that can carry the same foreign prints.
  const cas = state.casIndicative;
  if(cas && typeof cas === 'object' && cas.status === 'live'){
    const casValue = Number(cas.value);
    if(Number.isFinite(casValue) && casValue > 0){
      const spotRef = Number(state.spot);
      // CAS legitimately tracks spot within a fraction of a percent — a
      // >5% gap while spot is sane means the CAS payload is foreign.
      if(Number.isFinite(spotRef) && spotRef > 0
          && Math.abs(casValue - spotRef) / spotRef * 100 > 5){
        cas.value = null;
        cas.status = 'unavailable';
        cas.reason = 'guarded_foreign_print';
      }
    }
  }
  // Option-chain strike LTPs arrive as per-tick deltas and can carry the
  // same foreign prints. A >10x jump on one leg is never a real option
  // tick — restore the last good print for that strike/side.
  if(Array.isArray(state.chain) && state.chain.length){
    _guardChainLtpTicks(state);
  }
}

// key: `${symbol}|${expiry}|${strike}|${side}` -> last accepted LTP
const _lastGoodChainLtp = {};
let _chainGuardKey = null; // `${symbol}|${expiry}` the map belongs to

function _guardChainLtpTicks(state){
  const symbol = String(state.symbol || '').toUpperCase();
  const expiry = String(state.expiry || '').toUpperCase();
  const scope = `${symbol}|${expiry}`;
  if(scope !== _chainGuardKey){
    for(const k in _lastGoodChainLtp) delete _lastGoodChainLtp[k];
    _chainGuardKey = scope;
  }
  state.chain.forEach(row => {
    if(!row || typeof row !== 'object') return;
    const strike = Number(row.strike);
    if(!Number.isFinite(strike)) return;
    ['ceLTP', 'peLTP'].forEach(field => {
      const side = field === 'ceLTP' ? 'CE' : 'PE';
      const ltp = Number(row[field]);
      if(!Number.isFinite(ltp)) return;
      const key = `${scope}|${strike}|${side}`;
      const last = _lastGoodChainLtp[key];
      // Stateless bound — no baseline needed: a call premium can never
      // exceed the underlying and a put premium can never exceed its
      // strike. A foreign FUTURES print (~143k) keyed as an option row
      // fails this on first sight, before any last-good exists.
      const spotRef = Number(state.spot);
      if(ltp > 0 && Number.isFinite(spotRef) && spotRef > 0){
        const bound = side === 'CE' ? spotRef * 1.2 : strike * 1.2;
        if(ltp > bound){
          row[field] = last > 0 ? last : null;
          return;
        }
      }
      // Zero/absent prints are legitimate for illiquid strikes — only a
      // positive print deviating >10x from the last good one is a bad tick.
      if(ltp > 0 && last > 0 && (ltp > last * 10 || ltp < last / 10)){
        row[field] = last;
        return;
      }
      _lastGoodChainLtp[key] = ltp;
    });
  });
}

const _lastGoodPill = {};      // symbol -> {value, change, pct}
const _pillCandidates = {};    // symbol -> {value, firstSeen}

function _guardIndexPillTick(entry){
  if(!entry || typeof entry !== 'object') return;
  const symbol = String(entry.BackendSymbol || entry.Symbol || '')
    .trim().toUpperCase();
  const value = Number(entry['Last Price']);
  if(!symbol || !Number.isFinite(value) || value <= 0) return;
  const last = _lastGoodPill[symbol] || null;
  const deviates = !!last
    && Math.abs(value - last.value) / last.value * 100 > _SPOT_MAX_JUMP_PCT;
  if(!deviates){
    _pillCandidates[symbol] = null;
    _lastGoodPill[symbol] = {
      value,
      change: entry['Change'],
      pct: entry['% Change'],
    };
    return;
  }
  const now = Date.now();
  const candidate = _pillCandidates[symbol];
  const sameLevel = !!candidate
    && Math.abs(value - candidate.value) / candidate.value * 100 <= 1.0;
  if(sameLevel && now - candidate.firstSeen >= _SPOT_CONFIRM_MS){
    _pillCandidates[symbol] = null;
    _lastGoodPill[symbol] = {
      value,
      change: entry['Change'],
      pct: entry['% Change'],
    };
    return;
  }
  _pillCandidates[symbol] = sameLevel
    ? candidate
    : {value, firstSeen: now};
  entry['Last Price'] = last.value;
  entry['Change'] = last.change;
  entry['% Change'] = last.pct;
}

// indexQuotes: {NIFTY:{spot,spotChgPct}, ...} — same last-good +
// persistence-confirm pattern as the pill guard, keyed per index.
const _lastGoodIndexQuote = {};   // symbol -> {spot, chgPct}
const _indexQuoteCandidates = {}; // symbol -> {spot, firstSeen}

function _guardIndexQuotesMap(map){
  Object.keys(map).forEach(symbol => {
    const entry = map[symbol];
    if(!entry || typeof entry !== 'object') return;
    const key = String(symbol).toUpperCase();
    const value = Number(entry.spot);
    if(!Number.isFinite(value) || value <= 0) return;
    const last = _lastGoodIndexQuote[key] || null;
    const deviates = !!last
      && Math.abs(value - last.spot) / last.spot * 100 > _SPOT_MAX_JUMP_PCT;
    if(!deviates){
      _indexQuoteCandidates[key] = null;
      _lastGoodIndexQuote[key] = {spot: value, chgPct: entry.spotChgPct};
      return;
    }
    const now = Date.now();
    const candidate = _indexQuoteCandidates[key];
    const sameLevel = !!candidate
      && Math.abs(value - candidate.spot) / candidate.spot * 100 <= 1.0;
    if(sameLevel && now - candidate.firstSeen >= _SPOT_CONFIRM_MS){
      _indexQuoteCandidates[key] = null;
      _lastGoodIndexQuote[key] = {spot: value, chgPct: entry.spotChgPct};
      return;
    }
    _indexQuoteCandidates[key] = sameLevel
      ? candidate
      : {spot: value, firstSeen: now};
    entry.spot = last.spot;
    entry.spotChgPct = last.chgPct;
  });
}

// providerIndicativeQuotes: {symbol, quotes:{PROVIDER:{price,asOf}}} —
// guard each provider's indicative price the same way, keyed
// `${symbol}|${provider}`.
const _lastGoodProviderQuote = {};
const _providerQuoteCandidates = {};

function _guardProviderQuotes(payload){
  const quotes = payload.quotes;
  if(!quotes || typeof quotes !== 'object') return;
  const symbol = String(payload.symbol || '').toUpperCase();
  if(!symbol) return;
  Object.keys(quotes).forEach(provider => {
    const entry = quotes[provider];
    if(!entry || typeof entry !== 'object') return;
    const value = Number(entry.price);
    if(!Number.isFinite(value) || value <= 0) return;
    const key = `${symbol}|${String(provider).toUpperCase()}`;
    const last = _lastGoodProviderQuote[key] || null;
    const deviates = !!last
      && Math.abs(value - last.price) / last.price * 100 > _SPOT_MAX_JUMP_PCT;
    if(!deviates){
      _providerQuoteCandidates[key] = null;
      _lastGoodProviderQuote[key] = {price: value};
      return;
    }
    const now = Date.now();
    const candidate = _providerQuoteCandidates[key];
    const sameLevel = !!candidate
      && Math.abs(value - candidate.price) / candidate.price * 100 <= 1.0;
    if(sameLevel && now - candidate.firstSeen >= _SPOT_CONFIRM_MS){
      _providerQuoteCandidates[key] = null;
      _lastGoodProviderQuote[key] = {price: value};
      return;
    }
    _providerQuoteCandidates[key] = sameLevel
      ? candidate
      : {price: value, firstSeen: now};
    entry.price = last.price;
  });
}

class DataService {
  constructor() {
    this.fsaHandle = null;
    this.legacyFile = null;
    this.data = null;
    this.autoRefreshTimer = null;
    this.countdownTimer = null;
    this.timerMins = Config.refresh.defaultAutoRefreshMins;
    this.renderScheduled = false;
    this.store = new MarketStore();
    this.wsManager = new WSManager(Config.ws.url);
    this.feedStatusTimer = null;
    this.lastStaleRecoveryAt = 0;
    this.snapshotRecoveryTimer = null;
    this.awaitingMarketSnapshot = true;
    this.pendingSymbol = null;
    this._setFeedStatus('CONNECTING');
    this.wsManager.on('open', () => {
      err('');
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--warn)';
      // Socket open is not the same thing as a coherent market snapshot.
      // Stay RECOVERING until the first message actually arrives.
      this._setFeedStatus('RECOVERING');
      this.awaitingMarketSnapshot = true;
      this._armSnapshotRecovery();
    });
    this.wsManager.on('close', () => {
      this._clearSnapshotRecovery();
      const dot=$i('ws-status'); if(dot) dot.style.background='var(--red)';
      this._setFeedStatus('DISCONNECTED', 'WebSocket closed');
    });
    // Raw wire message -> MarketStore interprets it -> 'change' fires with
    // the merged state, which is what actually drives a re-render. Feed
    // market freshness is updated BEFORE ingest so the top bar built by the
    // same tick already renders LIVE rather than one tick behind. Auxiliary
    // messages (portfolio, orders, funds, algoStatus, indexQuotes) prove the
    // transport is active but must not keep a frozen market snapshot LIVE.
    this.wsManager.on('message', (raw) => {
      this._markTransportMessage();

      if (raw && raw.type === 'control_ack' && raw.action === 'switch_data_source') {
        if (typeof window.applyDataSourceStatus === 'function') {
          window.applyDataSourceStatus(raw.status, raw.error);
        }
      } else if (raw && raw.type === 'control_error' && raw.action === 'switch_data_source') {
        if (typeof window.applyDataSourceStatus === 'function') {
          window.applyDataSourceStatus('unknown', raw.error);
        }
      }

      if (this.pendingSymbol && this._isMarketSnapshotMessage(raw)) {
        // A closing socket can have one buffered payload, and a newly opened
        // socket must establish a full baseline before any deltas are safe.
        // During a symbol handoff, accept only a full snapshot explicitly
        // belonging to the requested symbol.
        const isFull = !raw || !raw.type || raw.type === 'full';
        const payload = raw && raw.type === 'full' ? raw.payload : raw;
        const incomingSymbol = payload && payload.symbol
          ? String(payload.symbol).toUpperCase() : '';
        if (!isFull || incomingSymbol !== this.pendingSymbol) return;
        this.pendingSymbol = null;
      }
      if (this._isMarketSnapshotMessage(raw)) {
        this.awaitingMarketSnapshot = false;
        this._clearSnapshotRecovery();
        this._markFeedMessage();
      }
      this.store.ingest(raw);
    });
    this.feedStatusTimer = setInterval(() => this._checkFeedFreshness(), 1000);
    this.store.on('change', (state, change) => this.updateDashboard(state, change));
    this.store.on('baselineMismatch', () => {
      this._setFeedStatus('RECOVERING', 'Refreshing incompatible market snapshot');
      this.wsManager.connect(undefined, true);
    });
    // Tracks which symbol's DOM is currently built, so scheduleRender() can
    // force a full rebuild on a scrip switch instead of patching in place —
    // see the notYetBuilt/symbolChanged check in scheduleRender().
    this.lastRenderedSymbol = null;
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible') return;
      const last = (AppState.feedState && AppState.feedState.lastMessageAt) || 0;
      const staleAfter = (Config.ws && Config.ws.staleAfterMs) || 30000;
      const needsSnapshot = this.awaitingMarketSnapshot || Date.now() - last > staleAfter;
      this.wsManager.ensureConnected(needsSnapshot);
      if (!needsSnapshot && AppState.wsState) this.scheduleRender();
    });
    window.addEventListener('online', () => {
      this.wsManager.ensureConnected(true);
    });
  }

  _clearSnapshotRecovery(){
    if (this.snapshotRecoveryTimer) {
      clearTimeout(this.snapshotRecoveryTimer);
      this.snapshotRecoveryTimer = null;
    }
  }

  _armSnapshotRecovery(){
    this._clearSnapshotRecovery();
    this.snapshotRecoveryTimer = setTimeout(() => {
      this.snapshotRecoveryTimer = null;
      if (!this.awaitingMarketSnapshot) return;
      this._setFeedStatus('RECOVERING', 'Connected without a market snapshot; resyncing');
      this.wsManager.connect(undefined, true);
    }, 15000);
  }

  _setFeedStatus(status, reason){
    const prev = AppState.feedState || {};
    AppState.feedState = {
      status: status,
      quality: prev.quality || 'UNKNOWN',
      missing: prev.missing || [],
      marketSession: prev.marketSession || 'UNKNOWN',
      lastMessageAt: prev.lastMessageAt || null,
      lastTransportAt: prev.lastTransportAt || null,
      lastStatusAt: Date.now(),
      reason: reason || '',
      pipelineDelayed: prev.pipelineDelayed || false,
      pipelineReason: prev.pipelineReason || '',
    };
    this._updateFeedStatusDom();
    if (window.eventBus) window.eventBus.emit('feed:status', AppState.feedState);
  }

  _isMarketSnapshotMessage(raw){
    // A missing envelope is the legacy plain full-snapshot shape. Only the
    // versioned full/delta stream owns chain/Greeks/decision freshness.
    return !raw || !raw.type || raw.type === 'full' || raw.type === 'delta';
  }

  _applyPipelineStatus(status){
    if(!status) return;
    if(status.status === 'DELAYED'){
      const prev = AppState.feedState || {};
      const pipelineReason = status.reason || 'Analytics delayed; live prices continue';
      AppState.feedState = {
        ...prev,
        pipelineDelayed: true,
        pipelineReason,
        // Pipeline health is supplementary. It must not overwrite the
        // market transport state or its visible reason. The pipeline can
        // alternate DELAYED/LIVE every pass, which must not make the Feed
        // header flash or change size while WebSocket prices remain live.
        reason: prev.reason === prev.pipelineReason ? '' : prev.reason,
      };
      this._updateFeedStatusDom();
    } else if(status.status === 'LIVE'){
      const prev = AppState.feedState || {};
      AppState.feedState = {
        ...prev,
        pipelineDelayed: false,
        pipelineReason: '',
        reason: prev.reason === prev.pipelineReason ? '' : prev.reason,
      };
      this._updateFeedStatusDom();
    }
  }

  _markTransportMessage(){
    const prev = AppState.feedState || {};
    AppState.feedState = { ...prev, lastTransportAt: Date.now() };
  }

  _markFeedMessage(){
    const now = Date.now();
    const prev = AppState.feedState || {};
    AppState.feedState = {
      status: 'LIVE',
      quality: prev.quality || 'UNKNOWN',
      missing: prev.missing || [],
      marketSession: prev.marketSession || 'UNKNOWN',
      lastMessageAt: now,
      lastTransportAt: prev.lastTransportAt || now,
      lastStatusAt: prev.status === 'LIVE' ? (prev.lastStatusAt || now) : now,
      reason: '',
      pipelineDelayed: !!prev.pipelineDelayed,
      pipelineReason: prev.pipelineReason || '',
    };
    this._updateFeedStatusDom();
    if (window.eventBus && prev.status !== AppState.feedState.status) {
      window.eventBus.emit('feed:status', AppState.feedState);
    }
  }

  _updateDataQuality(state){
    const prev = AppState.feedState || {};
    const missing = [];
    if (!state || !Array.isArray(state.chain) || !state.chain.length) missing.push('chain');
    if (!state || !Array.isArray(state.greeks) || !state.greeks.length) missing.push('greeks');
    if (!state || !state.decision) missing.push('decision');
    AppState.feedState = {
      ...prev,
      quality: missing.length ? 'PARTIAL' : 'FULL',
      missing,
      marketSession: (state && state.marketSession) || prev.marketSession || 'UNKNOWN',
    };
    this._updateFeedStatusDom();
    if (window.eventBus && (prev.quality !== AppState.feedState.quality || prev.marketSession !== AppState.feedState.marketSession)) {
      window.eventBus.emit('feed:status', AppState.feedState);
    }
  }

  _checkFeedFreshness(){
    const fs = AppState.feedState || {};
    if (!fs.lastMessageAt) return;
    if (fs.status === 'DISCONNECTED' || fs.status === 'CONNECTING') return;
    const age = Date.now() - fs.lastMessageAt;
    const staleAfter = (Config.ws && Config.ws.staleAfterMs) || 30000;
    if (age > staleAfter) {
      const reason = `No market snapshot for ${Math.floor(age/1000)}s`;
      if (fs.status !== 'STALE' && fs.status !== 'RECOVERING') {
        this._setFeedStatus('STALE', reason);
      }
      const cooldown = (Config.ws && Config.ws.staleRecoveryCooldownMs) || staleAfter;
      if (Date.now() - this.lastStaleRecoveryAt >= cooldown) {
        this.lastStaleRecoveryAt = Date.now();
        this._setFeedStatus('RECOVERING', `${reason}; reconnecting`);
        this.wsManager.connect(undefined, true);
      }
    } else if (fs.status === 'STALE') {
      // Keep the age label moving without causing a Dashboard re-render.
      this._updateFeedStatusDom();
    }
  }

  _updateFeedStatusDom(){
    const el = $i('feed-status-pill');
    const fs = AppState.feedState || {status:'CONNECTING'};
    const status = fs.status || 'CONNECTING';
    const session = fs.marketSession || 'UNKNOWN';
    let visualStatus = status;
    let label = status;
    if (session === 'HOLIDAY') {
      visualStatus = 'HOLIDAY';
      label = status === 'DISCONNECTED' ? 'HOLIDAY · OFFLINE' : 'HOLIDAY';
    } else if (session === 'MARKET_CLOSED') {
      visualStatus = 'MARKET_CLOSED';
      label = status === 'DISCONNECTED' ? 'MARKET CLOSED · OFFLINE' : 'MARKET CLOSED';
    } else if (status === 'LIVE' && fs.quality === 'PARTIAL') {
      visualStatus = 'PARTIAL';
      label = 'PARTIAL';
    } else if (status === 'STALE' && fs.lastMessageAt) {
      label += ` ${Math.max(1, Math.floor((Date.now()-fs.lastMessageAt)/1000))}s`;
    }
    const missingTxt = fs.missing && fs.missing.length ? ` Missing: ${fs.missing.join(', ')}.` : '';
    const rawReasonText = fs.reason || (fs.quality === 'PARTIAL' ? missingTxt.trim() : '');
    // The MARKET CLOSED / HOLIDAY badge already communicates the normal
    // session state. Repeating "Market session is market closed" beside
    // it wastes top-bar width; preserve only actionable/non-redundant
    // reasons here. The full reason remains in the badge title above.
    const redundantSessionReason = (fs.marketSession === 'MARKET_CLOSED' || fs.marketSession === 'HOLIDAY')
      && /market session|market closed|holiday/i.test(rawReasonText);
    const reasonText = redundantSessionReason ? '' : rawReasonText;
    if (el) {
      el.textContent = label;
      el.dataset.status = visualStatus.toLowerCase().replace('_','-');
      const pipelineDetail = fs.pipelineDelayed && fs.pipelineReason ? ` Analytics: ${fs.pipelineReason}.` : '';
      el.title = (fs.reason || (fs.lastMessageAt ? `Last market snapshot ${new Date(fs.lastMessageAt).toLocaleTimeString()}` : status)) + missingTxt + pipelineDetail;
    }
    const reasonEl = $i('feed-status-reason');
    if (reasonEl) {
      reasonEl.textContent = reasonText;
      reasonEl.hidden = !reasonText;
    }
  }

  connectWebSocket(url){
    this._setFeedStatus('CONNECTING');
    this.wsManager.connect(url);
    this._renderStreamingControls();
  }

  setBrokerFeed(enabled){
    const sent = this.wsManager.send({type:'control_feed', payload:{enabled:!!enabled}});
    if (!sent) err('Connect the dashboard before controlling the broker feed.');
    return sent;
  }

  _renderStreamingControls(feedControl){
    if (!feedControl) return;
    const feedButton = $i('broker-feed-toggle');
    if (!feedButton) return;
    const enabled = !!feedControl.enabled;
    feedButton.dataset.running = enabled ? 'true' : 'false';
    feedButton.innerHTML = enabled
      ? '<span>■</span>Stop Stream' : '<span>▶</span>Start Stream';
    feedButton.title = feedControl.reason || `${enabled ? 'Stop' : 'Start'} the complete market-data cycle`;
  }

  beginSymbolSwitch(symbol){
    this.pendingSymbol = String(symbol || '').trim().toUpperCase() || null;
    this.awaitingMarketSnapshot = true;
    this._setFeedStatus('RECOVERING', this.pendingSymbol
      ? `Loading ${this.pendingSymbol}` : 'Loading symbol');

    // Never present the previous instrument's spot/change as if it belongs
    // to the newly selected symbol while the replacement snapshot is in
    // flight. The normal full render restores these nodes.
    ['topbar-spot', 'spot-chg-pts', 'spot-chg-pct'].forEach((id) => {
      const node = $i(id);
      if (!node) return;
      node.textContent = '—';
      node.classList.remove('tick-flash-up', 'tick-flash-down', 'chg-pos', 'chg-neg');
    });
  }

  // Called with the already-merged state (MarketStore.ingest ran before
  // emitting 'change') — this function is now pure side-effects: no more
  // branching on msg.type here, that lives in WSManager.
  updateDashboard(state, change){
  _sanitizeSpotTick(state);
  AppState.wsState = state;
  if(!AppState.wsState) return;
  const messageType = change && change.messageType;

  // Generic side-channel messages share the socket/store but do not change
  // chain, Greeks, decisions, charts, or market summaries. Route each one
  // directly to its owner instead of scheduling the entire dashboard pass.
  if(messageType === 'portfolio' || messageType === 'orders' || messageType === 'funds'){
    if(window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
    return;
  }
  if(messageType === 'pipelineStatus'){
    this._applyPipelineStatus(AppState.wsState.pipelineStatus);
    return;
  }
  if(messageType === 'feedControl'){
    this._renderStreamingControls(AppState.wsState.feedControl);
    const result = AppState.wsState.feedControl || {};
    if(result.reason) err(result.reason); else err('');
    return;
  }
  if(messageType === 'algoStatus'){
    if(window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
    return;
  }
  if(messageType === 'reconciliationAlert'){
    if(window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
    return;
  }
  if(messageType === 'indexQuotes'){
    if(window.patchIndexTicker) window.patchIndexTicker(AppState.wsState);
    return;
  }
  if(messageType === 'providerIndicativeQuotes'){
    if(app.chain && app.chain.patchProviderIndicativeQuotes){
      app.chain.patchProviderIndicativeQuotes(AppState.wsState.providerIndicativeQuotes);
    }
    return;
  }
  if(messageType === 'commodityQuote'){
    if(window.commodityPrices && typeof window.commodityPrices.applyLiveQuote === 'function'){
      window.commodityPrices.applyLiveQuote(AppState.wsState.commodityQuote);
    }
    return;
  }
  this._updateDataQuality(AppState.wsState);

  // Keep the native Price Chart buffer current even while its modal is
  // closed, so opening it never starts from an empty live series.
  if(window.priceChart && AppState.wsState.spot != null){
    window.priceChart.addTick(AppState.wsState.spot, Date.now(), null);
  }

  // applyExpirySelection() call removed (dead code — see chain-helpers.js):
  // expiry switching is handled by ChainView.onExpiryChange reconnecting
  // the WebSocket with ?expiry=..., not by splicing an alternate expiry's
  // chain into this tick's payload. This call was always a no-op since
  // app.chain.selectedExpiry (_selectedExpiry) is never written anywhere.

  // Keep canonical Option Chain rows ready for in-dashboard drill-downs.
  app.chainDense.refreshView(AppState.wsState);

  // Multiple WS messages (e.g. spot+oi+greeks) often arrive back-to-back
  // for the same logical tick. Coalesce them into a single render per
  // animation frame instead of doing a full rebuild for each one.
  scheduleRender();
}

  scheduleRender(){
  if(this.renderScheduled) return;
  this.renderScheduled = true;
  // ── WATCHDOG FIX ──
  // requestAnimationFrame alone silently stalls the entire live-update
  // pipeline (top bar, Decision Box, chain panels — everything gated
  // behind scheduleRender) whenever the browser throttles/suspends rAF
  // for this tab. Chromium does this for any tab that isn't the active
  // foreground tab UNLESS DevTools is attached to it, which is why this
  // used to "work fine with F12 open" and freeze solid with it closed —
  // WS messages kept arriving and MarketStore kept merging state the
  // whole time (that path isn't rAF-gated), but nothing ever got drawn
  // because the only thing turning state into DOM updates was stuck
  // waiting on a clamped/paused rAF callback.
  //
  // Fix: race the real rAF callback against a plain setTimeout watchdog.
  // Whichever fires first runs the render and cancels the other, so a
  // throttled rAF (typically clamped to ~1fps, not fully frozen) just
  // means the timeout wins most ticks instead — not that rendering stops.
  const runOnce = () => {
    if (!this.renderScheduled) return; // already run by the other trigger
    this.renderScheduled = false;
    if (rafId != null) cancelAnimationFrame(rafId);
    if (timeoutId != null) clearTimeout(timeoutId);
    doRender();
  };
  const rafId = requestAnimationFrame(runOnce);
  const timeoutId = setTimeout(runOnce, 250);
  const doRender = () => {
    if(!AppState.wsState) return;
    // Commodity quotes and option-chain data come from CommodityPricesView's
    // MCX endpoints. Equity WebSocket ticks must not replace its shared
    // `_data` payload or repaint the chain modal's underlying price.
    if (window.dashboardInstrumentMode === 'commodity') {
      this.lastRenderedSymbol = AppState.wsState.symbol || this.lastRenderedSymbol;
      if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
      if (window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
      if (window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
      return;
    }
    // ── FLICKER FIX ──
    // Every live tick used to go through parseAndRender() -> renderDashboard(),
    // which nukes and rebuilds the ENTIRE #dashboard subtree from a fresh
    // HTML string every time — that's what made every card flicker on every
    // tick. OI Flow never flickered because it was always patched in place
    // via #oi-flow-body.innerHTML only. The fix: give every tick that same
    // treatment. _rerenderChainPanels() already patches the chain table, DTE,
    // right panel, OI buildup/movers, IV surface, Greeks/GEX, OI velocity,
    // Simulator, and exec-grid in place, without touching unrelated DOM.
    // A full rebuild now only happens once, on the very first tick (when
    // #dashboard is still empty) or when the scrip itself changes.
    const dashEl = $i('dashboard');
    // Also treat "present but still hidden" as not-yet-built: #dashboard can
    // ship with non-empty skeleton/placeholder markup baked into the page
    // before any live data arrives, which made the old (innerHTML-only)
    // check false on tick #1 — skipping parseAndRender() entirely, which is
    // the ONLY code that ever flips #dashboard's display from none to block.
    // Net effect was the whole dashboard silently staying display:none
    // forever on the live-WS path even once real data was flowing.
    const notYetBuilt = !dashEl || !dashEl.innerHTML.trim() || dashEl.style.display === 'none';
    // ── SCRIP-SWITCH FIX ──
    // Symptom: switching symbol (NIFTY -> BANKNIFTY etc, e.g. reconnecting
    // to a different --symbol backend) updated the expiry dropdown and
    // chain table correctly (both driven through _rerenderChainPanels /
    // applyExpirySelection off the fresh AppState.wsState), but the Decision Engine
    // box kept showing the OLD scrip's bias/confidence/strategy until a
    // manual page refresh. Root cause: patchTopBarAndDecision() only ever
    // patches individual fields/DOM nodes in place — it was never designed
    // to detect "this is an entirely different instrument now", so nothing
    // forced it to redraw fields it assumes change rarely. Rather than
    // patch that assumption inside every incremental-update function, we
    // detect the scrip change once, here, at the dispatch point, and fall
    // back to a full rebuild — the same one already used for the very
    // first tick — which rebuilds the Decision Engine box (and everything
    // else) from scratch off the new symbol's data.
    const symbolChanged = AppState.wsState.symbol && this.lastRenderedSymbol && AppState.wsState.symbol !== this.lastRenderedSymbol;
    if (notYetBuilt || symbolChanged) {
      this.lastRenderedSymbol = AppState.wsState.symbol || this.lastRenderedSymbol;
      parseAndRender(JSON.stringify(AppState.wsState));
      if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
      if (window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
      if (window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
      return;
    }
          this.lastRenderedSymbol = AppState.wsState.symbol || this.lastRenderedSymbol;
    _data = AppState.wsState;
    if (window._afterRenderStratPayoff) _afterRenderStratPayoff();
    if (window._rerenderChainPanels) app.chain._rerenderChainPanels();
    // Decision Engine box + top-bar spot/badge/DTE strip are cheap to patch
    // in place too, so they stay live without rebuilding their containers.
    if (window.patchTopBarAndDecision) patchTopBarAndDecision(AppState.wsState);
    // Paper trading panel — lives outside #dashboard (self-mounted, see
    // bottom of file), so it's never touched by the rebuild above and just
    // needs its own cheap patch-in-place call here, same pattern as the
    // other panels on this line.
    if (window.renderPaperTradingPanel) window.renderPaperTradingPanel(AppState.wsState);
    // Algo status panel — same self-mounted, outside-#dashboard treatment
    // as the paper trading panel above. Its own setHtmlIfChanged guard
    // (algo-status.js) keeps this a no-op on ticks where algoStatus
    // itself hasn't changed since algo_status_loop() broadcasts on its
    // own slow independent timer, not tick-cadence.
    if (window.renderAlgoStatusPanel) window.renderAlgoStatusPanel(AppState.wsState);
    // Reconciliation alerts (risk/position_reconciler.py) — same
    // self-mounted, outside-#dashboard treatment, toast-based rather than
    // a persistent panel patch, so its own internal dedupe (algo-status.js's
    // _reconSeenTs) is what keeps this a no-op once an alert's already
    // been shown, not a setHtmlIfChanged guard like the panels above.
    if (window.renderReconciliationAlerts) renderReconciliationAlerts(AppState.wsState);
  };
}

  async triggerFile(){
  if(window.showOpenFilePicker){
    try{
      const [h]=await window.showOpenFilePicker({types:[{description:'JSON',accept:{'application/json':['.json']}}],multiple:false});
      _fsaHandle=h;_legacyFile=null;err('');
      await _readAndRender();
      startAutoRefresh(_timerMins);
    }catch(e){
      if(e.name!=='AbortError') err('File open error: '+e.message);
    }
  }else{
    $i('file-input').click();
  }
}

  handleFile(e){
  const f=e.target.files[0];
  if(!f)return;
  _legacyFile=f;_fsaHandle=null;err('');
  const r=new FileReader();
  r.onload=ev=>{parseAndRender(ev.target.result);startAutoRefresh(_timerMins);};
  r.onerror=()=>err('File read error.');
  r.readAsText(f,'utf-8');
}

  triggerPaste(){
  $i('paste-area').style.display='block';
  $i('load-go').style.display='inline-block';
  $i('paste-area').focus();
  err('');
}

  loadFromPaste(){
  parseAndRender($i('paste-area').value.trim());
}

  async _readAndRender(){
  try{
    let txt='';
    if(_fsaHandle){
      const file=await _fsaHandle.getFile();
      txt=await file.text();
    }else if(_legacyFile){
      txt=await _legacyFile.text();
    }else{
      err('No file loaded — use Open file first.');
      return;
    }
    if(!txt || txt.trim()===''){
      err('File is empty.');
      return;
    }
    parseAndRender(txt);
    err('');
  }catch(e){
    err('Read error: '+e.message);
  }
}

  async refreshDashboardFromExport(){
  if(!_fsaHandle && !_legacyFile){
    await triggerFile();
    return;
  }
  await _readAndRender();
}

  startAutoRefresh(mins=5){
  if(_autoRefreshTimer) clearInterval(_autoRefreshTimer);
  if(_countdownTimer) clearInterval(_countdownTimer);
  
  const intervalMs = mins * 60 * 1000;
  let remaining = intervalMs;
  
  function tick(){
    remaining -= 1000;
    if(remaining <= 0) remaining = intervalMs;
    const m = Math.floor(remaining / 60000);
    const s = Math.floor((remaining % 60000) / 1000);
    const timeStr = m + ':' + (s < 10 ? '0' : '') + s;
    
    const cd = document.getElementById('countdown-range');
    if(cd) cd.textContent = timeStr;
  }
  
  tick();
  _countdownTimer = setInterval(tick, 1000);
  
  _autoRefreshTimer = setInterval(async() => {
    if((_fsaHandle || _legacyFile) && document.getElementById('dashboard').style.display === 'block'){
      await _readAndRender();
      remaining = intervalMs;
    }
  }, intervalMs);
}

  parseAndRender(raw){
  if(!raw || raw.trim()===''){
    err('No data.');
    return;
  }
  let d;
  try{
    d=JSON.parse(raw);
  }catch(e){
    err('Parse error: '+e.message);
    return;
  }
  d=_fixMojibakeDeep(d);
  if(!d.spot){ err('Missing: spot'); return; }
  if(!d.chain || !d.chain.length){ err('Missing: chain'); return; }
  try{
    renderDashboard(d);
    if (window.updateGreeksMoneynessChart) window.updateGreeksMoneynessChart(d);
    $i('loader').style.display='none';
    $i('dashboard').style.display='block';
    err('');
  }catch(e){
    err('Render error: '+e.message);
    Logger.error('DataService', 'renderDashboard failed', e);
  }
}

  async doStickyRefresh(){
  const btn=document.getElementById('sticky-refresh-btn');
  if(btn){btn.classList.add('running');btn.textContent='🔄 Running…';}
  
  const cd=document.getElementById('countdown-range');
  if(cd) cd.textContent=_timerMins+':00';
  
  if(window._autoRefreshTimer){clearInterval(window._autoRefreshTimer);window._autoRefreshTimer=null;}
  if(window._countdownTimer){clearInterval(window._countdownTimer);window._countdownTimer=null;}
  
  try{
    await _readAndRender();
  }catch(e){
    const em=document.getElementById('err-msg');
    if(em) em.textContent='Refresh error: '+e.message;
  }finally{
    startAutoRefresh(_timerMins);
    if(btn){btn.classList.remove('running');btn.textContent='🔄 Refresh';}
  }
}
}
