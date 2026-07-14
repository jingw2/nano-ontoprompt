"""
v2 모델 스키마 테스트 — 실제 DB 연결 없이 SQLAlchemy 모델 구조만 검증
"""
from app.models.v2.connection import Connection, ConnectionKind
from app.models.v2.dataset import Dataset, DatasetVersion, MediaItem
from app.models.v2.pipeline import Pipeline, PipelineRun
from app.models.v2.curated import CuratedDataset, CuratedReview, CuratedRowEdit
from app.models.v2.mapping import OntologyMapping, OntologyLinkMapping


def test_connection_model_tablename():
    assert Connection.__tablename__ == "v2_connections"


def test_dataset_model_tablename():
    assert Dataset.__tablename__ == "v2_datasets"


def test_dataset_version_model_tablename():
    assert DatasetVersion.__tablename__ == "v2_dataset_versions"


def test_media_item_model_tablename():
    assert MediaItem.__tablename__ == "v2_media_items"


def test_pipeline_model_tablename():
    assert Pipeline.__tablename__ == "v2_pipelines"


def test_pipeline_run_model_tablename():
    assert PipelineRun.__tablename__ == "v2_pipeline_runs"


def test_curated_dataset_model_tablename():
    assert CuratedDataset.__tablename__ == "v2_curated_datasets"


def test_curated_review_model_tablename():
    assert CuratedReview.__tablename__ == "v2_curated_reviews"


def test_curated_row_edit_model_tablename():
    assert CuratedRowEdit.__tablename__ == "v2_curated_row_edits"


def test_ontology_mapping_model_tablename():
    assert OntologyMapping.__tablename__ == "v2_ontology_mappings"


def test_ontology_link_mapping_model_tablename():
    assert OntologyLinkMapping.__tablename__ == "v2_ontology_link_mappings"


def test_all_v2_tables_registered():
    """Base.metadata에 v2 테이블이 모두 등록되어 있는지 확인"""
    from app.database import Base
    v2_tables = {t for t in Base.metadata.tables.keys() if t.startswith("v2_")}
    assert v2_tables == {
        "v2_connections",
        "v2_datasets",
        "v2_dataset_versions",
        "v2_media_items",
        "v2_pipelines",
        "v2_pipeline_versions",
        "v2_pipeline_runs",
        "v2_curated_datasets",
        "v2_curated_reviews",
        "v2_curated_row_edits",
        "v2_ontology_logic_rules",
        "v2_ontology_state_machines",
        "v2_ontology_action_types",
        "v2_ontology_action_runs",
        "v2_ontology_mappings",
        "v2_ontology_link_mappings",
    }


def test_connection_kind_enum_values():
    assert set(k.value for k in ConnectionKind) == {"file", "mysql", "postgres", "mongo", "rest"}


def test_pipeline_route_field_exists():
    cols = [c.name for c in Pipeline.__table__.columns]
    assert "route" in cols
    assert "spec" in cols
    assert "schedule_cron" in cols


def test_ontology_mapping_has_field_mapping():
    cols = [c.name for c in OntologyMapping.__table__.columns]
    assert "field_mapping" in cols
    assert "entity_class" in cols
    assert "confidence" in cols
