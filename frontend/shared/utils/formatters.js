// ============================================================
// formatters.js
// Extracted verbatim from dashboard.js (Task 1 modularization,
// step 2 — see master optimization prompt). No logic changed.
//
// Pure display-formatting helpers: no DOM access, no dependency on
// _wsState or any other dashboard state — just number/string -> string.
// Heavily used by chain-views.js and panels-views.js (fmtN/fmtI/fmtK
// especially), which is why this loads before them in DashboardPro.html,
// right after chart-legend.js and price-chart-engine.js.
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

// Safe for both HTML text and quoted attribute values. Keeping this in the
// shared formatter layer prevents backend-supplied labels/reasons from being
// escaped differently by each dashboard surface.
function escapeHtml(value){
  return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[ch]);
}

// Indian compact units on an unscaled raw number. Unlike fmtK(), this keeps
// crore support for chain-level OI and volume totals.
function fmtCrLK(value){
  const v = Number(value);
  if(value == null || value === '' || !Number.isFinite(v)) return '—';
  const a = Math.abs(v);
  const prefix = v < 0 ? '-' : '';
  if(a >= 1e7) return prefix + (a / 1e7).toFixed(2) + 'Cr';
  if(a >= 1e5) return prefix + (a / 1e5).toFixed(2) + 'L';
  if(a >= 1e3) return prefix + (a / 1e3).toFixed(1) + 'K';
  return prefix + a.toFixed(0);
}

// v > 0 -> pos, v < 0 -> neg, v === 0 -> neutralVar (default --text-primary).
// Replaces netClr()/arpClr(), each independently reimplemented in
// exec-view.js, chain-template.js, chain-depth.js, and chain-renderer.js
// with three different CSS-var vocabularies between them (--green/--red,
// --pos/--neg, and --ce/--pe — the last of which is documented in
// theme.css as option-side identity color, not a sign color, so that
// usage was a semantic misuse, not just a naming mismatch).
function signColor(v, neutralVar){
  if (v > 0) return 'var(--pos)';
  if (v < 0) return 'var(--neg)';
  return neutralVar || 'var(--text-primary)';
}

// Robust price extent for chart auto-scaling. A single bad tick or spike
// candle must not flatten the whole visible series, so once enough points
// exist the scale fits the 5th/95th percentiles instead of the raw
// min/max. Small series keep the plain extent — there's not enough data
// to distinguish an outlier from a real move. Callers still apply their
// own padding on top of the returned {min,max}.
function robustPriceExtent(values){
  const nums = (values || []).map(Number).filter(Number.isFinite);
  if(!nums.length) return { min: 0, max: 1 };
  if(nums.length < 20){
    return { min: Math.min(...nums), max: Math.max(...nums) };
  }
  const sorted = nums.slice().sort((a, b) => a - b);
  const q = p => sorted[Math.min(sorted.length - 1, Math.max(0, Math.floor(p / 100 * (sorted.length - 1))))];
  return { min: q(5), max: q(95) };
}
