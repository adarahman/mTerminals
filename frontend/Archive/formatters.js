// ============================================================
// formatters.js
// Extracted verbatim from dashboard.js (Task 1 modularization,
// step 2 — see master optimization prompt). No logic changed.
//
// Pure display-formatting helpers: no DOM access, no dependency on
// _wsState or any other dashboard state — just number/string -> string.
// Heavily used by chain-views.js and panels-views.js (fmtN/fmtI/fmtK
// especially), which is why this loads before them in DashboardPro.html,
// right after chart-legend.js and price-chart.js.
//
// Phase 1 bootstrap cleanup note: this file's <script> tag was missing
// from DashboardPro.html up to now — dashboard.js still carried its own
// duplicate copies of every function below (including a fmtI() that had
// quietly drifted to add minimumFractionDigits:2), so those duplicates,
// not this file, were what actually ran. This file's fmtI() has been
// synced to match, the <script src="formatters.js"> tag has been added,
// and dashboard.js's duplicates have been removed — same output, one
// source of truth instead of two.
// ============================================================

function fmt(n){ return n==null ? '—' : fmtK(n); }
function sign(n){ return n==null ? '—' : (n>0?'+':'') + n; }
function dirClass(n){ return n>0?'up':n<0?'down':'flat'; }
function cell(primary, secondary, primClass, secClass){
  return `<div class="cell"><span class="p ${primClass||''}">${primary}</span><span class="s ${secClass||''}">${secondary}</span></div>`;
}
function fmtN(v,d){if(v==null||isNaN(v))return'—';return parseFloat(v).toFixed(d===undefined?2:d);}
function fmtK(v){v=parseFloat(v)||0;if(Math.abs(v)>=100000)return(v/100000).toFixed(2)+'L';if(Math.abs(v)>=1000)return(v/1000).toFixed(1)+'K';return Math.round(v).toString();}
function fmtI(v){return(parseFloat(v)||0).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});}
function sClr(v){return v>=0?'var(--green)':'var(--red)';}
function ceOiChgClr(v){return v>=0?'var(--red)':'var(--green)';}
