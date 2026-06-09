"""v2 Ontology Mapping API — 含 Link Mapping 手动配置"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional
from app.database import SessionLocal
from app.deps import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class SuggestRequest(BaseModel):
    dataset_name: str
    columns: list[str]
    sample_rows: list[dict] = []
    ontology_domain: str = ""


class CreateMappingRequest(BaseModel):
    curated_dataset_id: str
    entity_class: str
    field_mapping: dict
    primary_key_column: Optional[str] = None
    property_mappings: Optional[list[dict]] = None
    confidence: float = 1.0


@router.post("/{ontology_id}/mappings/suggest")
def suggest_mapping(ontology_id: str, body: SuggestRequest, db: Session = Depends(get_db)):
    from app.services.v2.mapping.auto_mapper import AutoMapper
    mapper = AutoMapper(db)
    suggestion = mapper.suggest_field_mapping(
        body.dataset_name, body.columns, body.sample_rows, body.ontology_domain
    )
    return {
        "entity_class": suggestion.entity_class,
        "entity_class_cn": suggestion.entity_class_cn,
        "description": suggestion.description,
        "primary_key_column": suggestion.primary_key_column,
        "field_mappings": [
            {
                "column_name": fm.column_name,
                "property_name": fm.property_name,
                "property_type": fm.property_type,
                "confidence": fm.confidence,
                "reason": fm.reason,
            }
            for fm in suggestion.field_mappings
        ],
    }


@router.post("/{ontology_id}/mappings")
def create_mapping(ontology_id: str, body: CreateMappingRequest, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    svc = MappingService(db)
    field_mapping = dict(body.field_mapping or {})
    if body.property_mappings:
        field_mapping["__properties__"] = body.property_mappings
    mapping = svc.create_mapping(
        ontology_id=ontology_id,
        curated_dataset_id=body.curated_dataset_id,
        entity_class=body.entity_class,
        field_mapping=field_mapping,
        primary_key_column=body.primary_key_column,
        confidence=body.confidence,
    )
    return {"mapping_id": mapping.id, "status": mapping.status}


@router.get("/{ontology_id}/mappings")
def list_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.dataset import Dataset
    from app.models.v2.curated import CuratedDataset
    svc = MappingService(db)
    mappings = svc.get_mappings(ontology_id)
    result = []
    for m in mappings:
        dataset_name = None
        row_count = None
        if m.curated_dataset_id:
            ds = db.query(Dataset).filter(Dataset.id == m.curated_dataset_id).first()
            if ds:
                dataset_name = ds.name
            else:
                cd = db.query(CuratedDataset).filter(CuratedDataset.id == m.curated_dataset_id).first()
                if cd:
                    dataset_name = cd.name
            from app.models.v2.dataset import DatasetVersion
            ver = db.query(DatasetVersion).filter(
                DatasetVersion.dataset_id == m.curated_dataset_id
            ).order_by(DatasetVersion.version_no.desc()).first()
            if ver:
                row_count = ver.rowcount
        result.append({
            "id": m.id,
            "curated_dataset_id": m.curated_dataset_id,
            "dataset_name": dataset_name,
            "row_count": row_count,
            "entity_class": m.entity_class,
            "field_mapping": m.field_mapping,
            "property_mappings": (m.field_mapping or {}).get("__properties__", []),
            "status": m.status,
            "confidence": m.confidence,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    return result


@router.post("/{ontology_id}/mappings/{mapping_id}/apply")
def apply_mapping(ontology_id: str, mapping_id: str, data: list[dict], db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    svc = MappingService(db)
    result = svc.apply_mapping(mapping_id, data)
    return result


@router.post("/{ontology_id}/mappings/{mapping_id}/apply-from-dataset")
def apply_mapping_from_dataset(ontology_id: str, mapping_id: str, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyMapping
    from app.services.v2.mapping.mapping_service import MappingService
    from app.services.v2.dataset_service import DatasetService

    mapping = db.query(OntologyMapping).filter(
        OntologyMapping.id == mapping_id,
        OntologyMapping.ontology_id == ontology_id,
    ).first()
    if not mapping:
        raise HTTPException(404, "Mapping not found")
    if not mapping.curated_dataset_id:
        raise HTTPException(400, "Mapping has no curated_dataset_id")

    try:
        ds_svc = DatasetService(db)
        data = ds_svc.preview(mapping.curated_dataset_id, 1, limit=10000)
    except Exception as e:
        raise HTTPException(500, f"Failed to read curated dataset: {e}")

    svc = MappingService(db)
    result = svc.apply_mapping(mapping_id, data)
    return result


@router.post("/{ontology_id}/mappings/build-all")
def build_all_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.services.v2.mapping.mapping_service import MappingService
    from app.models.v2.mapping import OntologyLinkMapping
    svc = MappingService(db)
    try:
        result = svc.build_all(ontology_id)
        active_links = db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "active",
        ).count()
        inferred_links = db.query(OntologyLinkMapping).filter(
            OntologyLinkMapping.ontology_id == ontology_id,
            OntologyLinkMapping.status == "inferred",
        ).count()
        result["link_mappings_configured"] = active_links
        result["link_mappings_inferred"] = inferred_links
        return result
    except Exception as e:
        raise HTTPException(500, detail=str(e))


class LinkMappingCreate(BaseModel):
    src_dataset_id: str
    tgt_dataset_id: str
    relation_type: str
    src_key: str
    tgt_key: str


@router.post("/{ontology_id}/link-mappings")
def create_link_mapping(ontology_id: str, body: LinkMappingCreate, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyLinkMapping
    from app.services.v2.dataset_service import DatasetService

    svc = DatasetService(db)
    try:
        src_rows = svc.preview(body.src_dataset_id, 1, limit=10000)
        tgt_rows = svc.preview(body.tgt_dataset_id, 1, limit=10000)
    except Exception as e:
        raise HTTPException(400, f"Failed to preview datasets for link mapping: {e}")
    if not src_rows:
        raise HTTPException(400, "Source dataset has no rows")
    if not tgt_rows:
        raise HTTPException(400, "Target dataset has no rows")
    if body.src_key not in src_rows[0]:
        raise HTTPException(400, f"Source key '{body.src_key}' not found in source dataset")
    if body.tgt_key not in tgt_rows[0]:
        raise HTTPException(400, f"Target key '{body.tgt_key}' not found in target dataset")

    tgt_values = {str(row.get(body.tgt_key, "")).strip() for row in tgt_rows if row.get(body.tgt_key)}
    match_count = sum(1 for row in src_rows if str(row.get(body.src_key, "")).strip() in tgt_values)
    if match_count == 0:
        raise HTTPException(400, "Link mapping produced 0 matches; choose different source/target keys")

    lm = OntologyLinkMapping(
        ontology_id=ontology_id,
        src_dataset_id=body.src_dataset_id,
        tgt_dataset_id=body.tgt_dataset_id,
        relation_type=body.relation_type,
        src_key=body.src_key,
        tgt_key=body.tgt_key,
        status="active",
    )
    db.add(lm)
    db.commit()
    db.refresh(lm)
    return {"link_mapping_id": lm.id, "relation_type": lm.relation_type, "match_count": match_count}


@router.get("/{ontology_id}/link-mappings")
def list_link_mappings(ontology_id: str, db: Session = Depends(get_db)):
    from app.models.v2.mapping import OntologyLinkMapping
    links = db.query(OntologyLinkMapping).filter(
        OntologyLinkMapping.ontology_id == ontology_id
    ).all()
    return [{
        "id": l.id, "src_dataset_id": l.src_dataset_id, "tgt_dataset_id": l.tgt_dataset_id,
        "relation_type": l.relation_type, "src_key": l.src_key, "tgt_key": l.tgt_key,
        "status": l.status,
    } for l in links]
