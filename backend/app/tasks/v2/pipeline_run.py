"""Pipeline 执行 Celery 任务 — 支持 DAG 编译 + 节点状态追踪"""
from __future__ import annotations
from datetime import datetime, timezone
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def get_celery_app():
    try:
        from app.tasks.extraction import celery_app
        return celery_app
    except Exception:
        return None


celery_app = get_celery_app()


def _init_node_status(definition: dict | None) -> dict[str, str]:
    """从 definition 中提取所有节点 ID，初始化为 'idle'"""
    if not definition:
        return {}
    nodes = definition.get("nodes", [])
    return {n["id"]: "idle" for n in nodes}


def _compute_quality_score(rows: list[dict], route: str, meta: dict) -> float:
    if not rows:
        return 0.0
    if route == "C":
        meta_fields = {"markdown_text", "filename", "source_file", "source_dataset_id",
                       "extraction_strategy", "extraction_method", "structured_extraction_ok",
                       "structured_extraction_error"}
        meaningful_fields = [k for k in rows[0].keys() if k not in meta_fields]
        total_fields = len(meaningful_fields) or 1
        filled = sum(1 for row in rows for k in meaningful_fields if row.get(k))
        completeness = filled / (len(rows) * total_fields) if total_fields > 0 else 0
        rule_bonus = min(0.2, int(rows[0].get("rule_count", 0)) * 0.02)
        return min(1.0, completeness + rule_bonus)
    rows_before = meta.get("rows_before", len(rows)) or len(rows)
    rows_after = meta.get("rows_after", len(rows)) or len(rows)
    retention = rows_after / rows_before if rows_before > 0 else 1.0
    total_cells = sum(len(r) for r in rows) or 1
    filled_cells = sum(1 for r in rows for v in r.values() if v is not None and str(v).strip() != "")
    fill_rate = filled_cells / total_cells
    return round(retention * 0.4 + fill_rate * 0.6, 3)


def _route_for_kind(kind: str | None, default_route: str | None = None) -> str:
    if default_route in ("A", "B", "C"):
        return default_route
    if kind == "semi":
        return "B"
    if kind == "unstructured":
        return "C"
    return "A"


def _transform_nodes(definition: dict | None) -> list[dict]:
    if not definition:
        return []
    return [n for n in definition.get("nodes") or [] if n.get("type") == "transform"]


def _route_from_transform_config(config: dict | None) -> str | None:
    path = (config or {}).get("path")
    if path == "structured":
        return "A"
    if path == "semi_structured":
        return "B"
    if path == "unstructured":
        return "C"
    if path == "wide_table":
        return "A"
    return None


def _merge_dict(base: dict, overlay: dict) -> dict:
    result = dict(base or {})
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_dict(result[key], value)
        else:
            result[key] = value
    return result


