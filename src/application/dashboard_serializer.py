"""Backward-compatible re-export of the dashboard serializer.

The implementation now lives in :mod:`application.dashboard.serializer`. This
shim keeps ``server/app.py`` (which does ``from application import
dashboard_serializer`` and monkeypatches ``export_dashboard_json``) and the
existing name-resolution test working until every importer is repointed at the
package.
"""
from application.dashboard import serializer as _serializer

# Re-export every public/private name so this module's namespace is identical
# to the real one — legacy importers and test_export_dashboard_referenced_globals
# both rely on the original attributes being present here.
for _name in dir(_serializer):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_serializer, _name)

del _name, _serializer
