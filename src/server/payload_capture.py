"""Real-export capture seam for the dashboard payload.

Extracted from server/app.py so the composition root no longer carries the
capture wiring inline. run_pipeline_once() reads the dashboard payload back
out of mTerminals_json's own export so the pipeline and the WS stream share
one serialization path.
"""
import json

from infrastructure.payload_capture import PayloadExportCapture
import application.dashboard_serializer as dashboard_serializer


def load_exported_dashboard_payload():
    """Read the last serialized dashboard payload back from disk."""
    with open("mTerminals.json") as exported:
        return json.load(exported)


def install_payload_export_capture():
    """Build the capture and monkey-patch the live export entry point.

    Returns the PayloadExportCapture instance so callers (e.g.
    server/app.py's run_pipeline_once) can reach .clear / .payload.
    """
    capture = PayloadExportCapture(
        exporter=dashboard_serializer.export_dashboard_json,
        fallback_loader=load_exported_dashboard_payload,
        export_overrides={"out_path": "mTerminals.json"},
    )
    dashboard_serializer.export_dashboard_json = capture.export
    return capture
