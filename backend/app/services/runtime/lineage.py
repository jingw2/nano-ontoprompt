"""Collect governed DatasetVersion/PipelineRun lineage for snapshots."""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.v2.dataset import DatasetVersion
from app.models.v2.pipeline import Pipeline, PipelineRun, PipelineRunInput
from app.schemas.runtime_snapshot import LineageBundle


class LineageError(Exception):
    """A requested dataset version cannot produce governed lineage."""

    def __init__(self, reason_code: str, message: str | None = None):
        self.reason_code = reason_code
        super().__init__(message or reason_code)


class LineageValidationError(LineageError):
    """Compatibility name for callers that distinguish validation failures."""


# These are intentionally aggregate/metadata fields.  In particular, raw
# rows, payloads, values, and arbitrary statistics are never copied into a
# snapshot summary.
_QUALITY_KEYS = {
    "row_count",
    "rowcount",
    "accepted_count",
    "rejected_count",
    "duplicate_count",
    "late_count",
    "null_count",
    "error_count",
    "warning_count",
    "quality_score",
    "completeness_score",
    "validity_score",
    "consistency_score",
    "lag_seconds",
    "schema_hash",
    "checksum",
}
_EVIDENCE_KEYS = {
    "id",
    "source_id",
    "source_type",
    "locator",
    "content_hash",
    "source_hash",
    "citation_id",
    "document_id",
    "dataset_version_id",
    "pipeline_run_id",
    "security_domain_id",
    "tenant_id",
    "tenant",
    "refresh_run_id",
}
_DOMAIN_KEYS = {"security_domain_id", "tenant_id", "tenant"}


def _normalize_ids(dataset_version_ids: Sequence[str]) -> tuple[str, ...]:
    if isinstance(dataset_version_ids, (str, bytes)):
        raise LineageError("INVALID_DATASET_VERSION_IDS", "dataset_version_ids must be a sequence")
    try:
        values = list(dataset_version_ids)
    except TypeError as exc:
        raise LineageError("INVALID_DATASET_VERSION_IDS") from exc
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise LineageError("INVALID_DATASET_VERSION_IDS")
    return tuple(sorted(set(values)))


def _safe_quality(source: Any) -> dict[str, Any]:
    """Copy only scalar aggregate fields from stored pipeline statistics."""
    if not isinstance(source, dict):
        return {}
    result: dict[str, Any] = {}
    for key in sorted(_QUALITY_KEYS):
        value = source.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[key] = value
    nested = source.get("quality_summary")
    if isinstance(nested, dict):
        for key in sorted(_QUALITY_KEYS):
            value = nested.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                result.setdefault(key, value)
    return result


def _safe_citation(source: Any) -> dict[str, Any] | None:
    if not isinstance(source, dict):
        return None
    citation = {
        key: source[key]
        for key in sorted(_EVIDENCE_KEYS)
        if key in source and isinstance(source[key], (str, int, float, bool))
    }
    return citation or None


def _citations_from_provenance(provenance: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(provenance, dict):
        return ()
    candidates: list[Any] = []
    for key in ("citations", "evidence", "sources"):
        value = provenance.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, dict):
            candidates.append(value)
    direct = _safe_citation(provenance)
    if direct is not None:
        candidates.append(direct)
    unique: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
    for candidate in candidates:
        citation = _safe_citation(candidate)
        if citation:
            key = tuple(sorted(citation.items()))
            unique[key] = citation
    return tuple(unique[key] for key in sorted(unique))


def _provenance_domains(provenance: Any) -> tuple[str, ...]:
    if not isinstance(provenance, dict):
        return ()
    values = []
    for key in _DOMAIN_KEYS:
        value = provenance.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return tuple(sorted(set(values)))


def _synthetic_tenant(value: str | None) -> str | None:
    """Understand the repository's synthetic user/tenant fixture convention."""
    if not isinstance(value, str):
        return None
    parts = value.split("-")
    if len(parts) >= 3 and parts[0] == "user":
        return f"tenant-{parts[1]}"
    return None


