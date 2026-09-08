"""Shared typed contracts for the business-journey DeepSeek vision adapter.

Everything here is exact and closed on purpose: one model ID, one origin,
one input-part shape, and a small set of stable-reason-code typed failures.
Nothing in this module makes a network call; it only defines the data the
rest of the ``business_journeys`` package (and the production
``DeepSeekVisionCaller`` in ``app.services.model_callers.deepseek_vision``)
passes around.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Final, Literal, Mapping

from pydantic import BaseModel as PydanticBaseModel
from pydantic import ConfigDict as PydanticConfigDict

MODEL_ID: Final[str] = "deepseek-v4-flash-vision-exp"
OFFICIAL_ORIGIN: Final[str] = "https://api.deepseek.com"

# The models API's `_validate_contract` (app/services/model_version.py)
# requires a complete, closed contract entry -- it never accepts a guessed
# tokenizer/window. This harness pins one exact model for every run (see
# the module docstring), so these fields describe THIS harness's own fixed
# baseline for that pinned model, not an independently-verified provider
# spec pulled from DeepSeek. `verified_maximum_output_tokens` matches the
# real `max_tokens` this harness actually requests (`deepseek_client.py`)
# rather than understating it -- a mismatch here would make this contract's
# own numbers internally inconsistent with what the harness does.
MODEL_CONTRACT_ENTRY: Final[dict] = {
    "provider_model_revision": MODEL_ID,
    "tokenizer_family": "deepseek-v4",
    "tokenizer_revision": "harness-pinned-baseline",
    "verified_context_window_tokens": 65536,
    "verified_maximum_output_tokens": 32000,
    "provider_contract_revision": "harness-pinned-v1",
    "provider_contract_hash": hashlib.sha256(MODEL_ID.encode()).hexdigest(),
}

CallKind = Literal["ontology", "agent_initial", "agent_final"]


# ---------------------------------------------------------------------------
# Typed failures (stable reason codes as the leading token of the message).
# ---------------------------------------------------------------------------


class BusinessJourneyModelError(Exception):
    """Base class for every typed failure in this package."""


class DeepSeekEndpointError(BusinessJourneyModelError):
    """A URL is not the exact, official, redirect-free DeepSeek endpoint."""


class ModelConfigurationError(BusinessJourneyModelError):
    """A requested/observed/configured model, provider, or origin drifted."""


class DeepSeekProviderError(BusinessJourneyModelError):
    """A non-retryable provider failure (never carries a response body)."""

    def __init__(self, message: str, *, category: str = "provider_error") -> None:
        super().__init__(message)
        self.category = category


class DeepSeekRetryExhausted(BusinessJourneyModelError):
    """The one allowed timeout/429 retry also failed."""

    def __init__(self, message: str, *, category: str, http_attempts: int = 2) -> None:
        super().__init__(message)
        self.category = category
        self.http_attempts = http_attempts


class SemanticMinimumError(BusinessJourneyModelError):
    """A response failed to satisfy a journey's semantic minimum."""


class ArtifactSafetyError(BusinessJourneyModelError):
    """An artifact contains a forbidden value or a sensitive pattern."""


# ---------------------------------------------------------------------------
# Model input / output shapes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InputPart:
    """The only model input part type: text or a base64 image_url block."""

    kind: Literal["text", "image"]
    media_type: str
    content: bytes | str
    sha256: str


@dataclass(frozen=True)
class ModelProbe:
    """Result of one ``GET /models`` preflight check."""

    requested_model: str
    available_models: tuple[str, ...]
    observed_model: str
    status: Literal["ok"]


@dataclass(frozen=True)
class ModelResponse:
    """Result of one ``POST /chat/completions`` call.

    Deliberately excludes raw prompt/response/header data.
    """

    model: str
    structured: Mapping[str, object]
    logical_call_id: str | None
    http_attempts: int
    retry_count: int
    requested_at: datetime
    completed_at: datetime


@dataclass(frozen=True)
class JourneyModelContext:
    """Immutable per-call addressing record.

    ``correlation_id`` is always exactly
    ``f"{run_id}:{journey_id}:{call_kind}:{logical_call_index}"``.
    """

    run_id: str
    journey_id: str
    model_config_version_id: str
    model_id: Literal["deepseek-v4-flash-vision-exp"]
    call_kind: CallKind
    logical_call_index: Literal[1, 2, 3]
    correlation_id: str

    def for_call(self, call_kind: CallKind, logical_call_index: Literal[1, 2, 3]) -> "JourneyModelContext":
        """Return a copy addressed at a different call kind/index.

        Recomputes ``correlation_id`` from the (unchanged) run/journey id so
        callers never hand-assemble the correlation string themselves.
        """
        correlation_id = f"{self.run_id}:{self.journey_id}:{call_kind}:{logical_call_index}"
        return replace(self, call_kind=call_kind, logical_call_index=logical_call_index, correlation_id=correlation_id)


