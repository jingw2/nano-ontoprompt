"""Persisted Agent runtime event stream (P4A-STREAM).

Events are persisted before they are notified; SSE replays from an
`after_seq` cursor with no gaps or duplicates.  A terminal event ends the
stream; missing sequences fail closed rather than silently skipping.  Stream
secrets are never exposed here (the stream-ticket endpoint owns them).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

TERMINAL_EVENT_TYPES = frozenset({"turn_succeeded", "turn_failed", "turn_cancelled"})


class EventStreamError(Exception):
    """Rejected event stream operation."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


def append_event(db: Session, *, turn_id: str, event_type: str, payload: dict | None = None) -> dict:
    """Persist one runtime event with the next monotonic sequence for the
    Turn; returns the stored row (persisted-before-notify)."""
    sequence = db.execute(text(
        "SELECT coalesce(max(sequence), 0) + 1 FROM agent_runtime_events WHERE turn_id = :id"
    ), {"id": turn_id}).scalar_one()
    row_id = _new_id()
    import json as _json
    db.execute(text(
        "INSERT INTO agent_runtime_events (id, turn_id, sequence, event_type, payload, created_at) "
        "VALUES (:id, :turn, :seq, :type, CAST(:payload AS json), now())"
    ), {"id": row_id, "turn": turn_id, "seq": sequence, "type": event_type,
        "payload": _json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)})
    db.commit()
    return {"id": row_id, "turn_id": turn_id, "sequence": sequence,
            "event_type": event_type, "payload": payload or {}, "created_at": _now()}


def list_events(db: Session, *, turn_id: str, after_seq: int | None = None,
                limit: int = 100) -> dict:
    """Cursor page of persisted events ordered by sequence (no gaps)."""
    params: dict = {"turn_id": turn_id, "limit": limit + 1}
    after_clause = ""
    if after_seq is not None:
        after_clause = "AND sequence > :after"
        params["after"] = after_seq
    rows = db.execute(text(
        "SELECT id, turn_id, sequence, event_type, payload, created_at "
        "FROM agent_runtime_events WHERE turn_id = :turn_id " + after_clause + " "
        "ORDER BY sequence LIMIT :limit"
    ), params).mappings().all()
    has_more = len(rows) > limit
    page = rows[:limit]
    terminal = False
    if page and page[-1]["event_type"] in TERMINAL_EVENT_TYPES:
        terminal = True
    return {
        "items": [dict(r) for r in page],
        "next_cursor": page[-1]["sequence"] if has_more else None,
        "has_more": has_more,
        "terminal": terminal,
    }


def verify_contiguous(db: Session, *, turn_id: str, after_seq: int | None = None) -> bool:
    """A replay must see exactly `after_seq + 1, +2, ...` — any gap means the
    persisted log diverged (fail closed)."""
    rows = db.execute(text(
        "SELECT sequence FROM agent_runtime_events WHERE turn_id = :id "
        "AND sequence > :after ORDER BY sequence"
    ), {"id": turn_id, "after": after_seq or 0}).scalars().all()
    expected = (after_seq or 0) + 1
    for seq in rows:
        if seq != expected:
            return False
        expected += 1
    return True


def stream_chunk(db: Session, *, turn_id: str, after_seq: int | None = None) -> list[dict]:
    """SSE payload chunk: persisted events after the cursor, terminal flag,
    and a gap indicator when sequences are not contiguous."""
    if not verify_contiguous(db, turn_id=turn_id, after_seq=after_seq):
        return [{"event": "gap", "data": "SEQUENCE_GAP"}]
    page = list_events(db, turn_id=turn_id, after_seq=after_seq, limit=100)
    events = page["items"]
    if not events:
        return []
    out = [{"event": e["event_type"], "data": e["payload"], "sequence": e["sequence"]}
           for e in events]
    if page["terminal"]:
        out.append({"event": "terminal", "data": {"turn_id": turn_id}})
    return out