def _spec_from_transform_config(config: dict | None) -> dict:
    config = config or {}
    spec: dict = {}
    steps = config.get("steps") or []

    if config.get("engine"):
        spec["engine"] = config.get("engine")
    if config.get("path"):
        spec["path"] = config.get("path")

    for step in steps:
        op = step.get("op") if isinstance(step, dict) else None
        params = step.get("params") if isinstance(step, dict) else {}
        params = params or {}

        if op in ("parse_json", "flatten_json", "explode_array"):
            spec["format"] = "json"
            flatten = dict(spec.get("json_flatten") or {})
            if op == "explode_array":
                flatten["array_explode"] = True
            flatten.update(params)
            spec["json_flatten"] = flatten
        elif op == "parse_xml":
            spec["format"] = "xml"
        elif op in ("detect_wide_table", "suggest_split", "apply_split"):
            wide = dict(spec.get("wide_table_split") or {})
            wide["enabled"] = True
            if op in ("detect_wide_table", "suggest_split"):
                wide["suggest_only"] = True
            if op == "apply_split":
                wide["suggest_only"] = False
            wide.update(params)
            spec["wide_table_split"] = wide
        elif op in ("drop_duplicates", "drop_nulls", "fill_nulls", "normalize_dates"):
            cleansing = dict(spec.get("cleansing") or {})
            if op == "drop_duplicates":
                cleansing["deduplicate"] = True
            elif op == "drop_nulls":
                cleansing["null_strategy"] = "drop"
            elif op == "fill_nulls":
                cleansing["null_strategy"] = params.get("strategy", "fill_empty")
            elif op == "normalize_dates":
                cleansing["normalize_dates"] = True
            cleansing.update({k: v for k, v in params.items() if k != "strategy"})
            spec["cleansing"] = cleansing
        elif op == "document_to_markdown":
            doc = dict(spec.get("document_to_md") or {})
            doc["strategy"] = params.get("strategy", doc.get("strategy", "markitdown"))
            doc.update(params)
            spec["document_to_md"] = doc
        elif op == "ocr_extract":
            doc = dict(spec.get("document_to_md") or {})
            doc["strategy"] = "ocr"
            doc.update(params)
            spec["document_to_md"] = doc
        elif op == "vlm_extract":
            doc = dict(spec.get("document_to_md") or {})
            doc["strategy"] = "vlm"
            doc.update(params)
            spec["document_to_md"] = doc
        elif op == "llm_structurize":
            extract = dict(spec.get("md_to_structured") or {})
            extract["auto_extract"] = True
            extract.update(params)
            spec["md_to_structured"] = extract

    if config.get("path") == "wide_table":
        wide = dict(spec.get("wide_table_split") or {})
        wide.setdefault("enabled", True)
        wide.setdefault("suggest_only", False)
        spec["wide_table_split"] = wide
    return spec


def _pipeline_runtime_config(pl) -> tuple[str | None, dict]:
    transforms = _transform_nodes(pl.definition)
    route = None
    spec = dict(pl.spec or {})
    for node in transforms:
        cfg = node.get("config") or {}
        route = route or _route_from_transform_config(cfg)
        spec = _merge_dict(spec, _spec_from_transform_config(cfg))
    return route, spec


def _source_runtime_route(source: dict, transform_route: str | None, default_route: str | None) -> str:
    return transform_route or source.get("route") or _route_for_kind(source.get("kind"), default_route)


def _find_dataset_for_file(db, filename: str):
    from app.models.v2.dataset import Dataset, DatasetVersion

    stem = Path(filename).stem
    candidates = db.query(Dataset).filter(
        Dataset.name == stem
    ).order_by(Dataset.created_at.desc()).limit(20).all()
    for candidate in candidates:
        ver = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == candidate.id
        ).order_by(DatasetVersion.version_no.desc()).first()
        if ver and ((ver.rowcount or 0) > 0 or ver.storage_uri):
            return candidate
    return candidates[0] if candidates else None


def _collect_sources(db, pl) -> list[dict]:
    from app.models.v2.dataset import Dataset

    sources: list[dict] = []
    definition = pl.definition or {}
    for node in definition.get("nodes") or []:
        if node.get("type") != "connector":
            continue
        for file_info in (node.get("config") or {}).get("files", []) or []:
            filename = file_info.get("name") or file_info.get("filename") or ""
            dataset_id = file_info.get("dataset_id")
            ds = db.query(Dataset).filter(Dataset.id == dataset_id).first() if dataset_id else None
            if not ds and filename:
                ds = _find_dataset_for_file(db, filename)
            if ds:
                sources.append({
                    "dataset_id": ds.id,
                    "filename": filename or ds.name,
                    "route": _route_for_kind(ds.kind, None),
                    "kind": ds.kind,
                })

    if not sources and pl.source_dataset_id:
        ds = db.query(Dataset).filter(Dataset.id == pl.source_dataset_id).first()
        if ds:
            sources.append({
                "dataset_id": ds.id,
                "filename": ds.name,
                "route": _route_for_kind(ds.kind, pl.route),
                "kind": ds.kind,
            })

    # Preserve order while removing duplicate datasets.
    seen: set[str] = set()
    unique_sources = []
    for source in sources:
        if source["dataset_id"] in seen:
            continue
        seen.add(source["dataset_id"])
        unique_sources.append(source)
    return unique_sources


