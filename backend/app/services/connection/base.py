"""Connector 抽象基类 and the shared incremental page contract."""
from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from app.schemas.refresh import ChangeEnvelope, SourceCursor


@dataclass(frozen=True)
class DeltaPage:
    """One bounded incremental read from a source.

    ``cursor_outcome`` is ``"advanced"`` only when the source supplied a
    valid cursor contract and a candidate position.  Full-batch fallbacks
    deliberately keep it ``"unchanged"`` so the polling service never
    guesses a watermark from an arbitrary row.
    """

    envelopes: Sequence[ChangeEnvelope]
    candidate_cursor: SourceCursor
    source_observed_at: datetime | None
    source_lag_seconds: float | None
    cursor_outcome: str = "advanced"


def _empty_cursor(*, source_id: str, resource: str, contract: str,
                  previous: SourceCursor | None = None) -> SourceCursor:
    if previous is not None:
        return previous
    return SourceCursor(
        source_id=source_id,
        resource=resource,
        contract=contract,
        watermark=None,
        primary_key=None,
        opaque_value=None,
        observed_at=None,
    )


def _stringify(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _event_identity(raw: Mapping[str, object], *, source_id: str, resource: str,
                    primary_key: str | None, watermark: str | None,
                    opaque_value: str | None) -> str:
    for key in ("event_id", "eventId", "change_id", "id"):
        value = raw.get(key)
        if value is not None and str(value):
            return str(value)
    material = json.dumps(
        {
            "source_id": source_id,
            "resource": resource,
            "primary_key": primary_key,
            "watermark": watermark,
            "opaque_value": opaque_value,
            "row": dict(raw),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def records_to_delta_page(
    records: Sequence[Mapping[str, object]], *, source_id: str, resource: str,
    contract: str, cursor: SourceCursor | None, primary_key_field: str = "id",
    watermark_field: str = "updated_at", cursor_field: str = "cursor",
    observed_at: datetime | None = None, next_cursor: object = None,
    cursor_outcome: str = "advanced",
) -> DeltaPage:
    """Normalize connector records into the common ``ChangeEnvelope`` page.

    Connectors use this helper after they have validated their server-owned
    cursor fields.  The helper is intentionally conservative: missing cursor
    values produce envelopes but never fabricate a candidate position.
    """
    envelopes: list[ChangeEnvelope] = []
    candidate: tuple[str, str] | None = None
    candidate_opaque: str | None = _stringify(next_cursor)
    observed_values: list[datetime] = []

    for raw in records:
        row = dict(raw)
        primary_key = _stringify(row.get(primary_key_field))
        if primary_key is None:
            primary_key = _stringify(row.get("_id")) or _stringify(row.get("id"))
        watermark = _stringify(row.get(watermark_field))
        opaque_value = _stringify(row.get(cursor_field))
        if opaque_value is None:
            opaque_value = _stringify(row.get("_id"))
        occurred_at = _as_datetime(row.get(watermark_field) or row.get("occurred_at"))
        if occurred_at is not None:
            observed_values.append(occurred_at)
        event_id = _event_identity(
            row, source_id=source_id, resource=resource,
            primary_key=primary_key, watermark=watermark,
            opaque_value=opaque_value,
        )
        operation = str(row.get("operation") or "upsert")
        if operation not in ("upsert", "delete"):
            operation = "upsert"
        payload = {
            key: value for key, value in row.items()
            if key not in {"event_id", "eventId", "change_id", "operation"}
        }
        envelopes.append(ChangeEnvelope(
            event_id=event_id,
            source_id=source_id,
            resource=resource,
            operation=operation,
            primary_key=primary_key or event_id,
            payload=payload,
            watermark=watermark,
            source_cursor=opaque_value if contract == "opaque_source_cursor" else None,
            schema_hash=_stringify(row.get("schema_hash")),
            occurred_at=occurred_at,
            received_at=datetime.now(timezone.utc),
        ))
        if contract == "watermark_primary_key" and watermark is not None:
            key = (watermark, primary_key or "")
            if candidate is None or key > candidate:
                candidate = key
        elif contract == "opaque_source_cursor" and opaque_value is not None:
            if candidate_opaque is None or opaque_value > candidate_opaque:
                candidate_opaque = opaque_value

    source_seen = max(observed_values) if observed_values else observed_at
    if source_seen is not None and source_seen.tzinfo is None:
        source_seen = source_seen.replace(tzinfo=timezone.utc)
    lag = None
    if source_seen is not None:
        lag = max(0.0, (datetime.now(timezone.utc) - source_seen).total_seconds())

    if cursor_outcome == "unchanged":
        candidate_cursor = _empty_cursor(
            source_id=source_id, resource=resource, contract=contract, previous=cursor,
        )
    elif contract == "watermark_primary_key" and candidate is not None:
        candidate_cursor = SourceCursor(
            source_id=source_id, resource=resource, contract=contract,
            watermark=candidate[0], primary_key=candidate[1], opaque_value=None,
            observed_at=source_seen,
        )
    elif contract == "opaque_source_cursor" and candidate_opaque is not None:
        candidate_cursor = SourceCursor(
            source_id=source_id, resource=resource, contract=contract,
            watermark=None, primary_key=None, opaque_value=candidate_opaque,
            observed_at=source_seen,
        )
    else:
        candidate_cursor = _empty_cursor(
            source_id=source_id, resource=resource, contract=contract, previous=cursor,
        )
        cursor_outcome = "unchanged"

    return DeltaPage(
        envelopes=tuple(envelopes), candidate_cursor=candidate_cursor,
        source_observed_at=source_seen, source_lag_seconds=lag,
        cursor_outcome=cursor_outcome,
    )


class ConnectorBase(ABC):
    """所有 Connector 必须实现的接口"""

    @abstractmethod
    def test_connection(self) -> bool:
        """连接测试。成功返回 True，失败返回 False 或抛异常。"""
        ...

    @abstractmethod
    def list_resources(self) -> list[str]:
        """可用资源列表 (表名、集合名、端点等)。"""
        ...

    @abstractmethod
    def pull_sample(self, resource: str, limit: int = 100) -> list[dict]:
        """查询样本数据 (最多 limit 行)。"""
        ...

    @abstractmethod
    def pull_full(self, resource: str) -> Any:
        """返回全量数据。大数据量可返回生成器或文件路径。"""
        ...

    def pull_delta(
        self, resource: str, *, cursor: SourceCursor | None = None,
        overlap_window: timedelta = timedelta(0), since: str | None = None,
    ) -> DeltaPage:
        """Incremental read boundary.

        A connector that cannot honor a cursor contract returns an explicit
        full batch with an unchanged cursor.  The legacy ``since`` keyword is
        accepted only for compatibility with older callers; it is not used to
        infer a cursor.
        """
        del overlap_window, since
        config = getattr(self, "_config", {}) or {}
        contract = str(config.get("cursor_contract") or (cursor.contract if cursor else "watermark_primary_key"))
        rows = self.pull_full(resource)
        if isinstance(rows, Mapping):
            rows = [rows]
        elif isinstance(rows, (bytes, bytearray)):
            rows = [{"content": bytes(rows)}]
        else:
            rows = list(rows or [])
        return records_to_delta_page(
            rows,
            source_id=str(config.get("source_id") or ""),
            resource=resource,
            contract=contract,
            cursor=cursor,
            primary_key_field=str(config.get("primary_key_column") or "id"),
            watermark_field=str(config.get("watermark_column") or "updated_at"),
            cursor_field=str(config.get("cursor_field") or "cursor"),
            cursor_outcome="unchanged",
        )
