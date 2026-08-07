// ============================================================
// conviction-gauge.js
// Conviction Multiplier Gauge — a 4-pillar alignment check that only
// flashes a directional "Go" verdict when at least 3 of 4 independent
// reads agree, instead of leaving the reader to reconcile conflicting
// sub-signals themselves (e.g. PCR reading bullish while the Decision
// Engine sits neutral-weak). Each pillar casts one vote: +1 bullish,
// -1 bearish, or 0 (abstain — insufficient signal either way, and
// abstaining pillars don't count against alignment). The verdict looks
// only at non-abstaining votes: >=3 agreeing bullish -> GO LONG, >=3
// agreeing bearish -> GO SHORT, exactly 2 agreeing (and no opposite
// vote) -> LEAN, otherwise WAIT (mixed or too few pillars voting).
//
// Pillars, each reusing data/functions this app already computes —
// nothing here talks to the backend directly:
//   1. FII/DII index-futures net flow direction, from
//      d.fiiDiiSentiment.{fii,dii}_index_fut_net (same fields
//      buildFiiDiiSummaryCard in panels-views.js already renders).
//   2. Gamma flip vs spot distance, via findGammaFlipStrike()
//      (chain-helpers.js) — only votes once spot clears the flip strike
//      by >= GAMMA_FLIP_MIN_DISTANCE points, else abstains. Convention:
//      spot above the flip votes bullish (dealers positioned long gamma
//      above it, short gamma below), matching the "GAMMA FLIP" alert
//      wording in chain-greeks.js's buildGreeksAlertsHtml.
//   3. Change-in-OI PCR vs total PCR expansion, from d.oiChgPCR vs
//      d.totalPCR (same fields buildChainSummaryHtml's PCR/PCR-Δ footer
//      in chain-template.js already renders) — votes only once the gap
//      exceeds OI_CHG_PCR_EXPANSION_MIN and points the same direction as
//      the absolute PCR (fresh OI genuinely extending the existing lean,
//      not just noise around 1.0).
//   4. Smart-money lean (OI dominance + writing/buying signal + GEX),
//      aggregating
//      buildInstitutionalView()'s per-strike score (chain-view-models.js)
//      across the visible chain.
//
// Plain function file (no classes) — must load after chain-helpers.js
// (findGammaFlipStrike/getFilteredChain/activeAtm) and chain-view-
// models.js (buildInstitutionalView), and before chain-renderer.js/
// chain-template.js, whose render calls use it. See DashboardPro.html's
// script order and build.mjs's page.js list.
// ============================================================

const GAMMA_FLIP_MIN_DISTANCE = 15;   // points spot must clear the flip by to vote
const OI_CHG_PCR_EXPANSION_MIN = 0.05; // minimum oiChgPCR-vs-totalPCR gap to count as "expansion"

function _convictionPillarFiiDii(d) {
  const s = d.fiiDiiSentiment || {};
  const net = (s.fii_index_fut_net || 0) + (s.dii_index_fut_net || 0);
  if (!s.source_date || net === 0) {
    return { vote: 0, label: 'FII/DII Flow', detail: 'Awaiting EOD feed' };
  }
  const vote = net > 0 ? 1 : -1;
  return {
    vote,
    label: 'FII/DII Flow',
    detail: `${net > 0 ? '+' : ''}${Math.round(net)} Cr combined net`,
  };
}

// IA redesign step 2: label carries an explicit "(Regime Vote)" scope
// tag, same treatment as _convictionPillarSmartMoneyLean's "(Aggregate
// Vote)" above — this pillar reduces the visible-range gamma-flip
// strike (same one Greeks Alerts flags, see chain-greeks.js) down to a
// single +1/0/-1 vote based on which side of it spot currently sits, so
// it reads as distinct from that card's raw alert and from the
// Simulator's scenario-adjusted GEX chart (dashboard-redesign-
// proposal.md §1's "Same story for Gamma/GEX" fragmentation note).
// CORRECTION (step 6 audit): this comment previously said "live,
// whole-chain gamma-flip strike" — the `greeks` param passed in here is
// visible-range (see computeConvictionGauge above), same scope as
// Greeks Alerts itself since its own step-6 fix, not whole-chain.
function _convictionPillarGammaFlip(d, greeks, atm) {
  const LABEL = 'Gamma Flip (Regime Vote)';
  // computeGammaFlip (metrics.js, IA redesign step 6)
  const flip = computeGammaFlip(greeks, atm);
  const spot = d.spot;
  if (!flip || spot == null) {
    return { vote: 0, label: LABEL, detail: 'No flip in visible range' };
  }
  const dist = spot - flip.strike;
  if (Math.abs(dist) < GAMMA_FLIP_MIN_DISTANCE) {
    return {
      vote: 0,
      label: LABEL,
      detail: `Spot within ${GAMMA_FLIP_MIN_DISTANCE}pt of flip (${Math.round(flip.strike)})`,
    };
  }
  const vote = dist > 0 ? 1 : -1;
  return {
    vote,
    label: LABEL,
    detail: `Spot ${dist > 0 ? '+' : ''}${Math.round(dist)}pt vs flip ${Math.round(flip.strike)}`,
  };
}