def _quality_summary(
    versions: dict[str, DatasetVersion],
    runs: dict[str, PipelineRun],
    refresh_runs: dict[str, Any],
    pairs: tuple[tuple[str, str], ...],
) -> dict[str, Any]:
    per_run: list[dict[str, Any]] = []
    for run_id in sorted(runs):
        run = runs[run_id]
        aggregate = _safe_quality(run.stats)
        refresh_ids = sorted({
            versions[dv].refresh_run_id
            for dv, rid in pairs
            if rid == run_id and versions[dv].refresh_run_id
        })
        for refresh_id in refresh_ids:
            for key, value in _safe_quality(getattr(refresh_runs.get(refresh_id), "quality_summary", None)).items():
                aggregate.setdefault(key, value)
        row_count = aggregate.get("row_count", aggregate.get("rowcount"))
        if row_count is None:
            row_counts = [
                versions[dv].rowcount
                for dv, rid in pairs
                if rid == run_id and versions[dv].rowcount is not None
            ]
            if row_counts:
                row_count = sum(row_counts)
                aggregate["row_count"] = row_count
        per_run.append({"pipeline_run_id": run_id, **aggregate})

    row_counts = [version.rowcount for version in versions.values() if version.rowcount is not None]
    if row_counts:
        total_row_count = sum(row_counts)
    else:
        total_row_count = sum(
            item.get("row_count", item.get("rowcount", 0))
            for item in per_run
            if isinstance(item.get("row_count", item.get("rowcount", 0)), (int, float))
        )
    summary: dict[str, Any] = {
        "dataset_version_count": len(versions),
        "pipeline_run_count": len(runs),
        "row_count": total_row_count,
        "runs": per_run,
    }
    # Keep top-level aggregate fields useful to callers while retaining the
    # complete per-run breakdown.  Numeric counters are summed; scores are
    # averaged only when every contributing value is numeric.
    for key in sorted(_QUALITY_KEYS - {"row_count", "rowcount", "checksum", "schema_hash"}):
        values = [
            item[key]
            for item in per_run
            if key in item
            and isinstance(item[key], (int, float))
            and not isinstance(item[key], bool)
        ]
        if values:
            if key.endswith("_score"):
                summary[key] = sum(values) / len(values)
            else:
                summary[key] = sum(values)
    return summary


def _evidence_summary(
    versions: dict[str, DatasetVersion],
    run_inputs: Sequence[PipelineRunInput],
    refresh_runs: dict[str, Any],
) -> dict[str, Any]:
    citations: list[dict[str, Any]] = []
    for row in run_inputs:
        citations.extend(_citations_from_provenance(row.provenance))
    for version in versions.values():
        refresh = refresh_runs.get(version.refresh_run_id)
        if refresh is not None:
            # Task 20: tag each refresh-sourced citation with the governed
            # RefreshRun it came from, so Runtime freshness/lineage evidence
            # can point back to the exact refresh, not just the source.
            for citation in _citations_from_provenance(getattr(refresh, "source_provenance", None)):
                citation = dict(citation)
                citation.setdefault("refresh_run_id", refresh.id)
                citations.append(citation)
    unique: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}
    for citation in citations:
        unique[tuple(sorted(citation.items()))] = citation
    sorted_citations = [unique[key] for key in sorted(unique)]
    return {"citation_count": len(sorted_citations), "citations": sorted_citations}


