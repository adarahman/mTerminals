"""Compatibility imports for the former root-level configuration module.

New code must import from :mod:`infrastructure.config`.
"""

from infrastructure.config import ENV_PATH, Settings, settings

__all__ = ["ENV_PATH", "Settings", "settings"]
