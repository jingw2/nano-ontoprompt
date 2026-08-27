"""Generation, registry, and no-secret contract tests for the runtime corpus.

Run with: python -m pytest test_data/runtime/test_fixture_manifest.py -q
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_runtime_fixtures import generate, validate_manifest  # noqa: E402
from registry import assert_case_registry, load_cases, targets_for  # noqa: E402

RUNTIME_DIR = Path(__file__).resolve().parent
SCANNED_SUFFIXES = {".json", ".sql", ".yml", ".yaml", ".md"}

# Explicit, public, test-only sentinels. Nothing outside this set may look
# like a real credential, key, token, production URI, email, or phone
# number anywhere in the generated corpus.
ALLOWED_SENTINELS = ("runtime/runtime", "vault:runtime-db", "example.invalid")

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9\-_\.]{8,}")
_JWT_RE = re.compile(r"\bey[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_TOKEN_FIELD_RE = re.compile(r'"(access_token|refresh_token)"\s*:\s*"([^"]*)"')
_AKIA_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_CLOUD_URI_MARKERS = (
    "amazonaws.com",
    "core.windows.net",
    "googleapis.com",
    "aliyuncs.com",
    "cloudfront.net",
    "blob.core",
)
_DB_URI_RE = re.compile(r"\b(postgresql|mysql(?:\+pymysql)?)://([^\s\"'/]+)@([^\s\"'/:]+)(?::\d+)?/(\S+)")
_ALLOWED_DB_HOSTS = {"postgres", "mysql", "localhost", "127.0.0.1"}
_EMAIL_RE = re.compile(r"\b[\w.+-]+@([\w-]+(?:\.[\w-]+)+)\b")
_PHONE_RE = re.compile(r"[+]?[0-9][0-9 ()\-]{8,}")
_ISO_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_COMPACT_DATE_PREFIX_RE = re.compile(r"^\d{8}")
_HEX64_RE = re.compile(r"\b[0-9a-f]{64}\b")


def _looks_like_a_date_or_timestamp(candidate: str) -> bool:
    """True if the match is a date/timestamp/seed literal, not a phone number.

    A fixed-seed generation contract (e.g. ``--seed 20260826``) and ISO-8601
    timestamps both produce digit runs that satisfy the broad
    ``[+]?[0-9][0-9 ()-]{8,}`` phone shape purely by coincidence; this
    exempts the two concrete date-like forms this corpus actually uses
    (``YYYY-MM-DD...`` and bare ``YYYYMMDD``) while still catching a
    genuine phone number.
    """

    iso_match = _ISO_DATE_PREFIX_RE.match(candidate)
    if iso_match:
        try:
            date.fromisoformat(iso_match.group(0))
            return True
        except ValueError:
            pass
    compact_match = _COMPACT_DATE_PREFIX_RE.match(candidate)
    if compact_match:
        try:
            datetime.strptime(compact_match.group(0), "%Y%m%d")
            return True
        except ValueError:
            pass
    return False


def _corpus_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in SCANNED_SUFFIXES)


def scan_runtime_corpus(root: Path) -> None:
    """Scan every generated JSON/SQL/YAML/Markdown byte for PII/secrets.

    Raises AssertionError describing the first violation found. The only
    permitted sentinel values are the three literal, public, test-only
    strings in ALLOWED_SENTINELS.
    """

    root = Path(root)
    for path in _corpus_files(root):
        text = path.read_text(encoding="utf-8")
        # Redact known-safe content hashes before pattern matching so a
        # sha256 hex digest can never coincidentally look like a phone
        # number or an AWS-style access key.
        redacted = _HEX64_RE.sub("sha256-hash-redacted-for-scan", text)
        rel = path.relative_to(root)

        if _PRIVATE_KEY_RE.search(redacted):
            raise AssertionError(f"{rel}: contains a private-key header")

        if _BEARER_RE.search(redacted) or _JWT_RE.search(redacted):
            raise AssertionError(f"{rel}: contains a bearer/JWT-shaped token")

        for match in _TOKEN_FIELD_RE.finditer(redacted):
            value = match.group(2)
            if value and value not in ALLOWED_SENTINELS:
                raise AssertionError(f"{rel}: {match.group(1)} has a non-sentinel value {value!r}")

        if _AKIA_RE.search(redacted):
            raise AssertionError(f"{rel}: contains an AWS-style access key ID")

        for marker in _CLOUD_URI_MARKERS:
            if marker in redacted:
                raise AssertionError(f"{rel}: contains a cloud-provider URI marker {marker!r}")

        for match in _DB_URI_RE.finditer(redacted):
            scheme, userinfo, host = match.group(1), match.group(2), match.group(3)
            if userinfo == "runtime:runtime" and host in _ALLOWED_DB_HOSTS:
                continue
            raise AssertionError(f"{rel}: contains a non-sentinel {scheme}:// connection string")

        for match in _EMAIL_RE.finditer(redacted):
            domain = match.group(1)
            if not domain.endswith(".invalid"):
                raise AssertionError(f"{rel}: contains a non-.invalid email domain {domain!r}")

        for match in _PHONE_RE.finditer(redacted):
            candidate = match.group(0)
            if _looks_like_a_date_or_timestamp(candidate):
                continue
            raise AssertionError(f"{rel}: contains a phone-number-shaped value {candidate!r}")


def test_runtime_fixture_generation_is_deterministic(tmp_path):
    first = generate(20260826, tmp_path / "first")
    second = generate(20260826, tmp_path / "second")
    assert first["manifest_version"] == 1
    assert first["files"] == second["files"]
    validate_manifest(tmp_path / "first")


def test_no_pii_or_real_secret():
    scan_runtime_corpus(Path("test_data/runtime"))


def test_every_case_has_a_valid_registered_target_descriptor():
    for case in load_cases(Path("test_data/runtime/manifest.json")):
        assert_case_registry(case)
        targets = targets_for(case["case_id"])
        assert len(targets) == 1
        assert [target.to_dict() for target in targets] == case["test_targets"]


def test_case_registry_metadata_is_complete():
    for case in load_cases(Path("test_data/runtime/manifest.json")):
        if "database" in case["layers"]:
            assert set(case["dialects"]) == {"mysql", "postgresql"}
        if "parity" in case["layers"]:
            assert set(case["transports"]) == {"mcp", "reference-agent", "rest", "sdk"}
        if "refresh" in case["layers"]:
            assert case["refresh_mode"] in {"batch", "micro_batch", "event_driven"}
            assert case["source_contract"] in {"watermark_primary_key", "opaque_source_cursor"}
            assert case["cursor_outcome"] in {"advanced", "unchanged", "dead_lettered"}
            if case["case_id"].startswith("cancel-"):
                assert case["cancel_outcome"] in {"requested", "cancelled", "already_terminal"}
            if case["case_id"] == "config-drift-late-finish":
                assert case["error_code"] == "CONFIGURATION_DRIFT"
                assert case["cursor_outcome"] == "unchanged"
        assert case["execution_mode"] in {"deterministic", "real_model_browser"}
        assert len(case["test_targets"]) == 1
        if case["execution_mode"] == "deterministic":
            assert case["test_targets"][0]["kind"] == "pytest"
        else:
            assert case["test_targets"][0]["kind"] == "playwright"
            assert "journey completes the governed browser loop" in case["test_targets"][0]["title"]
        if "business_journey" in case["layers"]:
            assert case["journey_id"] in {"supply_chain", "finance", "credit"}
            assert case["model_id"] == "deepseek-v4-flash-vision-exp"
            assert case["skip_allowed"] is False
            assert case["fixture_manifest_sha256"]
            assert case["risk_class"] in {"automatic", "human_approved", "rejected"}
