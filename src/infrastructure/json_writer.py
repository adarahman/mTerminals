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
    if _orjson is not None:
        try:
            with open(out_path, "wb") as output:
                output.write(
                    _orjson.dumps(
                        payload,
                        default=default,
                        option=_orjson.OPT_NON_STR_KEYS
                        | getattr(_orjson, "OPT_SERIALIZE_NUMPY", 0),
                    )
                )
            return
        except TypeError as exc:
            logger.warning(
                "orjson dump failed (%s); falling back to stdlib json",
                exc,
            )
    with open(out_path, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, default=default)
