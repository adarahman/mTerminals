"""Shared normalization helpers for dashboard payload builders."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def compact_number(value) -> str:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return "--"
    absolute = abs(parsed)
    if absolute >= 1_000_000:
        return f"{parsed / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"{parsed / 1_000:.1f}K"
    return f"{parsed:.0f}"


def formatted_number(value, decimals=0) -> str:
    try:
        return f"{float(value):,.{decimals}f}"
    except (TypeError, ValueError):
        return "--"


def rounded_number(value, decimals=4) -> float:
    if isinstance(value, (np.generic, pd.Series)):
        value = value.item()
    elif hasattr(value, "iloc"):
        value = value.iloc[0]
    if isinstance(value, (float, np.floating)):
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return round(float(value), decimals)
    try:
        return float(value)
    except Exception:
        return 0.0


def nullable_rounded_number(value, decimals=4) -> float | None:
    try:
        if isinstance(value, (np.generic, pd.Series)):
            value = value.item()
        elif hasattr(value, "iloc"):
            value = value.iloc[0]
        if value is None or pd.isna(value):
            return None
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
        return round(parsed, decimals)
    except Exception:
        return None


def integer(value) -> int:
    try:
        if isinstance(value, (np.generic, pd.Series)):
            value = value.item()
        return int(float(value))
    except Exception:
        return 0


def safe_string(value, default="—") -> str:
    if value is None:
        return default
    normalized = str(value).strip()
    return normalized if normalized else default