def _load_source_rows(db, svc, source: dict, limit: int = 10000) -> list[dict]:
    from app.models.v2.dataset import DatasetVersion

    if source["route"] == "C":
        ver = db.query(DatasetVersion).filter(
            DatasetVersion.dataset_id == source["dataset_id"]
        ).order_by(DatasetVersion.version_no.desc()).first()
        if not ver or not ver.storage_uri:
            return []
        raw = svc._storage.get_object(ver.storage_uri)
        return [{
            "filename": source["filename"],
            "content": raw,
            "storage_uri": ver.storage_uri,
            "source_dataset_id": source["dataset_id"],
        }]
    return svc.preview(source["dataset_id"], 1, limit=limit)


def _execute_route(route: str, ctx, data: list[dict]) -> tuple[list[dict], object]:
    from app.services.v2.pipeline.engine import execute_route_a, execute_route_b, execute_route_c

    if route == "B":
        return execute_route_b(ctx, data)
    if route == "C":
        return execute_route_c(ctx, data)
    return execute_route_a(ctx, data)


def _safe_csv_bytes(data: list[dict]) -> bytes:
    import csv
    import io
    import json as _json

    def _safe_str(v) -> str:
        if v is None:
            return ""
        if isinstance(v, bytes):
            return f"<{len(v)} bytes>"
        if isinstance(v, (dict, list)):
            return _json.dumps(v, ensure_ascii=False)
        return str(v)

    if not data:
        return b""

    all_keys: list[str] = []
    seen_keys: set[str] = set()
    for row in data:
        for key in row.keys():
            if key == "content":
                continue
            if key not in seen_keys:
                all_keys.append(key)
                seen_keys.add(key)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=all_keys, extrasaction="ignore", restval="")
    writer.writeheader()
    for row in data:
        writer.writerow({k: _safe_str(row.get(k)) for k in all_keys})
    return buf.getvalue().encode("utf-8")


def _save_curated_dataset(db, svc, pl, source: dict, data: list[dict], ctx, multi_source: bool, table_name: str | None = None) -> dict:
    stem = Path(source["filename"]).stem
    name_parts = [pl.name]
    if multi_source:
        name_parts.append(stem)
    if table_name:
        name_parts.append(table_name)
    name_parts.append("curated")
    ds_name = " ".join(name_parts)
    curated_ds = svc.create_dataset(name=ds_name, kind="curated")
    svc.create_version(curated_ds.id, _safe_csv_bytes(data), rowcount=len(data))

    if data:
        try:
            curated_ds.schema_json = curated_ds.schema_json or {}
            curated_ds.schema_json["quality_score"] = _compute_quality_score(data, source["route"], ctx.meta)
            curated_ds.schema_json["columns"] = [k for k in data[0].keys() if k != "content"]
            curated_ds.schema_json["route"] = source["route"]
            curated_ds.schema_json["source_dataset_id"] = source["dataset_id"]
            if table_name:
                curated_ds.schema_json["transform_output_table"] = table_name
            db.commit()
        except Exception:
            pass

    return {
        "source_dataset_id": source["dataset_id"],
        "source_file": source["filename"],
        "route": source["route"],
        "table_name": table_name,
        "curated_dataset_id": curated_ds.id,
        "rows_in": ctx.rows_in,
        "rows_out": len(data),
        "meta": ctx.meta,
    }


def _save_curated_outputs(db, svc, pl, source: dict, data: list[dict], ctx, multi_source: bool) -> list[dict]:
    split_tables = ctx.meta.get("split_tables")
    if isinstance(split_tables, dict) and split_tables:
        outputs = []
        for table_name, rows in split_tables.items():
            outputs.append(_save_curated_dataset(
                db, svc, pl, source, rows or [], ctx, multi_source=True, table_name=str(table_name)
            ))
        return outputs
    return [_save_curated_dataset(db, svc, pl, source, data, ctx, multi_source)]


