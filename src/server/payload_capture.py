"""Real-export capture seam for the dashboard payload.

Extracted from server/app.py so the composition root no longer carries the
capture wiring inline. AnalyticsPipelineRunner reads the dashboard payload back
out of mTerminals_json's own export so the pipeline and the WS stream share
one serialization path.
"""
import json

from infrastructure.payload_capture import PayloadExportCapture
from application.dashboard import serializer as dashboard_serializer


def load_exported_dashboard_payload():
    """Read the last serialized dashboard payload back from disk."""
    with open("mTerminals.json") as exported:
        return json.load(exported)


def install_payload_export_capture():
    """Build the capture adapter around the canonical exporter.

    Returns the PayloadExportCapture instance so callers (e.g.
    server/app.py's AnalyticsPipelineRunner) can reach .clear / .payload.
    """
    capture = PayloadExportCapture(
        exporter=dashboard_serializer.export_dashboard_json,
        fallback_loader=load_exported_dashboard_payload,
        export_overrides={"out_path": "mTerminals.json"},
    )
    return capture
