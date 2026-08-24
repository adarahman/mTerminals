import builtins
import inspect
import symtable

import application.dashboard_serializer as serializer


def test_export_dashboard_referenced_globals_are_resolved():
    """Catch imports removed while names remain in the live export function."""
    source = inspect.getsource(serializer.export_dashboard_json)
    function_table = symtable.symtable(source, "dashboard_serializer", "exec").get_children()[0]
    unresolved = {
        symbol.get_name()
        for symbol in function_table.get_symbols()
        if symbol.is_referenced()
        and symbol.is_global()
        and symbol.get_name() not in serializer.__dict__
        and not hasattr(builtins, symbol.get_name())
    }
    assert unresolved == set()