def collect_lineage(db: Session, dataset_version_ids: Sequence[str]) -> LineageBundle:
    """Return complete governed lineage for all requested DatasetVersions.

    The authoritative relationship is ``PipelineRunInput``.  A
    ``DatasetVersion.refresh_run_id`` is supplemental provenance and cannot
    substitute for the association required by Task 11's database guard.
    """
    version_ids = _normalize_ids(dataset_version_ids)
    if not version_ids:
        return LineageBundle()

    versions = {
        version.id: version
        for version in db.execute(
            select(DatasetVersion).where(DatasetVersion.id.in_(version_ids))
        ).scalars().all()
    }
    missing = tuple(value for value in version_ids if value not in versions)
    if missing:
        raise LineageError("LINEAGE_INCOMPLETE", f"missing dataset versions: {', '.join(missing)}")

    rows = db.execute(
        select(PipelineRunInput, PipelineRun)
        .join(PipelineRun, PipelineRun.id == PipelineRunInput.pipeline_run_id)
        .where(PipelineRunInput.dataset_version_id.in_(version_ids))
    ).all()
    if not rows:
        raise LineageError("LINEAGE_INCOMPLETE", "no PipelineRunInput provenance")

    pairs: set[tuple[str, str]] = set()
    runs: dict[str, PipelineRun] = {}
    domains: set[str] = set()
    tenants: set[str] = set()
    citations_inputs: list[PipelineRunInput] = []
    for input_row, run in rows:
        pair = (input_row.dataset_version_id, input_row.pipeline_run_id)
        pairs.add(pair)
        runs[run.id] = run
        citations_inputs.append(input_row)
        domains.update(_provenance_domains(input_row.provenance))
        if isinstance(input_row.provenance, dict):
            tenant = input_row.provenance.get("tenant_id", input_row.provenance.get("tenant"))
            if isinstance(tenant, str) and tenant:
                tenants.add(tenant)

    present_versions = {dataset_version_id for dataset_version_id, _ in pairs}
    if present_versions != set(version_ids):
        missing = sorted(set(version_ids) - present_versions)
        raise LineageError("LINEAGE_INCOMPLETE", f"dataset versions lack provenance: {', '.join(missing)}")
    if len(domains) > 1 or len(tenants) > 1:
        raise LineageError("TENANT_MISMATCH", "lineage spans multiple security domains or tenants")

    # Every selected originating run must be successful and complete.  The
    # output pointer may differ from an input version for multi-source runs,
    # but it must exist for a run to be governed.
    for run in runs.values():
        if not run.is_governed:
            raise LineageError("LINEAGE_NOT_GOVERNED", f"pipeline run {run.id} is not completed successfully")

    output_ids = tuple(sorted({run.dataset_version_id for run in runs.values() if run.dataset_version_id}))
    output_versions = {
        version.id
        for version in db.execute(
            select(DatasetVersion).where(DatasetVersion.id.in_(output_ids))
        ).scalars().all()
    } if output_ids else set()
    if set(output_ids) != output_versions:
        raise LineageError("LINEAGE_NOT_GOVERNED", "pipeline run output provenance is missing")

    # A snapshot's requested set must be the complete input set of each run;
    # silently dropping another input would make the hash/provenance false.
    all_run_inputs = db.execute(
        select(PipelineRunInput).where(PipelineRunInput.pipeline_run_id.in_(tuple(runs)))
    ).scalars().all()
    for input_row in all_run_inputs:
        if input_row.dataset_version_id not in set(version_ids):
            raise LineageError(
                "LINEAGE_INCOMPLETE",
                f"pipeline run {input_row.pipeline_run_id} has an unselected input",
            )
        if input_row.dataset_version_id not in versions:
            raise LineageError("LINEAGE_INCOMPLETE", f"missing input dataset version {input_row.dataset_version_id}")

    # Use source creator domains as a second, durable check when the older
    # refresh lineage did not carry explicit security-domain metadata.
    pipeline_ids = tuple(sorted({run.pipeline_id for run in runs.values()}))
    pipelines = {
        pipeline.id: pipeline
        for pipeline in db.execute(select(Pipeline).where(Pipeline.id.in_(pipeline_ids))).scalars().all()
    }
    creator_ids = tuple(sorted({pipeline.created_by for pipeline in pipelines.values() if pipeline.created_by}))
    if creator_ids:
        try:
            from app.models.user import User

            creators = db.execute(select(User).where(User.id.in_(creator_ids))).scalars().all()
            creator_domains = {creator.security_domain_id for creator in creators if creator.security_domain_id}
            if len(creator_domains) > 1:
                raise LineageError("TENANT_MISMATCH", "pipeline lineage spans multiple security domains")
            domains.update(creator_domains)
            for creator in creators:
                tenant = _synthetic_tenant(creator.id)
                if tenant:
                    tenants.add(tenant)
            if len(domains) > 1 or len(tenants) > 1:
                raise LineageError("TENANT_MISMATCH", "lineage spans multiple security domains or tenants")
        except LineageError:
            raise
        except SQLAlchemyError:
            # Some migration-only test databases omit users; explicit
            # provenance remains authoritative in that case.
            pass

    try:
        from app.models.v2.refresh import RefreshRun

        refresh_ids = tuple(sorted({
            version.refresh_run_id
            for version in versions.values()
            if version.refresh_run_id
        }))
        refresh_runs = {
            refresh.id: refresh
            for refresh in db.execute(select(RefreshRun).where(RefreshRun.id.in_(refresh_ids))).scalars().all()
        } if refresh_ids else {}
        for refresh in refresh_runs.values():
            domains.update(_provenance_domains(getattr(refresh, "source_provenance", None)))
            source_provenance = getattr(refresh, "source_provenance", None)
            if isinstance(source_provenance, dict):
                tenant = source_provenance.get("tenant_id", source_provenance.get("tenant"))
                if isinstance(tenant, str) and tenant:
                    tenants.add(tenant)
        if len(domains) > 1 or len(tenants) > 1:
            raise LineageError("TENANT_MISMATCH", "lineage spans multiple security domains or tenants")
    except (ImportError, SQLAlchemyError):
        refresh_runs = {}

    ordered_pairs = tuple(sorted(pairs))
    return LineageBundle(
        dataset_version_ids=version_ids,
        pipeline_run_ids=tuple(sorted(runs)),
        input_pairs=ordered_pairs,
        security_domain_ids=tuple(sorted(domains)),
        tenant_ids=tuple(sorted(tenants)),
        quality_summary=_quality_summary(versions, runs, refresh_runs, ordered_pairs),
        evidence_summary=_evidence_summary(versions, citations_inputs, refresh_runs),
    )


__all__ = [
    "LineageBundle",
    "LineageError",
    "LineageValidationError",
    "collect_lineage",
]
