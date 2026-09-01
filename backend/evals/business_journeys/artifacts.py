"""Closed-schema artifact allowlist and forbidden-value safety scanner.

``ArtifactAllowlist`` is a closed schema: unknown fields are rejected, so no
one can widen it into a raw-content carrier by accident. Forbidden values are
always derived from a loaded ``JourneyManifest`` (never a hand-maintained
literal list); pattern-based checks (email/JWT/bearer/sensitive header keys)
are the only hardcoded detectors, because those are structural, not
fixture-specific.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from .contracts import (
    ArtifactSafetyError,
    JourneyManifest,
    ScanResult,
    SanitizedBrowserArtifactRef,
    sha256_text,
)

SCANNER_VERSION = "1"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_BEARER_RE = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_SENSITIVE_HEADER_KEYS = {
    "authorization",
    "x-api-key",
    "api-key",
    "cookie",
    "set-cookie",
    "proxy-authorization",
}

_RAW_ARTIFACT_SUFFIXES = {".zip", ".png", ".jpg", ".jpeg", ".webm", ".log"}


# ---------------------------------------------------------------------------
# Closed schemas.
# ---------------------------------------------------------------------------


class ArtifactAllowlist(BaseModel):
    """The only fields ever allowed into uploaded CI evidence."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    schema_version: int
    run_id: str
    journey_id: str
    fixture_manifest_sha256: str
    model_requested: str
    model_observed: str
    model_origin: str
    model_caller: str
    model_config_version_id: str
    preflight_model_id: str
    logical_model_calls: int
    http_attempts: int
    retry_count: int
    call_timestamps: tuple[str, ...]
    call_kinds: tuple[str, ...]
    semantic_outcomes: tuple[str, ...]
    state_transitions: tuple[str, ...]
    citation_ids: tuple[str, ...]
    tool_trace_ids: tuple[str, ...]
    audit_event_ids: tuple[str, ...]
    backend_trace_ids: tuple[str, ...]
    browser_artifacts: tuple[Mapping[str, object], ...] = ()


class ScannerFailureSummary(BaseModel):
    """Fixed, closed schema for the on-failure evidence upload."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    run_id: str
    status: Literal["failed"]
    reason_codes: tuple[str, ...]
    category_counts: Mapping[str, int]
    scanner_version: str

    def to_json(self) -> Mapping[str, object]:
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Forbidden-value derivation.
# ---------------------------------------------------------------------------


def build_fixture_forbidden_values(manifest: JourneyManifest) -> frozenset[str]:
    """Union every literal value a journey fixture is willing to disclose.

    Every fixture cell/document projection, prompt-injection sentinel,
    synthetic secret/header/JWT value, PII sentinel, and production URL the
    manifest carries becomes one forbidden string. There is no separate,
    hand-maintained list.
    """
    values: set[str] = set()
    for group in (
        manifest.fixture_cells,
        manifest.prompt_injection_sentinels,
        manifest.secret_sentinels,
        manifest.pii_sentinels,
        manifest.production_urls,
    ):
        for value in group:
            if value:
                values.add(str(value))
    return frozenset(values)


# ---------------------------------------------------------------------------
# Browser artifact redaction.
# ---------------------------------------------------------------------------


def _contains_forbidden(data: bytes, forbidden_values: frozenset[str]) -> bool:
    text = data.decode("utf-8", errors="replace")
    return any(value and value in text for value in forbidden_values)


def _infer_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return "screenshot"
    if suffix in {".zip", ".trace"}:
        return "trace"
    return "log"


def _infer_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".zip": "application/zip",
        ".log": "text/plain",
    }.get(suffix, "application/octet-stream")


def sanitize_browser_artifacts(
    paths: Sequence[Path], *, forbidden_values: frozenset[str]
) -> tuple[SanitizedBrowserArtifactRef, ...]:
    """Emit only redacted metadata for browser artifacts; omit unsafe ones.

    Raw Playwright traces, screenshots, and logs are always unsafe to
    upload as-is: this only ever returns a hash/size/kind reference, and
    only when the underlying file contains no forbidden value.
    """
    refs: list[SanitizedBrowserArtifactRef] = []
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if _contains_forbidden(data, forbidden_values):
            continue
        refs.append(
            SanitizedBrowserArtifactRef(
                kind=_infer_kind(path),
                original_name_hash=sha256_text(path.name),
                size_bytes=len(data),
                content_type=_infer_content_type(path),
            )
        )
    return tuple(refs)


# ---------------------------------------------------------------------------
# Scanning.
# ---------------------------------------------------------------------------


def _has_sensitive_header_field(node: object) -> bool:
    if isinstance(node, Mapping):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in _SENSITIVE_HEADER_KEYS:
                return True
            if _has_sensitive_header_field(value):
                return True
        return False
    if isinstance(node, (list, tuple)):
        return any(_has_sensitive_header_field(item) for item in node)
    return False


def scan_artifact(path: Path, *, forbidden_values: frozenset[str]) -> None:
    """Recursively scan one file's bytes/fields; raise on anything unsafe.

    Checks (in order): fixture-manifest-derived forbidden value echoes,
    PII email patterns, bearer-token/secret patterns, JWT-shaped values, and
    sensitive header field names in any parsed JSON structure. The raised
    message never embeds the raw matched value, the file path, or file
    contents -- only a small, fixed set of category tokens.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")

    reasons: set[str] = set()
    if any(value and value in text for value in forbidden_values):
        reasons.add("FORBIDDEN_VALUE_ECHOED")
    if _EMAIL_RE.search(text):
        reasons.add("PII_EMAIL_DETECTED")
    if _BEARER_RE.search(text):
        reasons.add("SECRET_BEARER_TOKEN_DETECTED")
    if _JWT_RE.search(text):
        reasons.add("JWT_LIKE_VALUE_DETECTED")

    try:
        payload = json.loads(text)
    except ValueError:
        payload = None
    if payload is not None and _has_sensitive_header_field(payload):
        reasons.add("SENSITIVE_HEADER_FIELD_DETECTED")

    if reasons:
        raise ArtifactSafetyError(",".join(sorted(reasons)))


