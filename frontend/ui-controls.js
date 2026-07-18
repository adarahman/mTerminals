// ============================================================
// ui-controls.js
// Phase 1 bootstrap cleanup (see master optimization prompt, Task
// "Dashboard bootstrap cleanup"): dashboard.js is meant to hold ONLY app
// init/wiring/coordination now. UiControls is a self-contained "UI"
// subsystem (sticky-offset measurement, the auto-refresh timer tab strip,
// the range/velocity flyout, and section-jump nav) with no market-data
// opinions of its own — pulled out verbatim, same treatment ws-manager.js/
// market-store.js already got.
//
// Depends on $i (dom-utils.js, loaded earlier) and on the global
// startAutoRefresh(...) compatibility shim / app.data.timerMins that
// dashboard.js's App-bootstrap section defines — those are only touched
// from inside switchTimer(), which (like every other UI handler here)
// only ever runs after a user interaction, i.e. long after all scripts +
// dashboard.js's wiring have finished running. See DashboardPro.html
// script order.
// ============================================================

// ── STICKY HEADER STACK ──
// .sec-nav (#sec-nav-bar) and .top-bar (#sec-topbar) are stacked sticky
// elements, followed by .chain-right-panel further down. Their `top`
// offsets used to be hand-picked pixel guesses (62px / 116px) baked into
// the CSS, which silently drift out of sync whenever either bar's real
// height changes — e.g. the VIX pill wrapping the top-bar to two lines,
// or the risk/strategy nav buttons toggling visible — causing the bar
// below to overlap or leave a gap. This measures the actual rendered
// heights and feeds them back in as CSS custom properties so the offsets
// stay correct no matter what's currently showing.
const STICKY_GAP = 6; // matches .sec-nav / .top-bar margin-bottom
const STICKY_BASE_TOP = 8; // matches .sec-nav's own `top`

class UiControls {
  updateStickyOffsets(){
  const root = document.documentElement.style;
  const topBar = $i('sec-topbar');

  // The vertical nav rail (#sec-nav-bar) is now a fixed left-edge stripe,
  // not stacked above the top-bar, and the index ticker no longer has its
  // own row (it's inline inside the top-bar) — so the top-bar just sits
  // near the top of the viewport instead of being pushed down by either.
  const topBarTop = STICKY_BASE_TOP;
  root.setProperty('--topbar-top', topBarTop + 'px');

  const topBarH = topBar ? topBar.getBoundingClientRect().height : 44;
  const panelTop = topBarTop + topBarH + STICKY_GAP;
  root.setProperty('--panel-top', panelTop + 'px');

  // The old standalone `.head` bar above the chain table was removed
  // (see DashboardPro.html), so there is no local header height to add
  // to the sticky <thead> offset anymore. Force --head-h to 0 instead of
  // leaving it at a stale/default non-zero value — that stale value is
  // what was pushing the sticky thead down into the middle of the table
  // body instead of sitting flush above row 1.
  root.setProperty('--head-h', '0px');
}

  switchTimer(mins,el){
  app.data.timerMins=mins;
  document.querySelectorAll('[id^="timer-btn-"]').forEach(b=>b.classList.remove('active-range'));
  if(el) el.classList.add('active-range');
  startAutoRefresh(mins);
}

  toggleControlSidebar(){
  const el=$i('ctrl-sidebar');
  if(!el) return;
  const opening = !el.classList.contains('open');
  el.classList.toggle('open');
  if(opening){
    // Panel is no longer docked next to a fixed top-right toggle — that
    // button moved into the left #sec-nav-bar rail as "Range/Vel", so
    // position the flyout right next to wherever that button actually is
    // (its section-nav position can shift slightly depending on which
    // sec-btn-* items are visible) instead of a stale hardcoded spot.
    const btn = $i('ctrl-sidebar-toggle-btn');
    if(btn){
      const r = btn.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      // Give it a minimum offset from the nav rail so it never hugs the
      // literal edge (r.right is tiny for the narrow left rail, which was
      // pinning the panel flush against x≈80px — reading as clipped/cut-off
      // text and making it look like a stray row glued under whatever
      // section happened to be at that scroll position instead of a
      // floating flyout next to its own toggle button).
      const EDGE_MARGIN = 16;
      let left = Math.max(r.right + 8, EDGE_MARGIN);
      let top  = r.top;
      // Clamp so the panel can't render partially off-screen once its
      // max-width expands (measure after the 'open' class is applied).
      requestAnimationFrame(()=>{
        const pw = el.offsetWidth || 320;
        const ph = el.offsetHeight || 60;
        if(left + pw > vw - 8) left = Math.max(EDGE_MARGIN, vw - pw - 8);
        if(top + ph > vh - 8) top = Math.max(EDGE_MARGIN, vh - ph - 8);
        el.style.left = left + 'px';
        el.style.top  = top + 'px';
      });
      el.style.left = left + 'px';
      el.style.top  = top + 'px';
    }
  }
}

  secJump(id){
  let el=document.getElementById(id)
    || document.getElementById(id.replace(/-static$/,''))
    || document.getElementById(id+'-static');
  if(!el) return;
  
  const resolvedId=el.id;
  el.scrollIntoView({behavior:'smooth',block:'start'});
  
  // Update nav buttons
  document.querySelectorAll('#sec-nav-bar .sec-btn:not(#sticky-refresh-btn)').forEach(b=>{
    const fn=b.getAttribute('onclick')||'';
    const btnId=(fn.match(/secJump\('([^']+)'\)/)||[])[1]||'';
    const base=s=>s.replace(/-static$/,'');
    b.classList.toggle('active', base(btnId)===base(resolvedId));
  });
  
  el.style.outline='2px solid rgba(51,154,240,0.5)';
  el.style.outlineOffset='2px';
  setTimeout(()=>{el.style.outline='';el.style.outlineOffset='';},900);
}

}

// Unused standalone instance, pre-existing before this extraction (the
// live UI is always driven through app.ui — see dashboard.js's App class).
// Kept as-is: this file is a verbatim move, not a behavior change.
const ui = new UiControls();