def pipeline_run_task(pipeline_id: str, run_id: str):
    """Pipeline 执行任务 — 支持 DAG 编译 + 节点状态追踪"""
    from app.database import SessionLocal
    from app.models.v2.pipeline import Pipeline, PipelineRun
    from app.services.v2.pipeline.base import PipelineContext
    from app.services.v2.pipeline.dag_compiler import compile_definition
    from app.services.v2.dataset_service import DatasetService

    db = SessionLocal()
    try:
        run = db.query(PipelineRun).filter(PipelineRun.id == run_id).first()
        if not run:
            return
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        pl = db.query(Pipeline).filter(Pipeline.id == pipeline_id).first()
        if not pl:
            run.status = "failed"
            run.error_log = "Pipeline not found"
            db.commit()
            return

        # ── DAG 编译 ──────────────────────────────────────────────
        definition = pl.definition
        plan = compile_definition(definition)
        node_status = _init_node_status(definition)

        def set_node_status(nid: str, status: str):
            if nid in node_status:
                node_status[nid] = status
                # 每步更新持久化到 run 的临时字段
                run.stats = run.stats or {}
                run.stats["node_status"] = dict(node_status)
                db.commit()

        svc = DatasetService(db)
        sources = _collect_sources(db, pl)
        if not sources:
            raise ValueError("Pipeline has no source datasets")

        transform_route, runtime_spec = _pipeline_runtime_config(pl)

        if sources and not pl.source_dataset_id:
            pl.source_dataset_id = sources[0]["dataset_id"]
            db.commit()

        outputs = []
        multi_source = len(sources) > 1
        for source in sources:
            source["route"] = _source_runtime_route(source, transform_route, pl.route)
            data = _load_source_rows(db, svc, source)
            ctx = PipelineContext(
                dataset_id=source["dataset_id"],
                version_no=1,
                route=source["route"],
                spec=runtime_spec,
            )
            if source["route"] == "C":
                ctx.spec = dict(ctx.spec or {})
                ctx.spec["md_to_structured"] = {
                    "rule_based": True,
                    **(ctx.spec.get("md_to_structured") or {}),
                }
            ctx.rows_in = len(data)
            data, ctx = _execute_route(source["route"], ctx, data)
            ctx.rows_out = len(data)
            outputs.extend(_save_curated_outputs(db, svc, pl, source, data, ctx, multi_source))

        for nid in node_status:
            set_node_status(nid, "success")

        curated_ids = [o["curated_dataset_id"] for o in outputs]
        pl.target_curated_ids = curated_ids
        if len({s["route"] for s in sources}) == 1:
            pl.route = sources[0]["route"]
        else:
            pl.route = pl.route or sources[0]["route"] or "A"
        db.commit()

        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.stats = {
            "rows_in": sum(o["rows_in"] for o in outputs),
            "rows_out": sum(o["rows_out"] for o in outputs),
            "meta": {"outputs": outputs},
            "node_status": node_status,
            "curated_dataset_id": curated_ids[0] if curated_ids else None,
            "curated_dataset_ids": curated_ids,
        }
        db.commit()

    except Exception as e:
        logger.error(f"Pipeline run failed: {e}")
        if run:
            run.status = "failed"
            run.error_log = str(e)
            run.finished_at = datetime.now(timezone.utc)
            if run.stats is None:
                run.stats = {}
            if "node_status" not in run.stats:
                run.stats["node_status"] = {}
            db.commit()
    finally:
        db.close()


def _get_node_type(definition: dict | None, node_id: str) -> str:
    """从 definition 中获取节点类型"""
    if not definition:
        return ""
    for n in definition.get("nodes", []):
        if n.get("id") == node_id:
            return n.get("type", "")
    return ""


if celery_app:
    pipeline_run_task = celery_app.task(pipeline_run_task)
