import math

import pandas as pd
import pytest

from application.payload_builders.common import nullable_rounded_number
from decision.signal_builder import verdict_pcr
from decision.types import DecisionResult
from oi.chain_metrics import compute_oi_change_pcr


@pytest.mark.parametrize("calls", [0, -100, float("nan")])
def test_unavailable_denominator_does_not_create_inflated_ratio(calls):
    ratio = compute_oi_change_pcr(pd.DataFrame({
        "CE_ChgOI": [calls], "PE_ChgOI": [447605],
    }))
    assert math.isnan(ratio)
    assert nullable_rounded_number(ratio, 2) is None
    result = DecisionResult()
    verdict_pcr(1.2, ratio, result)
    assert not result.active_signals


@pytest.mark.parametrize("puts, expected", [(150, 1.5), (0, 0), (-50, -0.5)])
def test_valid_ratio_preserves_put_change(puts, expected):
    assert compute_oi_change_pcr(pd.DataFrame({
        "CE_ChgOI": [40, 60], "PE_ChgOI": [puts, 0],
    })) == expected