function _convictionPillarPcrExpansion(d) {
  const totalPcr = d.totalPCR;
  const chgPcr = d.oiChgPCR;
  if (totalPcr == null || chgPcr == null) {
    return { vote: 0, label: 'PCR Expansion', detail: 'No data' };
  }
  const gap = chgPcr - totalPcr;
  if (gap > OI_CHG_PCR_EXPANSION_MIN && chgPcr > 1) {
    return {
      vote: 1,
      label: 'PCR Expansion',
      detail: `Chg-OI PCR ${chgPcr.toFixed(2)} > Total ${totalPcr.toFixed(2)}`,
    };
  }
  if (gap < -OI_CHG_PCR_EXPANSION_MIN && chgPcr < 1) {
    return {
      vote: -1,
      label: 'PCR Expansion',
      detail: `Chg-OI PCR ${chgPcr.toFixed(2)} < Total ${totalPcr.toFixed(2)}`,
    };
  }
  return {
    vote: 0,
    label: 'PCR Expansion',
    detail: `Chg-OI PCR ${chgPcr.toFixed(2)} vs Total ${totalPcr.toFixed(2)} (flat)`,
  };
}

// NOTE: labeled "Smart Money Lean", not "Block Prints" — this pillar
// averages buildInstitutionalView().score (OI dominance + call/put
// writing-vs-buying signal + GEX sign) across the whole chain. It has
// nothing to do with block-size prints; actual block-print detection
// (Vol/OI ratio spikes per strike) lives in the Institutional F&O
// Simulator's Vol/OI Velocity panel (simulator-view.js). The two used to
// share the "Block Prints" label despite measuring different things,
// which read as the same fact shown twice even though it wasn't.
// IA redesign step 2: label carries an explicit "(Aggregate Vote)" scope
// tag so this pillar reads as distinct from Institutional Activity
// Crux's "Near-ATM Ledger" and Smart Money Ranking's "Visible-Range
// Ranking" (renamed step 6 — see that file's comment) — same underlying
// per-strike score, three different aggregations, no longer
// distinguishable only by which file each lives in (dashboard-redesign-
// proposal.md §1's fragmentation table).
function _convictionPillarSmartMoneyLean(chain, greeks) {
  const LABEL = 'Smart Money Lean (Aggregate Vote)';
  if (!chain.length) {
    return { vote: 0, label: LABEL, detail: 'No data' };
  }
  let totalScore = 0;
  chain.forEach(r => {
    const g = greeks.find(x => x.strike === r.strike) || {};
    totalScore += buildInstitutionalView(r, g).score;
  });
  const avgScore = totalScore / chain.length;
  if (avgScore >= 0.5) {
    return { vote: 1, label: LABEL, detail: `Net institutional score +${avgScore.toFixed(2)}` };
  }
  if (avgScore <= -0.5) {
    return { vote: -1, label: LABEL, detail: `Net institutional score ${avgScore.toFixed(2)}` };
  }
  return { vote: 0, label: LABEL, detail: `Net institutional score ${avgScore.toFixed(2)} (flat)` };
}

