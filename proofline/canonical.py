"""Deterministic JSON and content-addressing helpers.

All protocol digests are over UTF-8 bytes of compact, recursively sorted JSON.
Mutable presentation forms, whitespace, and dictionary insertion order are not
part of a digest.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Iterable

from .errors import SchemaError

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    """Serialize JSON with one stable representation."""

    try:
        _validate_json_value(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise SchemaError(f"value is not canonical JSON: {exc}") from exc


def _validate_json_value(value: Any, *, path: str = "$", _seen: set[int] | None = None) -> None:
    """Reject values that Python's encoder would silently coerce.

    Canonical hashes must describe one JSON value, not Python's permissive
    superset.  In particular, non-string object keys, non-finite numbers, and
    cyclic containers are rejected before encoding.
    """

    if _seen is None:
        _seen = set()
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"non-finite number at {path}")
        return
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in _seen:
            raise ValueError(f"cyclic value at {path}")
        _seen.add(marker)
        try:
            for index, item in enumerate(value):
                _validate_json_value(item, path=f"{path}[{index}]", _seen=_seen)
        finally:
            _seen.remove(marker)
        return
    if isinstance(value, dict):
        marker = id(value)
        if marker in _seen:
            raise ValueError(f"cyclic value at {path}")
        _seen.add(marker)
        try:
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError(f"object key at {path} is not a string")
                _validate_json_value(item, path=f"{path}.{key}", _seen=_seen)
        finally:
            _seen.remove(marker)
        return
    raise TypeError(f"unsupported JSON value at {path}: {type(value).__name__}")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def strict_json_loads(value: str | bytes) -> Any:
    """Parse JSON without duplicate keys or non-standard numeric constants."""

    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SchemaError(f"value is not UTF-8 JSON: {exc}") from exc
    if not isinstance(value, str):
        raise SchemaError("JSON input must be text or UTF-8 bytes")
    try:
        parsed = json.loads(
            value,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
        _validate_json_value(parsed)
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SchemaError(f"value is not strict JSON: {exc}") from exc


def sha256_bytes(value: bytes) -> str:
    """Return a namespaced SHA-256 content digest."""

    return "sha256:" + hashlib.sha256(value).hexdigest()


def digest_json(value: Any) -> str:
    """Hash the canonical JSON representation of *value*."""

    return sha256_bytes(canonical_json(value).encode("utf-8"))


def is_digest(value: Any) -> bool:
    return isinstance(value, str) and bool(DIGEST_RE.fullmatch(value))


def require_digest(value: Any, field: str = "digest") -> str:
    if not is_digest(value):
        raise SchemaError(f"{field} must be sha256:<64 lowercase hex chars>")
    return value


def strip_digest_field(value: dict[str, Any], field: str) -> dict[str, Any]:
    """Copy a mapping without its self-referential digest field."""

    result = dict(value)
    result.pop(field, None)
    return result