@dataclass(frozen=True)
class ImmutableModelConfigVersion:
    """One pinned, immutable DeepSeek vision model configuration version."""

    version_id: str
    provider: str
    model_id: str
    origin: str
    behavior_hash: str
    frozen_at: object
    # Persisted on `model_config_versions.options` for reproducibility, but
    # previously never read back out of `ModelConfigEvidence` or forwarded
    # to the real completion request -- every real call silently ran at
    # DeepSeek's default sampling instead of this pinned baseline.
    temperature: float = 0.0
    seed: int = 0


# ---------------------------------------------------------------------------
# Semantic minimum contracts.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JourneySemanticMinimum:
    """The exact entities/relations/rules/actions/citations a response needs."""

    entities: tuple[str, ...]
    relations: tuple[str, ...]
    rules: tuple[str, ...]
    actions: tuple[str, ...]
    keywords: tuple[str, ...]
    source_citation_ids: tuple[str, ...]
    numeric_predicates: Mapping[str, Mapping[str, object]]
    low_risk_action: str
    high_risk_action: str


@dataclass(frozen=True)
class SemanticValidation:
    """The result of checking one response against a semantic minimum."""

    passed: bool
    missing_entities: tuple[str, ...]
    missing_relations: tuple[str, ...]
    missing_rules: tuple[str, ...]
    missing_actions: tuple[str, ...]
    missing_citations: tuple[str, ...]
    missing_keywords: tuple[str, ...]
    numeric_predicate_failures: tuple[str, ...]
    reason_codes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Ledger.
#
# NOTE: this module deliberately does NOT define its own `JourneyManifest`.
# The real, typed manifest is Task 1's `test_data/runtime/journey_registry.
# JourneyManifest`; `evals.business_journeys.artifacts` imports it directly
# (following the cross-directory import convention already established by
# `backend/tests/runtime/run_registered_cases.py`) rather than this module
# defining a second, incompatible shape.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LogicalCallLease:
    """A leased, uniquely-indexed logical call slot for one run/journey."""

    run_id: str
    journey_id: str
    logical_call_index: int
    call_kind: str
    correlation_id: str
    logical_call_id: str


MAX_LOGICAL_MODEL_CALLS: Final[int] = 3
MAX_HTTP_ATTEMPTS: Final[int] = 6


