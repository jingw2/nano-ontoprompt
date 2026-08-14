"""Pinned OntoPrompt serializer (P3B-SAVER, Section 6.1).

Tagged canonical JSON for primitives, UUID, decimal, UTC datetime, bytes,
tuples and registered state dataclasses.  Unknown Python objects, pickle,
executable types, credentials, provider clients and ORM objects fail with
`UNSERIALIZABLE_CHECKPOINT_VALUE`.  Format/version is stored on every row.
"""
from __future__ import annotations

import base64
import dataclasses
import datetime as _dt
import decimal
import json
import uuid
from typing import Any

SERIALIZER_VERSION = "ontoprompt-tagged-json-v1"

_TAG_STR = "s"
_TAG_INT = "i"
_TAG_FLOAT = "f"
_TAG_BOOL = "b"
_TAG_NULL = "n"
_TAG_UUID = "u"
_TAG_DECIMAL = "d"
_TAG_DATETIME = "t"
_TAG_BYTES = "y"
_TAG_TUPLE = "p"
_TAG_LIST = "l"
_TAG_DICT = "o"
_TAG_DATA = "dc"  # registered dataclass


class UnserializableCheckpointValue(Exception):
    """The value cannot be persisted in a checkpoint (fail closed)."""


def _is_registered_dataclass(value: Any) -> bool:
    return dataclasses.is_dataclass(value) and not isinstance(value, type) \
        and type(value).__module__.startswith(("app.runtime", "app.services"))


def _tag(value: Any) -> dict:
    if value is None:
        return {"t": _TAG_NULL}
    if isinstance(value, bool):
        return {"t": _TAG_BOOL, "v": value}
    if isinstance(value, str):
        return {"t": _TAG_STR, "v": value}
    if isinstance(value, int):
        return {"t": _TAG_INT, "v": value}
    if isinstance(value, float):
        return {"t": _TAG_FLOAT, "v": value}
    if isinstance(value, uuid.UUID):
        return {"t": _TAG_UUID, "v": str(value)}
    if isinstance(value, decimal.Decimal):
        return {"t": _TAG_DECIMAL, "v": str(value)}
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            raise UnserializableCheckpointValue("naive datetime unsupported")
        return {"t": _TAG_DATETIME, "v": value.astimezone(_dt.timezone.utc).isoformat()}
    if isinstance(value, (bytes, bytearray)):
        return {"t": _TAG_BYTES, "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, tuple):
        return {"t": _TAG_TUPLE, "v": [_tag(item) for item in value]}
    if isinstance(value, list):
        return {"t": _TAG_LIST, "v": [_tag(item) for item in value]}
    if isinstance(value, dict):
        return {"t": _TAG_DICT, "v": {str(k): _tag(v) for k, v in value.items()}}
    if _is_registered_dataclass(value):
        fields = {f.name: _tag(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"t": _TAG_DATA, "n": type(value).__name__, "v": fields}
    raise UnserializableCheckpointValue(
        f"UNSERIALIZABLE_CHECKPOINT_VALUE:{type(value).__module__}.{type(value).__name__}"
    )


def _untag(node: Any) -> Any:
    if not isinstance(node, dict) or "t" not in node:
        raise UnserializableCheckpointValue("malformed tagged value")
    tag = node["t"]
    value = node.get("v")
    if tag == _TAG_NULL:
        return None
    if tag == _TAG_BOOL:
        return bool(value)
    if tag == _TAG_STR:
        return str(value)
    if tag == _TAG_INT:
        return int(value)
    if tag == _TAG_FLOAT:
        return float(value)
    if tag == _TAG_UUID:
        return uuid.UUID(value)
    if tag == _TAG_DECIMAL:
        return decimal.Decimal(value)
    if tag == _TAG_DATETIME:
        return _dt.datetime.fromisoformat(value)
    if tag == _TAG_BYTES:
        return base64.b64decode(value)
    if tag == _TAG_TUPLE:
        return tuple(_untag(item) for item in value)
    if tag == _TAG_LIST:
        return [_untag(item) for item in value]
    if tag == _TAG_DICT:
        return {k: _untag(v) for k, v in value.items()}
    if tag == _TAG_DATA:
        return {"__dataclass__": node["n"], **{k: _untag(v) for k, v in value.items()}}
    raise UnserializableCheckpointValue(f"unknown tag {tag}")


def dumps(value: Any) -> bytes:
    """Serialize to canonical tagged JSON bytes (sort_keys for determinism)."""
    tagged = _tag(value)
    return json.dumps(tagged, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def loads(data: bytes) -> Any:
    """Deserialize tagged JSON bytes back to the value graph."""
    node = json.loads(data.decode("utf-8"))
    return _untag(node)


def value_hash(value: Any) -> str:
    import hashlib
    return hashlib.sha256(dumps(value)).hexdigest()
