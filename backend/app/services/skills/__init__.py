"""Signed Skill canonicalization and signature verification (P7C).

Canonicalization is the same discipline as retention's `_canonical_hash`:
json.dumps(manifest, sort_keys=True, separators=(",", ":")). Signatures are
Ed25519 over the hex-decoded canonical hash — deterministic for a given
manifest, independent of transport formatting."""
from __future__ import annotations

import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.services.tool_gateway import EXTERNAL_TOOL_DESCRIPTORS, TRUSTED_READ_DESCRIPTORS

# a skill manifest may only reference these governed descriptors — skills
# never introduce executable code; the approval and dispatch rechecks
# enforce this at every boundary
VALID_MANIFEST_TOOL_DESCRIPTORS = frozenset(
    set(TRUSTED_READ_DESCRIPTORS) | set(EXTERNAL_TOOL_DESCRIPTORS)
)

_SKILL_TOOL_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def canonicalize_manifest(manifest: dict) -> str:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"))


def manifest_canonical_hash(manifest: dict) -> str:
    return hashlib.sha256(canonicalize_manifest(manifest).encode("utf-8")).hexdigest()


def verify_manifest_signature(*, manifest: dict, public_key_hex: str, signature_hex: str) -> bool:
    digest = bytes.fromhex(manifest_canonical_hash(manifest))
    try:
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        public_key.verify(bytes.fromhex(signature_hex), digest)
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False


def validate_skill_manifest(manifest: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(manifest.get("name"), str) or not manifest["name"].strip():
        problems.append("MANIFEST_NAME_REQUIRED")
    if not isinstance(manifest.get("description"), str) or not manifest["description"].strip():
        problems.append("MANIFEST_DESCRIPTION_REQUIRED")
    instructions = manifest.get("instructions")
    if instructions is not None and not isinstance(instructions, str):
        problems.append("MANIFEST_INSTRUCTIONS_MUST_BE_STRING")
    tools = manifest.get("tools")
    if tools is None:
        return problems
    if not isinstance(tools, list) or not tools:
        problems.append("MANIFEST_TOOLS_MUST_BE_NONEMPTY_LIST")
        return problems
    seen_aliases: set[str] = set()
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            problems.append(f"MANIFEST_TOOL_{index}_NOT_OBJECT")
            continue
        alias = tool.get("alias")
        if not isinstance(alias, str) or not _SKILL_TOOL_ALIAS_RE.match(alias):
            problems.append(f"MANIFEST_TOOL_{index}_ALIAS_INVALID")
        elif alias in seen_aliases:
            problems.append(f"MANIFEST_TOOL_{index}_ALIAS_DUPLICATE")
        seen_aliases.add(str(alias))
        descriptor_id = tool.get("descriptor_id")
        if descriptor_id not in VALID_MANIFEST_TOOL_DESCRIPTORS:
            problems.append(f"MANIFEST_TOOL_{index}_DESCRIPTOR_UNKNOWN")
        if not isinstance(tool.get("description"), str) or not tool["description"].strip():
            problems.append(f"MANIFEST_TOOL_{index}_DESCRIPTION_REQUIRED")
        parameters = tool.get("parameters")
        if parameters is not None and not isinstance(parameters, dict):
            problems.append(f"MANIFEST_TOOL_{index}_PARAMETERS_MUST_BE_OBJECT")
    return problems
