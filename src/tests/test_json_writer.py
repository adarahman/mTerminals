import json

from infrastructure.json_writer import write_json


def test_json_writer_persists_payload_with_custom_default(tmp_path):
    output = tmp_path / "dashboard.json"

    write_json(
        str(output),
        {"values": {1, 2}},
        default=lambda value: sorted(value) if isinstance(value, set) else value,
    )

    assert json.loads(output.read_text()) == {"values": [1, 2]}
