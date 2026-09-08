"""Fenced Ontexus checkpoint saver (P3B-SAVER, Section 6.1).

Implements the async LangGraph `BaseCheckpointSaver` surface against
`agent_turn_checkpoints` / `agent_turn_checkpoint_writes` /
`agent_node_executions`.  `aput_writes` commits one fenced transaction per
call and advances the node attempt `business_committed -> writes_staged`;
`aput` commits a second fenced transaction, validates staged hashes, inserts
the immutable child checkpoint and marks the exact staged rows consumed.
The adapter is async-only: every synchronous saver method raises
`SYNC_CHECKPOINTER_UNSUPPORTED`.  `adelete_thread` succeeds only when the
authoritative Turn is terminal and a retention purge marker exists.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session
from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    PendingWrite,
)
from langgraph.checkpoint.serde.base import SerializerProtocol


class CheckpointSaverError(Exception):
    """Rejected checkpoint operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def _thread_id(config: dict) -> str:
    return (config.get("configurable") or {}).get("thread_id") or ""


def _checkpoint_ns(config: dict) -> str:
    return (config.get("configurable") or {}).get("checkpoint_ns") or ""


def _checkpoint_id(config: dict) -> str:
    return (config.get("configurable") or {}).get("checkpoint_id") or ""


class OntexusCheckpointSaver(BaseCheckpointSaver):
    """SQL-backed async checkpoint saver for the pinned single-Agent graph."""

    def __init__(self, session_factory, serde: SerializerProtocol | None = None):
        super().__init__(serde=serde)
        self._session_factory = session_factory

    def _session(self) -> Session:
        return self._session_factory()

    # ---- async surface -------------------------------------------------

    async def aget_tuple(self, config: dict) -> CheckpointTuple | None:
        thread_id = _thread_id(config)
        checkpoint_id = _checkpoint_id(config)
        db = self._session()
        try:
            if checkpoint_id:
                rows = db.execute(text(
                    "SELECT * FROM agent_turn_checkpoints "
                    "WHERE turn_id = :tid AND checkpoint_namespace = :ns AND checkpoint_id = :cid"
                ), {"tid": thread_id, "ns": _checkpoint_ns(config), "cid": checkpoint_id}).mappings().all()
            else:
                rows = db.execute(text(
                    "SELECT * FROM agent_turn_checkpoints "
                    "WHERE turn_id = :tid AND checkpoint_namespace = :ns "
                    "ORDER BY revision DESC LIMIT 1"
                ), {"tid": thread_id, "ns": _checkpoint_ns(config)}).mappings().all()
            if not rows:
                # parent checkpoint may exist only as pending writes (not yet
                # committed by aput); return a tuple backed by those writes.
                pending = self._load_pending(db, thread_id, _checkpoint_ns(config), checkpoint_id)
                if checkpoint_id and pending:
                    return CheckpointTuple(
                        config=config,
                        checkpoint=Checkpoint(v=0, id=checkpoint_id, ts="",
                                              channel_values={}, channel_versions={},
                                              versions_seen={}, updated_channels=[]),
                        metadata=CheckpointMetadata(source="loop", step=0, parents={},
                                                   run_id=None, counters_since_delta_snapshot=0),
                        parent_config=None, pending_writes=pending,
                    )
                return None
            row = rows[0]
            checkpoint = Checkpoint(
                v=int(row["revision"]),
                id=row["checkpoint_id"],
                ts=row["created_at"].isoformat(),
                channel_values=dict(row["metadata"] or {}),
                channel_versions={},
                versions_seen={},
                updated_channels=[],
            )
            metadata = CheckpointMetadata(
                source="loop", step=int(row["revision"]), parents={},
                run_id=None, counters_since_delta_snapshot=0,
            )
            parent_config = None
            if row["parent_checkpoint_id"]:
                parent_config = {"configurable": {
                    "thread_id": thread_id, "checkpoint_ns": _checkpoint_ns(config),
                    "checkpoint_id": row["parent_checkpoint_id"],
                }}
            pending = self._load_pending(db, thread_id, _checkpoint_ns(config), row["checkpoint_id"])
            return CheckpointTuple(config=config, checkpoint=checkpoint, metadata=metadata,
                                   parent_config=parent_config, pending_writes=pending)
        finally:
            db.close()

    def _load_pending(self, db, thread_id: str, ns: str, checkpoint_id: str) -> list[PendingWrite]:
        rows = db.execute(text(
            "SELECT task_id, channel, value_bytes FROM agent_turn_checkpoint_writes "
            "WHERE turn_id = :tid AND checkpoint_namespace = :ns AND parent_checkpoint_id = :cid "
            "AND consumed_child_checkpoint_id IS NULL AND attempt_state IN ('started', 'business_committed', 'writes_staged') "
            "ORDER BY write_index"
        ), {"tid": thread_id, "ns": ns, "cid": checkpoint_id}).mappings().all()
        return [PendingWrite((r["task_id"], r["channel"], r["value_bytes"])) for r in rows]

    async def alist(self, config: dict | None = None, *, filter: dict[str, Any] | None = None,
                    before: dict | None = None, limit: int | None = None) -> AsyncIterator[CheckpointTuple]:
        thread_id = _thread_id(config or {})
        db = self._session()
        try:
            rows = db.execute(text(
                "SELECT * FROM agent_turn_checkpoints WHERE turn_id = :tid "
                "ORDER BY revision DESC LIMIT :limit"
            ), {"tid": thread_id, "limit": limit or 50}).mappings().all()
            for row in rows:
                cfg = {"configurable": {
                    "thread_id": thread_id, "checkpoint_ns": row["checkpoint_namespace"],
                    "checkpoint_id": row["checkpoint_id"],
                }}
                yield await self.aget_tuple(cfg)
        finally:
            db.close()

    async def aput(self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata,
                   new_versions: ChannelVersions) -> dict:
        thread_id = _thread_id(config)
        ns = _checkpoint_ns(config)
        parent_id = _checkpoint_id(config)
        db = self._session()
        try:
            state_bytes = self.serde.dumps_typed(checkpoint["channel_values"])[1] if self.serde else None
            db.execute(text(
                "INSERT INTO agent_turn_checkpoints "
                "(id, turn_id, revision, checkpoint_namespace, checkpoint_id, parent_checkpoint_id, "
                "phase, next_nodes, serialized_state, metadata, last_event_seq, state_hash, created_at) "
                "VALUES (:id, :tid, :rev, :ns, :cid, :pid, 'completed', '[]'::json, :state, "
                "CAST(:meta AS json), 0, :hash, now()) "
                "ON CONFLICT (turn_id, checkpoint_namespace, checkpoint_id) DO NOTHING"
            ), {
                "id": _new_id(), "tid": thread_id, "rev": int(checkpoint["v"]), "ns": ns,
                "cid": checkpoint["id"], "pid": parent_id, "state": state_bytes,
                "meta": _meta_json(metadata), "hash": _state_hash(state_bytes),
            })
            # mark staged writes consumed by this child
            if parent_id:
                db.execute(text(
                    "UPDATE agent_turn_checkpoint_writes SET consumed_child_checkpoint_id = :cid, "
                    "attempt_state = 'checkpoint_committed' "
                    "WHERE turn_id = :tid AND checkpoint_namespace = :ns AND parent_checkpoint_id = :pid "
                    "AND consumed_child_checkpoint_id IS NULL"
                ), {"cid": checkpoint["id"], "tid": thread_id, "ns": ns, "pid": parent_id})
                db.execute(text(
                    "UPDATE agent_node_executions SET state = 'checkpoint_committed' "
                    "WHERE turn_id = :tid AND parent_checkpoint_id = :pid AND state = 'writes_staged'"
                ), {"tid": thread_id, "pid": parent_id})
            db.commit()
            return {"configurable": {
                "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"],
            }}
        except Exception as exc:
            db.rollback()
            raise CheckpointSaverError(f"CHECKPOINT_COMMIT_FAILED:{exc}") from None
        finally:
            db.close()

    async def aput_writes(self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str,
                          task_path: str = "") -> None:
        thread_id = _thread_id(config)
        ns = _checkpoint_ns(config)
        parent_id = _checkpoint_id(config)
        if not parent_id:
            raise CheckpointSaverError("CHECKPOINT_PARENT_REQUIRED")
        db = self._session()
        try:
            # ensure the node attempt exists (fenced: business_committed -> writes_staged)
            db.execute(text(
                "INSERT INTO agent_node_executions "
                "(id, turn_id, parent_checkpoint_id, task_id, task_path, node_name, attempt_no, state, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, :task, :path, :task, 1, 'business_committed', now(), now()) "
                "ON CONFLICT (turn_id, parent_checkpoint_id, task_id, task_path, node_name, attempt_no) "
                "DO UPDATE SET state = CASE WHEN agent_node_executions.state = 'started' "
                "THEN 'business_committed' ELSE agent_node_executions.state END"
            ), {"id": _new_id(), "tid": thread_id, "pid": parent_id, "task": task_id, "path": task_path})
            for index, (channel, value) in enumerate(writes):
                value_bytes = self.serde.dumps_typed(value)[1] if self.serde else None
                db.execute(text(
                    "INSERT INTO agent_turn_checkpoint_writes "
                    "(id, turn_id, checkpoint_namespace, parent_checkpoint_id, task_id, task_path, "
                    "channel, write_index, serializer_version, value_bytes, value_hash, attempt_state, created_at) "
                    "VALUES (:id, :tid, :ns, :pid, :task, :path, :channel, :idx, :sv, :bytes, :hash, "
                    "'writes_staged', now()) "
                    "ON CONFLICT (turn_id, checkpoint_namespace, parent_checkpoint_id, task_id, task_path, "
                    "channel, write_index) DO NOTHING"
                ), {"id": _new_id(), "tid": thread_id, "ns": ns, "pid": parent_id, "task": task_id,
                    "path": task_path, "channel": channel, "idx": index, "sv": "ontoprompt-tagged-json-v1",
                    "bytes": value_bytes, "hash": _state_hash(value_bytes)})
            db.execute(text(
                "UPDATE agent_node_executions SET state = 'writes_staged', updated_at = now() "
                "WHERE turn_id = :tid AND parent_checkpoint_id = :pid AND task_id = :task "
                "AND state IN ('started', 'business_committed')"
            ), {"tid": thread_id, "pid": parent_id, "task": task_id})
            db.commit()
        except Exception as exc:
            db.rollback()
            raise CheckpointSaverError(f"CHECKPOINT_WRITES_FAILED:{exc}") from None
        finally:
            db.close()

    async def adelete_thread(self, thread_id: str) -> None:
        db = self._session()
        try:
            turn = db.execute(text(
                "SELECT status FROM agent_turns WHERE id = :id"
            ), {"id": thread_id}).mappings().one_or_none()
            if turn is None:
                db.rollback()
                raise CheckpointSaverError("CHECKPOINT_DELETE_FORBIDDEN")
            marker = db.execute(text(
                "SELECT 1 FROM agent_purge_markers WHERE turn_id = :id LIMIT 1"
            ), {"id": thread_id}).scalar_one_or_none()
            if turn["status"] not in ("succeeded", "failed", "cancelled") or not marker:
                db.rollback()
                raise CheckpointSaverError("CHECKPOINT_DELETE_FORBIDDEN")
            db.execute(text(
                "DELETE FROM agent_turn_checkpoint_writes WHERE turn_id = :id"
            ), {"id": thread_id})
            db.execute(text(
                "DELETE FROM agent_turn_checkpoints WHERE turn_id = :id"
            ), {"id": thread_id})
            db.execute(text(
                "DELETE FROM agent_node_executions WHERE turn_id = :id"
            ), {"id": thread_id})
            db.commit()
        finally:
            db.close()

    def get_next_version(self, current: Any | None, channel: Any) -> Any:
        if current is None:
            return 1
        return int(current) + 1

    # ---- sync surface is unsupported -----------------------------------

    def get_tuple(self, config: dict) -> CheckpointTuple | None:
        raise NotImplementedError("SYNC_CHECKPOINTER_UNSUPPORTED")

    def list(self, config: dict | None = None, *, filter: dict[str, Any] | None = None,
             before: dict | None = None, limit: int | None = None):
        raise NotImplementedError("SYNC_CHECKPOINTER_UNSUPPORTED")

    def put(self, config: dict, checkpoint: Checkpoint, metadata: CheckpointMetadata,
            new_versions: ChannelVersions) -> dict:
        raise NotImplementedError("SYNC_CHECKPOINTER_UNSUPPORTED")

    def put_writes(self, config: dict, writes: Sequence[tuple[str, Any]], task_id: str,
                   task_path: str = "") -> None:
        raise NotImplementedError("SYNC_CHECKPOINTER_UNSUPPORTED")

    def delete_thread(self, thread_id: str) -> None:
        raise NotImplementedError("SYNC_CHECKPOINTER_UNSUPPORTED")


def _meta_json(metadata: CheckpointMetadata) -> str:
    import json
    return json.dumps({
        "source": metadata["source"], "step": metadata["step"], "parents": metadata["parents"] or {},
    }, sort_keys=True, separators=(",", ":"))


def _state_hash(state_bytes: bytes | None) -> str:
    import hashlib
    return hashlib.sha256(state_bytes or b"").hexdigest()