class ModelCallLedger:
    """Atomically-counted logical-call/HTTP-attempt ledger.

    Backed by a private SQLite connection (in-memory by default) so
    ``(run_id, journey_id, logical_call_index)`` uniqueness and the
    3-logical-call / 6-HTTP-attempt budgets are enforced by real constraints
    plus a lock, not by convention.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_totals (
                run_id TEXT NOT NULL,
                journey_id TEXT NOT NULL,
                logical_model_calls INTEGER NOT NULL DEFAULT 0,
                http_attempts INTEGER NOT NULL DEFAULT 0,
                preflight_checked_at TEXT,
                preflight_model_id TEXT,
                PRIMARY KEY (run_id, journey_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_logical_calls (
                run_id TEXT NOT NULL,
                journey_id TEXT NOT NULL,
                logical_call_index INTEGER NOT NULL,
                call_kind TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                http_attempts INTEGER NOT NULL DEFAULT 0,
                observed_model TEXT,
                finished_at TEXT,
                PRIMARY KEY (run_id, journey_id, logical_call_index)
            )
            """
        )
        self._conn.commit()

    def _ensure_totals_row(self, run_id: str, journey_id: str) -> None:
        self._conn.execute(
            "INSERT INTO ledger_totals (run_id, journey_id) VALUES (?, ?) "
            "ON CONFLICT(run_id, journey_id) DO NOTHING",
            (run_id, journey_id),
        )

    def record_preflight(self, context: JourneyModelContext, observed_model: str) -> None:
        with self._lock:
            self._ensure_totals_row(context.run_id, context.journey_id)
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE ledger_totals SET preflight_checked_at = ?, preflight_model_id = ? "
                "WHERE run_id = ? AND journey_id = ?",
                (now, observed_model, context.run_id, context.journey_id),
            )
            self._conn.commit()

    def begin_logical_call(self, context: JourneyModelContext) -> LogicalCallLease:
        with self._lock:
            self._ensure_totals_row(context.run_id, context.journey_id)
            try:
                self._conn.execute(
                    "INSERT INTO ledger_logical_calls "
                    "(run_id, journey_id, logical_call_index, call_kind, correlation_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        context.run_id,
                        context.journey_id,
                        context.logical_call_index,
                        context.call_kind,
                        context.correlation_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                self._conn.rollback()
                raise ModelConfigurationError(
                    "LOGICAL_CALL_INDEX_DUPLICATE: "
                    f"{context.run_id}:{context.journey_id}:{context.logical_call_index}"
                ) from exc

            row = self._conn.execute(
                "SELECT logical_model_calls FROM ledger_totals WHERE run_id = ? AND journey_id = ?",
                (context.run_id, context.journey_id),
            ).fetchone()
            next_count = row[0] + 1
            if next_count > MAX_LOGICAL_MODEL_CALLS:
                self._conn.rollback()
                raise ModelConfigurationError(
                    f"LOGICAL_CALL_BUDGET_EXCEEDED: {context.run_id}:{context.journey_id}"
                )
            self._conn.execute(
                "UPDATE ledger_totals SET logical_model_calls = ? WHERE run_id = ? AND journey_id = ?",
                (next_count, context.run_id, context.journey_id),
            )
            self._conn.commit()

            return LogicalCallLease(
                run_id=context.run_id,
                journey_id=context.journey_id,
                logical_call_index=context.logical_call_index,
                call_kind=context.call_kind,
                correlation_id=context.correlation_id,
                logical_call_id=f"{context.run_id}:{context.journey_id}:{context.logical_call_index}",
            )

    def record_http_attempt(self, lease: LogicalCallLease, attempt_no: int) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT http_attempts FROM ledger_totals WHERE run_id = ? AND journey_id = ?",
                (lease.run_id, lease.journey_id),
            ).fetchone()
            current = row[0] if row else 0
            next_total = current + 1
            if next_total > MAX_HTTP_ATTEMPTS:
                self._conn.rollback()
                raise ModelConfigurationError(
                    f"HTTP_ATTEMPT_BUDGET_EXCEEDED: {lease.run_id}:{lease.journey_id}"
                )
            self._conn.execute(
                "UPDATE ledger_totals SET http_attempts = ? WHERE run_id = ? AND journey_id = ?",
                (next_total, lease.run_id, lease.journey_id),
            )
            self._conn.execute(
                "UPDATE ledger_logical_calls SET http_attempts = ? "
                "WHERE run_id = ? AND journey_id = ? AND logical_call_index = ?",
                (attempt_no, lease.run_id, lease.journey_id, lease.logical_call_index),
            )
            self._conn.commit()

    def finish_logical_call(self, lease: LogicalCallLease, observed_model: str) -> None:
        with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "UPDATE ledger_logical_calls SET observed_model = ?, finished_at = ? "
                "WHERE run_id = ? AND journey_id = ? AND logical_call_index = ?",
                (observed_model, now, lease.run_id, lease.journey_id, lease.logical_call_index),
            )
            self._conn.commit()

    def totals(self, run_id: str, journey_id: str) -> Mapping[str, int]:
        row = self._conn.execute(
            "SELECT logical_model_calls, http_attempts FROM ledger_totals "
            "WHERE run_id = ? AND journey_id = ?",
            (run_id, journey_id),
        ).fetchone()
        if row is None:
            return {"logical_model_calls": 0, "http_attempts": 0}
        return {"logical_model_calls": row[0], "http_attempts": row[1]}


# ---------------------------------------------------------------------------
# Artifact safety contracts.
# ---------------------------------------------------------------------------


class SanitizedBrowserArtifactRef(PydanticBaseModel):
    """Redacted metadata for one browser artifact; never raw content.

    A closed pydantic model (``extra="forbid"``) so no one can widen it into
    a raw-content carrier, and so it composes directly as
    ``ArtifactAllowlist.browser_artifacts``'s element type instead of an
    open ``Mapping`` that would accept arbitrary keys/values.
    """

    model_config = PydanticConfigDict(extra="forbid", frozen=True)

    kind: str
    original_name_hash: str
    size_bytes: int
    content_type: str

    def to_dict(self) -> Mapping[str, object]:
        return self.model_dump(mode="json")


@dataclass(frozen=True)
class ScanResult:
    """Outcome of ``scan_and_materialize``."""

    status: Literal["passed", "failed"]
    safe_evidence_dir: object
    failure_summary_path: object
    scan_safe: bool
    reason_codes: tuple[str, ...]
    counts: Mapping[str, int]


# ---------------------------------------------------------------------------
# Small helpers shared by client/validators/artifacts/tests.
# ---------------------------------------------------------------------------


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
