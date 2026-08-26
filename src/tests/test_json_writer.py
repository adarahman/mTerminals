import json

from infrastructure.json_writer import encode_json, write_json


def test_encode_json_has_no_file_side_effect(tmp_path):
    target = tmp_path / "payload.json"

    encoded = encode_json({"value": 7}, default=str)

    assert not target.exists()
    assert json.loads(encoded) == {"value": 7}


def test_write_json_persists_encoded_payload_once(tmp_path):
    target = tmp_path / "payload.json"

    write_json(str(target), {"value": 7}, default=str)

    assert json.loads(target.read_text()) == {"value": 7}


def test_json_writer_persists_payload_with_custom_default(tmp_path):
    output = tmp_path / "dashboard.json"

    write_json(
        str(output),
        {"values": {1, 2}},
        default=lambda value: sorted(value) if isinstance(value, set) else value,
    )

    assert json.loads(output.read_text()) == {"values": [1, 2]}
