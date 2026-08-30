from server.websocket_payload import compute_diff


def test_keyed_delta_contains_only_changed_row_fields():
    old = {
        "chain": [
            {"strike": 24000, "ceLTP": 100.0, "ceOI": 500, "ceGamma": 0.002},
            {"strike": 24100, "ceLTP": 60.0, "ceOI": 300, "ceGamma": 0.001},
        ]
    }
    new = {
        "chain": [
            {"strike": 24000, "ceLTP": 101.5, "ceOI": 500, "ceGamma": 0.002},
            {"strike": 24100, "ceLTP": 60.0, "ceOI": 300, "ceGamma": 0.001},
        ]
    }

    delta = compute_diff(old, new)

    assert delta == {
        "chain": {
            "_keyed": True,
            "_key_field": "strike",
            "changed": [{"strike": 24000, "ceLTP": 101.5}],
        }
    }


def test_keyed_delta_preserves_row_field_and_row_removals():
    old = {
        "chain": [
            {"strike": 24000, "ceLTP": 100.0, "temporary": True},
            {"strike": 24100, "ceLTP": 60.0},
        ]
    }
    new = {"chain": [{"strike": 24000, "ceLTP": 100.0}]}

    delta = compute_diff(old, new)

    assert delta["chain"]["changed"] == [
        {"strike": 24000, "_removed": ["temporary"]}
    ]
    assert delta["chain"]["_removed_keys"] == [24100]