// Computes the full gauge: all 4 pillar reads + the aggregate verdict.
// Exposed separately from buildConvictionGaugeHtml() so other panels
// (e.g. a future alert banner) can read the verdict without re-rendering
// the card's markup.
function computeConvictionGauge(d) {
  const atm = activeAtm(d);
  const chain = getFilteredChain(d);
  // getVisibleRangeGreeks (metrics.js, IA redesign step 6) — same
  // visible-range filter as the Greeks Alerts card / Smart Money
  // Ranking / Greeks modal, replacing this file's own independent copy
  // of the identical filter logic.
  const greeks = getVisibleRangeGreeks(d, chain);

  const pillars = [
    _convictionPillarFiiDii(d),
    _convictionPillarGammaFlip(d, greeks, atm),
    _convictionPillarPcrExpansion(d),
    _convictionPillarSmartMoneyLean(chain, greeks),
  ];

  const bullish = pillars.filter(p => p.vote > 0).length;
  const bearish = pillars.filter(p => p.vote < 0).length;

  let verdict, verdictColor, verdictCls, direction;
  if (bullish >= 3) {
    verdict = 'GO LONG'; verdictColor = 'var(--pos)'; verdictCls = 'go-long'; direction = 1;
  } else if (bearish >= 3) {
    verdict = 'GO SHORT'; verdictColor = 'var(--neg)'; verdictCls = 'go-short'; direction = -1;
  } else if (bullish === 2 && bullish > bearish) {
    verdict = 'LEAN LONG'; verdictColor = 'var(--info)'; verdictCls = 'lean-long'; direction = 1;
  } else if (bearish === 2 && bearish > bullish) {
    verdict = 'LEAN SHORT'; verdictColor = 'var(--warn)'; verdictCls = 'lean-short'; direction = -1;
  } else {
    verdict = 'WAIT'; verdictColor = 'var(--text-tertiary)'; verdictCls = 'wait'; direction = 0;
  }

  // NOTE: this gauge deliberately does NOT compute its own "confidence"
  // percentage. It used to (dominant pillar's share of all 4 votes,
  // rendered next to the Reasons header) — but that was just a second
  // percentage sitting directly under the Decision Engine's own
  // Confidence figure (renderDecisionBoxHtml, dec.confidence), and the
  // two numbers measure different things (vote alignment here vs. the
  // Decision Engine's own model), so showing both as bare "%" invited
  // reading them as the same fact restated. Confidence has exactly one
  // home — the Decision Engine panel above this one. This gauge's own
  // job is the bullish/bearish/abstain breakdown, which the cg-footer
  // line already states in plain words ("3 bullish · 1 abstain — needs
  // 3 of 4 aligned to fire").

  // Reasons — the subset of pillars agreeing with the verdict's direction,
  // rendered as a checklist so the verdict is traceable to specific,
  // named signals (Explainability Panel). WAIT has no direction, so all
  // non-abstaining pillars are listed as the (conflicting) reasons for
  // not firing.
  const reasons = direction === 0
    ? pillars.filter(p => p.vote !== 0)
    : pillars.filter(p => p.vote === direction);

  return { pillars, bullish, bearish, verdict, verdictColor, verdictCls, direction, reasons };
}

// Builds the always-visible gauge card. Follows the same self-contained-
// render-function convention as buildChainSummaryHtml/buildFiiDiiSummaryCard
// (panels-views.js) — own #conviction-gauge-card wrapper, safe to diff/swap
// via outerHTML on every tick (see _rerenderChainPanels in chain-renderer.js).
function buildConvictionGaugeHtml(d) {
  const gauge = computeConvictionGauge(d);

  const pillarChips = gauge.pillars.map(p => {
    const cls = p.vote > 0 ? 'cg-bull' : p.vote < 0 ? 'cg-bear' : 'cg-abstain';
    const icon = p.vote > 0 ? '▲' : p.vote < 0 ? '▼' : '·';
    return `
      <div class="cg-pillar ${cls}">
        <div class="cg-pillar-icon">${icon}</div>
        <div class="cg-pillar-body">
          <div class="cg-pillar-label">${p.label}</div>
          <div class="cg-pillar-detail">${p.detail}</div>
        </div>
      </div>`;
  }).join('');

  const abstain = gauge.pillars.length - gauge.bullish - gauge.bearish;

  const reasonRows = gauge.reasons.map(p => `
      <div class="cg-reason">
        <span class="cg-reason-check">✓</span>
        <span class="cg-reason-label">${p.label}</span>
        <span class="cg-reason-detail">${p.detail}</span>
      </div>`).join('');

  const reasonsBlock = gauge.reasons.length ? `
    <div class="cg-explain">
      <div class="cg-explain-header">
        <span class="cg-explain-title">${gauge.direction === 0 ? 'Conflicting reads' : 'Reasons'}</span>
      </div>
      ${reasonRows}
    </div>` : '';

  return `
  <div class="section-card sc-neutral" id="conviction-gauge-card">
    <div class="section-header">
      <span class="section-title"><span class="section-icon">🎯</span>Conviction Multiplier</span>
      <span class="cg-verdict ${gauge.verdictCls}" style="color:${gauge.verdictColor};">${gauge.verdict}</span>
    </div>
    <div class="cg-pillars-grid">${pillarChips}</div>
    ${reasonsBlock}
    <div class="cg-footer">${gauge.bullish} bullish · ${gauge.bearish} bearish · ${abstain} abstain — needs 3 of 4 aligned to fire</div>
  </div>`;
}