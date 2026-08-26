"""JSON file persistence with a fast encoder and safe fallback."""
from __future__ import annotations

from collections.abc import Callable
import json
import logging
from typing import Any


logger = logging.getLogger(__name__)

try:
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None


def write_json(
    out_path: str,
    payload: dict,
    *,
    default: Callable[[Any], Any],
) -> None:
    encoded = encode_json(payload, default=default)
    if isinstance(encoded, bytes):
        with open(out_path, "wb") as output:
            output.write(encoded)
    else:
        with open(out_path, "w", encoding="utf-8") as output:
            output.write(encoded)


def encode_json(
    payload: dict,
    *,
    default: Callable[[Any], Any],
) -> bytes | str:
    """Encode a payload without performing file I/O."""
    if _orjson is not None:
        try:
            return _orjson.dumps(
                payload,
                default=default,
                option=_orjson.OPT_NON_STR_KEYS
                | getattr(_orjson, "OPT_SERIALIZE_NUMPY", 0),
            )
        except TypeError as exc:
            logger.warning(
                "orjson dump failed (%s); falling back to stdlib json",
                exc,
            )
    return json.dumps(payload, ensure_ascii=False, default=default)