def _validate_allowlist_schema(payload: Mapping[str, object]) -> None:
    try:
        ArtifactAllowlist.model_validate(payload)
    except ValidationError as exc:
        raise ArtifactSafetyError("ARTIFACT_SCHEMA_VIOLATION") from exc


def scan_and_materialize(
    staging_dir: Path,
    *,
    output_dir: Path,
    failure_summary_path: Path,
    manifest: JourneyManifest,
    run_id: str,
) -> ScanResult:
    """Scan every file under ``staging_dir`` and materialize the outcome.

    On success: writes each staged file, re-validated and re-serialized
    through the closed ``ArtifactAllowlist`` schema, into a fresh
    ``output_dir``. On failure: writes only a fixed-schema
    ``ScannerFailureSummary`` to ``failure_summary_path`` and creates no
    output directory.
    """
    forbidden_values = build_fixture_forbidden_values(manifest)
    staged_files = sorted(p for p in staging_dir.rglob("*") if p.is_file())

    counts = {"scanned": 0, "failed": 0}
    reason_codes: set[str] = set()

    for path in staged_files:
        counts["scanned"] += 1
        failed_this_file = False
        try:
            scan_artifact(path, forbidden_values=forbidden_values)
        except ArtifactSafetyError as exc:
            reason_codes.update(str(exc).split(","))
            failed_this_file = True
        try:
            _validate_allowlist_schema(json.loads(path.read_text(encoding="utf-8", errors="replace")))
        except (ArtifactSafetyError, ValueError):
            reason_codes.add("ARTIFACT_SCHEMA_VIOLATION")
            failed_this_file = True
        if failed_this_file:
            counts["failed"] += 1

    if reason_codes:
        summary = ScannerFailureSummary(
            schema_version=1,
            run_id=run_id,
            status="failed",
            reason_codes=tuple(sorted(reason_codes)),
            category_counts={code: 1 for code in reason_codes},
            scanner_version=SCANNER_VERSION,
        )
        failure_summary_path.parent.mkdir(parents=True, exist_ok=True)
        failure_summary_path.write_text(json.dumps(summary.to_json(), indent=2, sort_keys=True))
        return ScanResult(
            status="failed",
            safe_evidence_dir=None,
            failure_summary_path=failure_summary_path,
            scan_safe=False,
            reason_codes=tuple(sorted(reason_codes)),
            counts=counts,
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    for path in staged_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        allowed = ArtifactAllowlist.model_validate(payload)
        target = output_dir / path.relative_to(staging_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(allowed.model_dump_json(indent=2))

    return ScanResult(
        status="passed",
        safe_evidence_dir=output_dir,
        failure_summary_path=failure_summary_path,
        scan_safe=True,
        reason_codes=(),
        counts=counts,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan and materialize business-journey CI evidence.")
    parser.add_argument("staging_dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--failure-summary", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--journey-id", required=True)
    parser.add_argument("--fixture-version", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args(argv)

    manifest = JourneyManifest(
        journey_id=args.journey_id,
        fixture_version=args.fixture_version,
        manifest_sha256=args.manifest_sha256,
    )
    result = scan_and_materialize(
        args.staging_dir,
        output_dir=args.output,
        failure_summary_path=args.failure_summary,
        manifest=manifest,
        run_id=args.run_id,
    )
    print(f"scan_safe={'true' if result.scan_safe else 'false'}")
    return 0 if result.scan_safe else 1


if __name__ == "__main__":
    sys.exit(main())
