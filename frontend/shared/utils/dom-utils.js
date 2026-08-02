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

// ── CLICK-GUARD (generic) ───────────────────────────────────────────────
// Same root-cause fix as ChainView's Decision Detail guard (chain-renderer.js
// _bindDecisionDetailGuard/refreshDecisionBoxGuarded), generalized so any
// live-refreshed card with plain action buttons can reuse it instead of
// hand-rolling the mousedown/mouseup race. Cards like #chain-summary-card
// ("Full Chain" button) and #inst-activity-summary-card ("Strike Detail
// Report" button) get outerHTML-swapped on every WS tick whenever their
// computed HTML changes (near-constant, since OI/price move every tick).
// If a tick lands between mousedown and the click committing — easily
// possible at several ticks/second — outerHTML tears the button out of the
// DOM mid-gesture and the browser cancels the click because its target was
// removed, so the click never fires at all. Reads as "the button is
// frozen." Fix: track an in-flight click per card and skip that tick's
// destructive rebuild while one is pending, same as the Decision Detail
// box already does for its <summary> toggle.
const _clickGuardPending = {};

// Call once right after (re)building `card`'s markup, before wiring
// anything else — mirrors _bindDecisionDetailGuard's contract. Binds to
// every <button> and any element with an inline onclick inside the card,
// so it also covers future buttons added to these cards without needing
// another call site update.
function bindCardClickGuard(card, guardKey){
  if(!card) return;
  _clickGuardPending[guardKey] = false;
  const actionEls = card.querySelectorAll('button, [onclick]');
  actionEls.forEach((el) => {
    let safetyTimer = null;
    const clearPending = () => {
      _clickGuardPending[guardKey] = false;
      clearTimeout(safetyTimer);
    };
    const setPending = () => {
      _clickGuardPending[guardKey] = true;
      // Safety net: if the gesture never completes (mouse released off the
      // element, window loses focus mid-press, etc.) `click` never fires
      // and clearPending would never run, permanently wedging this card's
      // live refresh. Cap how long a single gesture can hold the guard —
      // same value/reasoning as the Decision Detail guard's safety timer.
      clearTimeout(safetyTimer);
      safetyTimer = setTimeout(clearPending, 500);
    };
    el.addEventListener('mousedown', setPending);
    el.addEventListener('keydown', (e) => { if(e.key==='Enter'||e.key===' ') setPending(); });
    // `click` fires once the browser has actually committed the gesture
    // (after mouseup, and after the element's own onclick handler already
    // ran) — clearing here, not on mouseup, avoids reopening the same race
    // a tick landing between mouseup and click would otherwise hit.
    el.addEventListener('click', clearPending);
  });
}

function isCardClickPending(guardKey){
  return !!_clickGuardPending[guardKey];
}

// ── OUTERHTML DIFF-PATCH (generic) ──────────────────────────────────────
// Consolidates a pattern that used to be hand-rolled 7x in
// chain-renderer.js's _rerenderChainPanels (chain-summary-card,
// oi-flow-summary-card, greeks-alerts-card, fiidii-summary-card,
// inst-activity-summary-card, advanced-analytics-card, exec-section-wrap)
// — each one build-fresh-html / compare-to-dataset.lastHtml / outerHTML-
// swap / rebind-click-guard, differing only in which element/builder/guard
// key they used. Same fix shape as setHtmlIfChanged above, just for the
// outerHTML (whole-element, not innerHTML) case those cards need since
// setHtmlIfChanged's dataset-diff cache lives on the element itself and
// several of these cards' builders return their own wrapper element.
//
// opts:
//   guardKey     - if set, skip the patch entirely while a click on this
//                  card is mid-gesture (see isCardClickPending above)
//   bindGuard    - if true, (re)binds the click guard to the fresh element
//                  after a swap (pass guardKey too); cards with no action
//                  buttons of their own (oi-flow-summary-card, greeks-
//                  alerts-card, advanced-analytics-card) don't need this
//   preserveState / restoreState - optional pair for state that would
//                  otherwise be lost across the outerHTML rebuild (e.g.
//                  advanced-analytics-card's <details open> state)
function patchOuterHtmlIfChanged(elId, buildHtml, opts){
  opts = opts || {};
  const el = document.getElementById(elId);
  if(!el) return;
  if(opts.guardKey && isCardClickPending(opts.guardKey)) return;
  const freshHtml = buildHtml();
  if(el.dataset.lastHtml === freshHtml) return;
  const preserved = opts.preserveState ? opts.preserveState(el) : undefined;
  el.outerHTML = freshHtml;
  const fresh = document.getElementById(elId);
  if(!fresh) return;
  fresh.dataset.lastHtml = freshHtml;
  if(opts.bindGuard && opts.guardKey) bindCardClickGuard(fresh, opts.guardKey);
  if(opts.restoreState) opts.restoreState(fresh, preserved);
}
