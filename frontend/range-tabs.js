// ============================================================
// range-tabs.js
// The ATM range tab-group (±3 / ±5 / ±10 / ±15 / All, each calling
// switchChainRange(n,this)) used to be hand-copied into three places in
// dashboard-v26.html — the sidebar (#range-tabs-side), the Greeks/GEX
// modal (#range-tabs-grk), and the IV Surface modal (#range-tabs-iv).
// switchChainRange() already treats every element whose id starts with
// "range-tabs-" as one synced group (see chain-view.js), so the only
// thing actually duplicated was the markup itself. This file is the one
// place that markup is defined; the HTML just leaves an empty
// <div class="tab-group" id="range-tabs-XXX" data-range-tabs></div> in
// each spot and this script fills all of them in on load.
//
// To add/remove a range option (e.g. a ±20 band), edit RANGE_TAB_OPTIONS
// below — every instance across the app picks it up automatically.
// ============================================================

const RANGE_TAB_OPTIONS = [
  { value: 3,    label: '\u00B13' },
  { value: 5,    label: '\u00B15' },
  { value: 10,   label: '\u00B110' },
  { value: 15,   label: '\u00B115' },
  { value: 9999, label: 'All' },
];
const RANGE_TAB_DEFAULT = 10;

function buildRangeTabsHtml(options, defaultValue) {
  options = options || RANGE_TAB_OPTIONS;
  defaultValue = defaultValue != null ? defaultValue : RANGE_TAB_DEFAULT;
  return options.map(function (opt) {
    const active = opt.value === defaultValue ? ' active-range' : '';
    return '<div class="tab-btn' + active + '" onclick="switchChainRange(' + opt.value + ',this)">' + opt.label + '</div>';
  }).join('');
}

// Fills every placeholder present at call time. Safe to call again later
// (e.g. after a modal's markup is rebuilt) — it just overwrites innerHTML,
// it doesn't care whether this is the first pass or a re-render.
function renderRangeTabs(root) {
  (root || document).querySelectorAll('[data-range-tabs]').forEach(function (el) {
    el.innerHTML = buildRangeTabsHtml();
  });
}

// The three placeholder <div>s in dashboard-v26.html sit above this
// script in document order, so they already exist in the DOM by the time
// this file executes — no DOMContentLoaded wait needed.
renderRangeTabs();
