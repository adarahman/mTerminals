import math

from application.payload_builders.common import (
    compact_number,
    formatted_number,
    integer,
    nullable_rounded_number,
    rounded_number,
    safe_string,
)


def test_common_payload_formatters_preserve_dashboard_contract():
    assert compact_number(12_500_000) == "12.50M"
    assert compact_number(12_500) == "12.5K"
    assert formatted_number(1234.5, 1) == "1,234.5"
    assert rounded_number(1.23456, 2) == 1.23
    assert rounded_number(math.nan) == 0.0
    assert nullable_rounded_number(None) is None
    assert nullable_rounded_number(math.inf) is None
    assert integer("12.9") == 12
    assert safe_string(" ") == "—"
