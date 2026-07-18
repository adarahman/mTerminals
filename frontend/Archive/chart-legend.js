(function() {
  const tags = ['Deep OTM','OTM','Near OTM','ATM','Near ITM','ITM','Deep ITM'];
  const order = ['-3', '-2', '-1', '0', '1', '2', '3'];
  const emptyData = () => new Array(tags.length).fill(null);
  // Which actual strikes got folded into each bucket on the last update —
  // used by the tooltip so hovering "Deep ITM" shows e.g. "24500, 24450, 24400"
  // instead of leaving you to guess.
  let bucketStrikesByIndex = order.map(() => []);

  // Raw (pre-normalization) values per dataset, keyed the same way as the
  // chart's own data arrays. The chart itself plots gamma/theta/vega on a
  // 0–1 normalized scale (see norm() below) so all four series share one
  // axis — but the tooltip should show the real Greek value the person is
  // actually hovering over, not the normalized fraction it's plotted at.
  let rawByDatasetIndex = [emptyData(), emptyData(), emptyData(), emptyData()];

  // The canvas now lives inside the dashboard's innerHTML template (next to
  // Strategy Payoff), which gets fully rebuilt on every live tick — so the
  // <canvas id="greeksChart"> element itself is destroyed and recreated
  // each render. `greeksChart` is therefore created lazily and re-bound
  // whenever the underlying canvas element changes, instead of being
  // built once at page load against a canvas that's guaranteed to persist.
  let greeksChart = null;

  function ensureGreeksChart() {
    const canvasEl = document.getElementById('greeksChart');
    if (!canvasEl) return null;
    if (greeksChart && greeksChart.canvas === canvasEl) return greeksChart;
    if (greeksChart) { try { greeksChart.destroy(); } catch (e) {} }

    greeksChart = new Chart(canvasEl, {
      type: 'line',
      data: {
        // Two-line ticks: actual strike on top, moneyness tag underneath —
        // e.g. ["24700", "Deep OTM"] — so the label means something even as
        // strikes shift tick to tick.
        labels: tags.map(t => ['—', t]),
        datasets: [
          { label: 'Delta (call)', data: emptyData(), borderColor: '#2a78d6', backgroundColor: '#2a78d6', borderWidth: 2, pointRadius: 3, pointStyle: 'circle', tension: 0.35, spanGaps: true },
          { label: 'Gamma', data: emptyData(), borderColor: '#1baf7a', backgroundColor: '#1baf7a', borderWidth: 2, pointRadius: 3, pointStyle: 'rect', borderDash: [6,3], tension: 0.35, spanGaps: true },
          { label: '|Theta| decay', data: emptyData(), borderColor: '#e34948', backgroundColor: '#e34948', borderWidth: 2, pointRadius: 3, pointStyle: 'triangle', borderDash: [2,2], tension: 0.35, spanGaps: true },
          { label: 'Vega', data: emptyData(), borderColor: '#eda100', backgroundColor: '#eda100', borderWidth: 2, pointRadius: 3, pointStyle: 'rectRot', tension: 0.35, spanGaps: true }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            // 'index' + intersect:false so hovering anywhere on a bucket's
            // x-position shows all four series' tooltip lines together,
            // not just whichever line the cursor happens to sit on top of.
            mode: 'index',
            intersect: false,
            callbacks: {
              // afterTitle appends the real strike list under the bucket
              // name in the tooltip header, e.g. "Deep ITM" -> "Strikes: 24400, 24450, 24500"
              afterTitle: (items) => {
                if (!items.length) return '';
                const strikes = bucketStrikesByIndex[items[0].dataIndex] || [];
                return strikes.length ? `Strikes: ${strikes.join(', ')}` : 'No strikes in range';
              },
              // The plotted value is a normalized 0–1 fraction (for gamma/
              // theta/vega) so it can share an axis with delta — show the
              // real underlying Greek value here instead.
              label: (item) => {
                const raw = (rawByDatasetIndex[item.datasetIndex] || [])[item.dataIndex];
                const dsLabel = item.dataset.label;
                if (raw === null || raw === undefined) return `${dsLabel}: —`;
                // Delta/Gamma need more decimal places (small magnitudes);
                // Theta/Vega read better at 2.
                const decimals = item.datasetIndex <= 1 ? 4 : 2;
                const sign = (item.datasetIndex === 2) ? '-' : ''; // theta shown as |Theta|, but real value is a decay (negative)
                return `${dsLabel}: ${sign}${raw.toFixed(decimals)}`;
              }
            }
          }
        },
        scales: {
          y: { min: 0, max: 1, ticks: { callback: (v) => v.toFixed(1), color: '#898781' }, grid: { color: '#e1e0d9' } },
          x: {
            ticks: {
              color: '#898781',
              autoSkip: false,
              maxRotation: 0,
              font: (ctx) => ({ size: ctx.chart.width < 420 ? 9 : 11 })
            },
            grid: { display: false }
          }
        }
      },
      // Keep the chart in sync when its grid column resizes (window resize,
      // sidebar toggle, etc.) — Chart.js's own ResizeObserver handles most of
      // this via `responsive: true`, but a manual nudge avoids stale sizing
      // right after a layout-affecting DOM change (e.g. panel collapse).
      plugins: [{
        id: 'resizeNudge',
        afterResize: (chart) => chart.update('none')
      }]
    });
    return greeksChart;
  }

  // ── Live data wiring ──────────────────────────────────────────────────
  // Call window.updateGreeksMoneynessChart(payload) from wherever the
  // dashboard's WebSocket onmessage handler dispatches the parsed JSON
  // (same payload shape mTerminals_json.py writes: needs `.greeks` — an
  // array of {strike, cDelta, cGamma, cTheta, cVega, ...} — and `.atm`).
  //
  // Bucketing: strike spacing is auto-detected from the live chain (no
  // hardcoded 50/100), then each strike is placed into a ±3-step bucket
  // relative to ATM (negative = OTM side/higher strikes, positive = ITM
  // side/lower strikes for a call). Strikes beyond ±3 steps fold into the
  // Deep OTM / Deep ITM buckets and are averaged together.
  window.updateGreeksMoneynessChart = function(payload) {
    const chart = ensureGreeksChart();
    if (!chart) return;
    const rows = payload && payload.greeks;
    const atm  = payload && payload.atm;
    if (!Array.isArray(rows) || rows.length === 0 || !atm) return;

    const strikes = [...new Set(rows.map(r => r.strike))].sort((a, b) => a - b);
    let step = Infinity;
    for (let i = 1; i < strikes.length; i++) {
      const d = strikes[i] - strikes[i - 1];
      if (d > 0 && d < step) step = d;
    }
    if (!isFinite(step) || step <= 0) step = 50; // fallback only if a single-strike chain sneaks in

    const buckets = { '-3': [], '-2': [], '-1': [], '0': [], '1': [], '2': [], '3': [] };
    rows.forEach(r => {
      const rawIdx = Math.round((atm - r.strike) / step);
      const idx = Math.max(-3, Math.min(3, rawIdx));
      buckets[String(idx)].push(r);
    });

    // A row with no live quote typically comes back as cDelta=cGamma=
    // cTheta=cVega=0 from the pricing pipeline rather than a real value.
    // Folded buckets (Deep OTM/Deep ITM) can contain several such illiquid
    // strikes alongside genuine near-money ones — averaging the zeros in
    // drags the whole bucket toward 0 and breaks delta's monotonic shape
    // (e.g. Deep ITM delta reading ~0.15 instead of climbing toward 1).
    // Filter those out before averaging; only fall back to including them
    // if a bucket has no real data at all.
    const hasQuote = (r) => [r.cDelta, r.cGamma, r.cTheta, r.cVega]
      .some(v => (Number(v) || 0) !== 0);

    // Build the actual strike label for each bucket: a single strike shows
    // as-is ("24700"); a folded bucket (Deep OTM/Deep ITM usually holds
    // several strikes) shows as a range ("25100+" / "≤24550").
    const strikesOf = (arr) => arr.map(r => r.strike).sort((a, b) => a - b);
    const strikesWithFlag = (arr) => arr
      .slice()
      .sort((a, b) => a.strike - b.strike)
      .map(r => hasQuote(r) ? String(r.strike) : `${r.strike} (no quote)`);
    bucketStrikesByIndex = order.map(k => strikesWithFlag(buckets[k]));
    const labelFor = (k, idx) => {
      const sk = strikesOf(buckets[k]);
      if (!sk.length) return ['—', tags[idx]];
      if (sk.length === 1) return [String(sk[0]), tags[idx]];
      // idx < 3 is the OTM side (higher strikes) -> "min+"; idx > 3 is ITM side -> "≤max"
      const label = idx < 3 ? `${sk[0]}+` : `≤${sk[sk.length - 1]}`;
      return [label, tags[idx]];
    };
    greeksChart.data.labels = order.map((k, i) => labelFor(k, i));

    const avg = (arr, key) => {
      const withQuote = arr.filter(hasQuote);
      const pool = withQuote.length ? withQuote : arr;
      return pool.length
        ? pool.reduce((s, r) => s + (Number(r[key]) || 0), 0) / pool.length
        : null;
    };

    const delta = order.map(k => avg(buckets[k], 'cDelta'));
    const gamma = order.map(k => avg(buckets[k], 'cGamma'));
    const theta = order.map(k => { const v = avg(buckets[k], 'cTheta'); return v === null ? null : Math.abs(v); });
    const vega  = order.map(k => avg(buckets[k], 'cVega'));

    // Stash the real (pre-normalization) values for the tooltip. delta is
    // plotted as-is (already 0–1), so its raw value is the plotted value;
    // gamma/theta/vega get normalized below purely for display, so their
    // raw values have to be captured here, before norm() overwrites them.
    rawByDatasetIndex = [delta, gamma, theta, vega];

    // Delta is natively 0–1. Gamma/theta/vega get normalized against this
    // chain's own peak so all four series read cleanly on one 0–1 axis.
    const norm = (arr) => {
      const vals = arr.filter(v => v !== null);
      const max = vals.length ? Math.max(...vals) : 0;
      return arr.map(v => v === null ? null : (max > 0 ? v / max : 0));
    };

    greeksChart.data.datasets[0].data = delta;
    greeksChart.data.datasets[1].data = norm(gamma);
    greeksChart.data.datasets[2].data = norm(theta);
    greeksChart.data.datasets[3].data = norm(vega);
    greeksChart.update('none'); // 'none' = no re-animation on every tick
  };
})();
