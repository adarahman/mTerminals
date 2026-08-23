"""Pure WebSocket payload transformations.

Kept independent of aiohttp and server globals so delta semantics can be
tested without booting the live server.
"""
from __future__ import annotations

import numpy as np


def json_default(obj):
    """Convert NumPy scalar and array values for ``orjson``."""
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Type is not JSON serializable: {type(obj)}")


def compute_diff(old, new, key_field="strike"):
    """Recursively return only values in ``new`` that differ from ``old``."""
    if old == new:
        return None
    if isinstance(new, dict) and isinstance(old, dict):
        out = {}
        for key, value in new.items():
            if key not in old:
                out[key] = value
            else:
                diff = compute_diff(old[key], value, key_field)
                if diff is not None:
                    out[key] = diff
        removed = [key for key in old if key not in new]
        if removed:
            out["_removed"] = removed
        return out or None
    if isinstance(new, list) and isinstance(old, list):
        if new and isinstance(new[0], dict) and key_field in new[0]:
            old_by_key = {
                row.get(key_field): row for row in old if isinstance(row, dict)
            }
            changed = []
            for row in new:
                key = row.get(key_field)
                old_row = old_by_key.get(key)
                if old_row is None:
                    changed.append(row)
                elif old_row != row:
                    row_diff = compute_diff(old_row, row, key_field)
                    changed.append(
                        {key_field: key, **row_diff}
                        if isinstance(row_diff, dict)
                        else row
                    )
            removed_keys = [key for key in old_by_key if key not in {row.get(key_field) for row in new}]
            if not changed and not removed_keys:
                return None
            result = {"_keyed": True, "_key_field": key_field, "changed": changed}
            if removed_keys:
                result["_removed_keys"] = removed_keys
            return result
        return new
    return new
