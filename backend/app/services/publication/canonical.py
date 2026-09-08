"""Closed manifest validation and byte-exact canonical JSON."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic_core import core_schema


class CanonicalizationError(ValueError):
    pass


class ReleaseIntegrityError(ValueError):
    pass


def _validate_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, Decimal, str, datetime)):
        canonical_json(value)
        return value
    if isinstance(value, list):
        return [_validate_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _validate_json_value(item) for key, item in value.items()}
    raise CanonicalizationError(f"unsupported JSON value: {type(value).__name__}")


class JsonValue:
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> core_schema.CoreSchema:
        validator = core_schema.no_info_plain_validator_function(
            _validate_json_value,
            serialization=core_schema.plain_serializer_function_ser_schema(lambda value: value),
        )
        return core_schema.json_or_python_schema(
            json_schema=core_schema.any_schema(),
            python_schema=validator,
        )


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class OntologyIdentity(ClosedModel):
    id: str
    name: str
    security_domain_id: str
    description: str | None
    build_mode: str


class ReleaseIdentity(ClosedModel):
    version_no: int
    version: str

    @field_validator("version_no")
    @classmethod
    def positive_version(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("version_no must be positive")
        return value


class PropertyDefinition(ClosedModel):
    id: str
    name: str
    type: str
    required: bool
    default: JsonValue
    constraints: dict[str, JsonValue]
    sensitivity: str


class EntityDefinition(ClosedModel):
    id: str
    name: str
    type: str
    description: str | None
    property_definitions: list[PropertyDefinition]


class RelationDefinition(ClosedModel):
    id: str
    name: str
    source_entity_id: str
    target_entity_id: str
    cardinality: str
    direction: str
    properties: list[PropertyDefinition]


class LogicRuleDefinition(ClosedModel):
    id: str
    fully_qualified_label: str
    version: int
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    expression: JsonValue
    effect_classification: str
    enabled: bool


class StateMachineDefinition(ClosedModel):
    id: str
    label: str
    version: int
    states: list[JsonValue]
    transitions: list[JsonValue]
    guards: list[JsonValue]
    effects: list[JsonValue]
    enabled: bool


class ActionDefinition(ClosedModel):
    id: str
    fully_qualified_label: str
    version: int
    parameter_schema: dict[str, JsonValue]
    result_schema: dict[str, JsonValue]
    declared_instance_effects: list[JsonValue]
    risk: str
    approval_policy: JsonValue
    enabled: bool


class ToolDescriptor(ClosedModel):
    descriptor_id: str
    version: int
    source_kind: str
    source_id: str
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    capability: str
    timeout_ms: int
    result_limit: int
    descriptor_hash: str

    @field_validator("descriptor_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("descriptor_hash must be lowercase SHA-256 hexadecimal")
        return value


class Manifest(ClosedModel):
    manifest_version: Literal["ontology-manifest-v1"]
    compiler_version: Literal["ontology-compiler-v1"]
    policy_compiler_version: Literal["restricted-policy-dsl-v1"]
    aggregate_tool_schema_hash: str
    ontology: OntologyIdentity
    release: ReleaseIdentity
    entities: list[EntityDefinition]
    relations: list[RelationDefinition]
    logic_rules: list[LogicRuleDefinition]
    state_machines: list[StateMachineDefinition]
    actions: list[ActionDefinition]
    tool_descriptors: list[ToolDescriptor]

    @model_validator(mode="after")
    def validate_recursive_json_domain(self) -> "Manifest":
        canonical_json(self.model_dump(mode="python"))
        return self

    @field_validator("aggregate_tool_schema_hash")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise ValueError("aggregate_tool_schema_hash must be lowercase SHA-256 hexadecimal")
        return value


class CanonicalManifest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    bytes: bytes
    projection: str
    schema_hash: bytes


def _reject_surrogates(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CanonicalizationError("unpaired UTF-16 surrogate")


def _decimal_text(value: Decimal | int) -> str:
    number = Decimal(value)
    if not number.is_finite():
        raise CanonicalizationError("numeric values must be finite")
    if number == 0:
        return "0"
    sign, digits_tuple, exponent = number.as_tuple()
    digits = list(digits_tuple)
    while digits and digits[-1] == 0:
        digits.pop()
        exponent += 1
    coefficient_digits = len(digits)
    if exponent >= 0:
        integer_digits = coefficient_digits + exponent
        scale = 0
    else:
        scale = -exponent
        integer_digits = max(coefficient_digits - scale, 0) or 1
    precision = integer_digits + scale
    if integer_digits > 20 or scale > 18 or precision > 38:
        raise CanonicalizationError("number exceeds NUMERIC(38,18)")
    plain = format(number, "f")
    negative = plain.startswith("-")
    unsigned = plain[1:] if negative else plain
    integer, separator, fraction = unsigned.partition(".")
    fraction = fraction.rstrip("0")
    rendered = integer + ((separator + fraction) if fraction else "")
    return ("-" if negative else "") + rendered


def _quote(value: str) -> str:
    _reject_surrogates(value)
    pieces = ['"']
    escapes = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    for character in value:
        if character in escapes:
            pieces.append(escapes[character])
        elif ord(character) < 0x20:
            pieces.append(f"\\u{ord(character):04x}")
        else:
            pieces.append(character)
    pieces.append('"')
    return "".join(pieces)


def _render(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float):
        raise CanonicalizationError("binary floats are forbidden")
    if isinstance(value, (int, Decimal)):
        return _decimal_text(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None or value.utcoffset().total_seconds() != 0:
            raise CanonicalizationError("datetime must be timezone-aware UTC")
        return _quote(value.strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
    if isinstance(value, list):
        return "[" + ",".join(_render(item) for item in value) + "]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise CanonicalizationError("JSON object keys must be strings")
        return "{" + ",".join(_quote(key) + ":" + _render(value[key]) for key in sorted(value)) + "}"
    raise CanonicalizationError(f"unsupported JSON value: {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    return _render(value).encode("utf-8")


def parse_json(value: str | bytes) -> Any:
    def reject_constant(constant: str) -> None:
        raise CanonicalizationError(f"non-finite JSON number: {constant}")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, item in pairs:
            if key in result:
                raise CanonicalizationError(f"duplicate JSON object key: {key}")
            result[key] = item
        return result

    parsed = json.loads(
        value,
        parse_int=Decimal,
        parse_float=Decimal,
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    canonical_json(parsed)
    return parsed


def verify_release_integrity(
    manifest_bytes: bytes,
    manifest_projection_json: str | bytes,
    schema_hash: bytes,
) -> None:
    try:
        projection_bytes = canonical_json(parse_json(manifest_projection_json))
    except (CanonicalizationError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("RELEASE_INTEGRITY_FAILURE") from exc
    if projection_bytes != manifest_bytes or hashlib.sha256(manifest_bytes).digest() != schema_hash:
        raise ReleaseIntegrityError("RELEASE_INTEGRITY_FAILURE")


def _sorted_manifest(model: Manifest) -> dict[str, Any]:
    value = model.model_dump(mode="python")
    value["entities"].sort(key=lambda item: item["id"])
    for entity in value["entities"]:
        entity["property_definitions"].sort(key=lambda item: item["id"])
    for collection in ("relations", "logic_rules", "state_machines", "actions"):
        value[collection].sort(key=lambda item: item["id"])
    value["tool_descriptors"].sort(key=lambda item: item["descriptor_id"])
    return value


def canonical_manifest(value: Manifest | dict[str, Any]) -> CanonicalManifest:
    model = value if isinstance(value, Manifest) else Manifest.model_validate(value)
    manifest = _sorted_manifest(model)
    encoded = canonical_json(manifest)
    return CanonicalManifest(
        bytes=encoded,
        projection=encoded.decode("utf-8"),
        schema_hash=hashlib.sha256(encoded).digest(),
    )
