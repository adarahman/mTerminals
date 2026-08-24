from infrastructure.payload_capture import PayloadExportCapture


def test_capture_retains_exporter_result_and_applies_overrides():
    calls = []

    def exporter(value, **kwargs):
        calls.append((value, kwargs))
        return {"value": value}

    capture = PayloadExportCapture(
        exporter=exporter,
        export_overrides={"out_path": "canonical.json"},
    )

    result = capture.export(42, out_path="ignored.json")

    assert result == {"value": 42}
    assert capture.payload == result
    assert calls == [(42, {"out_path": "canonical.json"})]
    capture.clear()
    assert capture.payload is None


def test_capture_loads_fallback_when_exporter_returns_none():
    capture = PayloadExportCapture(
        exporter=lambda: None,
        fallback_loader=lambda: {"symbol": "NIFTY"},
    )

    assert capture.export() == {"symbol": "NIFTY"}
    assert capture.payload == {"symbol": "NIFTY"}


def test_capture_contains_fallback_loader_failure():
    def fail():
        raise ValueError("invalid export")

    capture = PayloadExportCapture(exporter=lambda: None, fallback_loader=fail)

    assert capture.export() is None
    assert capture.payload is None
