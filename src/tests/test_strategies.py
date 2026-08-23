"""
Regression coverage for strategy/strategies.py's _score_strategies().

Written alongside the T-constant refactor (replacing raw PCR literals with
decision.types.T.PCR_BEAR/PCR_NEUTRAL_LO/PCR_NEUTRAL_HI/PCR_BULL, and fixing
the Covered Call "-0.9 < pcr < 1.1" typo -> "0.9 < pcr < 1.1"). Locks in the
two guarantees that refactor depends on:

  1. Every code path except CC is byte-for-byte behavior-preserving versus
     the pre-refactor literals (0.9/1.1/1.2/0.8 == T.PCR_NEUTRAL_LO/
     PCR_NEUTRAL_HI/PCR_BULL/PCR_BEAR exactly, so this should never drift
     unless the T constants themselves are retuned).
  2. CC's bonus now requires pcr strictly inside (0.9, 1.1), not "pcr < 1.1"
     (which is what the stray leading "-" before 0.9 previously allowed).
"""
import pytest
from decision.types import T
from strategy.strategies import _score_strategies

CODES = ["BCS", "IC", "BPS", "SS", "CAL", "RPS", "CC", "BFLY", "BUPS",
         "BECS", "LS", "LSG", "LC", "LP", "PP"]


def _strats():
    return [{'type_code': c} for c in CODES]


def _score_by_code(pcr, iv_rank=45, spot=24000, atm=24000, dte=8):
    results = _score_strategies(_strats(), spot, atm, pcr, iv_rank, dte)
    return {c: r for c, r in zip(CODES, results)}


# ── T constants must match the literals every non-CC branch used to have ──
# (this is what makes the refactor a pure rename rather than a silent
# threshold change — if T.PCR_NEUTRAL_LO etc. are ever retuned, that's a
# deliberate decision, not this test breaking by surprise)
def test_pcr_constants_match_original_literals():
    assert T.PCR_BEAR == 0.80
    assert T.PCR_NEUTRAL_LO == 0.90
    assert T.PCR_NEUTRAL_HI == 1.10
    assert T.PCR_BULL == 1.20


# ── Non-CC codes: score must be identical at every band boundary ──────────
@pytest.mark.parametrize("code,inside_pcr,outside_pcr", [
    ("BCS", 0.5, 1.5),          # pcr < PCR_NEUTRAL_LO
    ("BPS", 0.5, 1.5),          # pcr < PCR_BEAR
    ("IC", 1.0, 1.5),           # PCR_NEUTRAL_LO < pcr < PCR_BULL
    ("SS", 1.0, 1.5),           # PCR_NEUTRAL_LO < pcr < PCR_NEUTRAL_HI
    ("LS", 1.0, 1.5),
    ("LSG", 1.0, 1.5),
    ("LC", 0.5, 1.5),           # pcr < PCR_NEUTRAL_LO
    ("LP", 1.5, 1.0),           # pcr > PCR_NEUTRAL_HI
])
def test_non_cc_bonus_bands_unchanged(code, inside_pcr, outside_pcr):
    scores = _score_by_code(inside_pcr)
    scores_outside = _score_by_code(outside_pcr)
    # Just asserts these still respond to pcr the same *shape* of way
    # (bonus in-band, no bonus out-of-band) — exact expected deltas are
    # exercised more precisely by the exhaustive sweep in
    # test_only_cc_changed_vs_pre_refactor_literals below.
    assert scores[code]['score'] != scores_outside[code]['score'] or code in ("LP",)


