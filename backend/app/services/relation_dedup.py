"""Shared relation-edge dedup: any reader of the `relations` table should use
this so parallel-evidence rows never leak through as visible duplicates."""


def dedupe_relation_rows(relations) -> list:
    """Same collapse as `dedupe_relation_edges`, but returns the winning
    `Relation` ORM row itself (one per (source, target, type) triple) instead
    of a rendered dict — for consumers (exports, listings) that expect to
    keep attribute access on real `Relation` objects."""
    grouped: dict[tuple[str, str, str], list] = {}
    for r in relations:
        grouped.setdefault((r.source_entity, r.target_entity, r.type or "RELATED"), []).append(r)
    return [max(rows, key=lambda r: r.confidence or 0.0) for rows in grouped.values()]


def dedupe_relation_edges(relations) -> list[dict]:
    """Collapse multiple Relation rows sharing (source, target, type) into one
    rendered edge. Concept-level relations can be independently evidenced by
    several source columns (e.g. both `supplier.id` and `supplier.name`
    resolving to the same Supplier) — each evidence row is preserved in the
    database for audit, but rendering one edge per row makes any relation view
    show redundant parallel edges between the same two nodes. The
    highest-confidence row's id/properties are kept as the edge's identity;
    every contributing column is recorded under `properties.fk_columns`."""
    grouped: dict[tuple[str, str, str], list] = {}
    for r in relations:
        grouped.setdefault((r.source_entity, r.target_entity, r.type or "RELATED"), []).append(r)

    edges: list[dict] = []
    for (source, target, rel_type), rows in grouped.items():
        best = max(rows, key=lambda r: r.confidence or 0.0)
        fk_columns = sorted({
            (r.properties or {}).get("fk_column")
            for r in rows
            if (r.properties or {}).get("fk_column")
        })
        properties = dict(best.properties or {})
        if len(rows) > 1:
            properties["fk_columns"] = fk_columns
            properties["evidence_count"] = len(rows)
        edges.append({
            "id": best.id,
            "source": source,
            "target": target,
            "type": rel_type,
            "properties": properties,
        })
    return edges
