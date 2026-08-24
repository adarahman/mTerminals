"""Compatibility imports for the former root-level paths module.

New code must import from :mod:`infrastructure.paths`.
"""

from infrastructure.paths import BACKEND_DIR, CACHE_DIR, PROJECT_ROOT, RUNTIME_DIR

__all__ = ["BACKEND_DIR", "PROJECT_ROOT", "RUNTIME_DIR", "CACHE_DIR"]