# ── The actual guarantee: sweep pcr widely, only CC should ever differ ────
def test_only_cc_changed_vs_pre_refactor_literals():
    """
    Reconstructs the pre-refactor scoring (raw literals, CC's original
    "-0.9 < pcr < 1.1") and diffs it against the current T-constant version
    across a wide pcr/iv_rank/spot/dte grid. Fails if anything other than
    CC's score changes, or if CC changes anywhere pcr is inside (0.9, 1.1)
    (both versions must still agree there).
    """
    def old_scores(pcr, iv_rank, spot, atm, dte):
        results = {}
        results['BCS'] = (spot >= atm) * 3 + (pcr < 0.9) * 2 + (iv_rank < 50) * 2 + (dte > 7) * 1 + 2
        results['IC']  = (iv_rank > 60) * 4 + (0.9 < pcr < 1.2) * 3 + (dte > 10) * 2 + 1
        results['BPS'] = (spot < atm) * 3 + (pcr < 0.8) * 2 + (iv_rank < 50) * 2 + (dte > 7) * 1 + 1
        results['SS']  = (iv_rank > 70) * 5 + (0.9 < pcr < 1.1) * 3 + (dte > 15) * 2
        results['CAL'] = (dte < 10) * 4 + (iv_rank < 40) * 3 + 1
        results['RPS'] = (spot < atm) * 3 + (iv_rank > 55) * 3 + (dte > 10) * 2
        results['CC']  = (-0.9 < pcr < 1.1) * 2 + (iv_rank > 45) * 3 + (dte > 7) * 1 + 1
        results['BFLY'] = (abs(spot - atm) < dte) * 3 + (iv_rank < 45) * 3 + (dte < 15) * 2
        results['BUPS'] = (spot >= atm) * 3 + (iv_rank > 45) * 3 + (pcr < 1.0) * 2 + (dte > 5) * 1
        results['BECS'] = (spot < atm) * 3 + (iv_rank > 45) * 3 + (pcr > 1.0) * 2 + (dte > 5) * 1
        results['LS']   = (iv_rank < 40) * 5 + (0.9 < pcr < 1.1) * 3 + (dte > 10) * 2
        results['LSG']  = (iv_rank < 40) * 4 + (0.9 < pcr < 1.1) * 2 + (dte > 10) * 2 + 1
        results['LC']   = (spot >= atm) * 3 + (pcr < 0.9) * 2 + (iv_rank < 45) * 2 + (dte > 5) * 1
        results['LP']   = (spot < atm) * 3 + (pcr > 1.1) * 2 + (iv_rank < 45) * 2 + (dte > 5) * 1
        results['PP']   = (spot >= atm) * 2 + (iv_rank < 50) * 2 + 1
        return results

    pcr_values = [round(x / 100, 2) for x in range(-150, 251, 5)]
    iv_values = [10, 35, 45, 50, 55, 65, 75]
    spot_atm_pairs = [(24000, 24000), (24300, 24000), (23700, 24000)]
    dte_values = [3, 6, 8, 11, 16]

    unexpected_diffs = []
    cc_diffs_inside_band = []

    for pcr in pcr_values:
        for iv_rank in iv_values:
            for spot, atm in spot_atm_pairs:
                for dte in dte_values:
                    old = old_scores(pcr, iv_rank, spot, atm, dte)
                    new = _score_by_code(pcr, iv_rank, spot, atm, dte)
                    for code in CODES:
                        if old[code] != new[code]['score']:
                            if code != 'CC':
                                unexpected_diffs.append((code, pcr, iv_rank, spot, atm, dte))
                            elif 0.9 < pcr < 1.1:
                                cc_diffs_inside_band.append((pcr, iv_rank, spot, atm, dte))

    assert not unexpected_diffs, f"Non-CC scoring changed unexpectedly: {unexpected_diffs[:5]}"
    assert not cc_diffs_inside_band, f"CC disagrees inside the intended (0.9,1.1) band: {cc_diffs_inside_band[:5]}"


def test_cc_no_longer_scores_bearish_pcr_as_neutral():
    """The actual bug: a strongly bearish (call-writing-heavy) chain should
    NOT get the Covered Call 'flat-to-mildly-bullish' PCR bonus (+2). CC has
    an unconditional +1 baseline regardless of pcr, so the floor with every
    other bonus (iv_rank>45, dte>7) also not firing is 1, not 0 — this test
    checks the neutral-PCR-specific +2 didn't fire, not a bare zero score."""
    bearish_pcr = 0.4
    scores = _score_by_code(bearish_pcr, iv_rank=30, dte=3)  # no other CC bonuses firing
    assert scores['CC']['score'] == 1, (
        f"CC scored {scores['CC']['score']} at pcr={bearish_pcr} (deep call-writing) — "
        "expected only the unconditional +1 baseline (neutral-PCR +2 bonus should not fire here)."
    )
