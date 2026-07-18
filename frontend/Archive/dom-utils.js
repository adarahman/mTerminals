// ============================================================
// dom-utils.js
// Phase 1 bootstrap cleanup (see master optimization prompt, Task
// "Dashboard bootstrap cleanup"): dashboard.js is meant to hold ONLY
// app init/wiring/coordination now — these are generic, stateless DOM
// helpers with no opinion about market data or any particular panel, so
// they've been pulled out into their own file, same treatment ws-manager.js/
// market-store.js/formatters.js already got.
//
// $i/err/setHtmlIfChanged/sizeCanvasIfChanged are called by chain-views.js,
// panels-views.js, and dashboard.js — all of those calls happen at
// render/interaction time (never at parse time), so this file just needs
// to load before the browser fires its first render, not before any
// particular other script. Loaded early (right after formatters.js) so
// that's never in question. See DashboardPro.html script order.
//
// err(m) specifically is also called from ws-manager.js's connect() — that
// file's own header comment covers why the cross-file reference is safe.
// ============================================================

function $i(id){return document.getElementById(id);}
function err(m){const el=$i('err-msg');if(el)el.textContent=m;}

// ── FLICKER HELPERS ──────────────────────────────────────────────────────
// OI Flow never flickers because #oi-flow-body is small, text/color-only
// markup. Institutional F&O Simulator and Strategy Payoff flickered because
// every live tick unconditionally rewrote <select> option lists and reset
// <canvas> width/height (which resets the 2D context) even when nothing
// about that particular panel had actually changed. These two helpers make
// "re-render" mean "diff first, touch the DOM only if something changed" —
// the same effect OI Flow gets for free from being simple markup.

// Skip the innerHTML write entirely when the freshly-built HTML string is
// byte-identical to what's already there. Cheap string compare beats a
// guaranteed reflow/repaint on every single WS tick.
function setHtmlIfChanged(el, html){
  if(!el) return;
  if(el.dataset.lastHtml === html) return;
  el.innerHTML = html;
  el.dataset.lastHtml = html;
}

// Only touch canvas.width/height (which clears + resets the 2D context,
// forcing a full repaint) when the on-screen size actually changed. Redraw
// the contents every tick as before, but stop paying the resize cost for
// ticks where the layout hasn't moved — this is what removed the visible
// "flash" from the GEX and Strategy Payoff charts.
function sizeCanvasIfChanged(canvas, wCss, hCss){
  const dpr = window.devicePixelRatio || 1;
  const key = wCss + 'x' + hCss + '@' + dpr;
  const ctx = canvas.getContext('2d');
  if(canvas.dataset.sizeKey === key) return ctx;
  canvas.width  = wCss * dpr;
  canvas.height = hCss * dpr;
  canvas.style.width  = wCss + 'px';
  canvas.style.height = hCss + 'px';
  ctx.setTransform(1,0,0,1,0,0);
  ctx.scale(dpr, dpr);
  canvas.dataset.sizeKey = key;
  return ctx;
}
