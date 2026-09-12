"""Heuristic security scan for user-uploaded Skill content (P7C).

A skill manifest never carries executable code — it only declares a
name/description/instructions and references to already-governed tool
descriptors (see app/services/skills/__init__.py). So the realistic risk
from a self-service upload is not code execution but the free-text fields
smuggling credential material or a prompt-injection payload that the model
would read as part of its own instructions. This is a best-effort, static
pattern scan — not a sandboxed execution scan — and is documented as such
everywhere it is used."""
from __future__ import annotations

import re

MAX_FIELD_LENGTH = 20_000

_SUSPICIOUS_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "PRIVATE_KEY_MATERIAL"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS_ACCESS_KEY_ID"),
    (re.compile(r"aws_secret_access_key", re.IGNORECASE), "AWS_SECRET_KEY_REFERENCE"),
    (re.compile(r"ignore (all |any )?(previous|prior|above) instructions", re.IGNORECASE), "PROMPT_INJECTION_PHRASE"),
    (re.compile(r"disregard (your|all|the) (system prompt|instructions|rules)", re.IGNORECASE), "PROMPT_INJECTION_PHRASE"),
    (re.compile(r"you (have no restrictions|are not bound by any)", re.IGNORECASE), "PROMPT_INJECTION_PHRASE"),
    (re.compile(r"<script[\s>]", re.IGNORECASE), "EMBEDDED_SCRIPT_TAG"),
]


def scan_skill_content(manifest: dict) -> list[str]:
    """Returns a list of finding codes — empty means clean. Checks the
    manifest's free-text fields only; `tools[].descriptor_id` is already
    validated against a fixed whitelist by `validate_skill_manifest` and
    zip-container hygiene (path traversal, executable extensions, size caps)
    is checked separately in app/services/skills/upload.py before this ever
    runs."""
    findings: list[str] = []
    for field in ("name", "description", "instructions"):
        value = manifest.get(field)
        if not isinstance(value, str):
            continue
        if len(value) > MAX_FIELD_LENGTH:
            findings.append(f"{field.upper()}_TOO_LONG")
        for pattern, label in _SUSPICIOUS_PATTERNS:
            if pattern.search(value):
                findings.append(f"{field.upper()}_{label}")
    return findings
