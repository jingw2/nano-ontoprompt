"""FK 检测回归测试 — 点号列名(JSON flatten 产物)与中文列名"""
from app.services.v2.mapping.mapping_service import MappingService


def _svc() -> MappingService:
    return MappingService(db=None)


def test_dot_separated_fk_column_detected():
    """supplier.id (JSON flatten 产物) 应识别为指向 Supplier 的 FK"""
    candidates = _svc()._detect_fk_columns(
        src_cols=["order_id", "order_date", "supplier.id", "supplier.name", "logistics.carrier"],
        tgt_entity_class="Supplier",
        tgt_dataset_name="supplier_database",
    )
    cols = [c for c, _ in candidates]
    assert "supplier.id" in cols
    rel = dict(candidates)["supplier.id"]
    assert rel == "HAS_SUPPLIER"


def test_dot_fk_does_not_match_unrelated_target():
    """supplier.id 不应被识别为指向 Warehouse 的 FK"""
    candidates = _svc()._detect_fk_columns(
        src_cols=["supplier.id", "supplier.name"],
        tgt_entity_class="Warehouse",
        tgt_dataset_name="warehouse_db",
    )
    assert candidates == []


def test_chinese_fk_column_via_value_overlap():
    """中文列名「供应商」值与目标主键高度重合时应判定为 FK,关系名用目标实体类"""
    rows = [{"供应商": "SUP-001"}, {"供应商": "SUP-002"}, {"供应商": "SUP-003"}]
    candidates = _svc()._detect_fk_columns(
        src_cols=["运单号", "供应商", "目的区域"],
        tgt_entity_class="Supplier",
        tgt_dataset_name="supplier_database",
        src_sample_rows=rows,
        tgt_pk_values={"SUP-001", "SUP-002", "SUP-003", "SUP-004"},
    )
    assert ("供应商", "HAS_SUPPLIER") in candidates


def test_chinese_column_without_overlap_not_fk():
    """值与目标主键无重合的中文列不应判定为该目标的 FK"""
    rows = [{"目的区域": "华东"}, {"目的区域": "华南"}]
    candidates = _svc()._detect_fk_columns(
        src_cols=["目的区域"],
        tgt_entity_class="Supplier",
        tgt_dataset_name="supplier_database",
        src_sample_rows=rows,
        tgt_pk_values={"SUP-001", "SUP-002"},
    )
    assert candidates == []


def test_standard_underscore_fk_still_works():
    """既有行为回归: supplier_id 仍应被识别"""
    candidates = _svc()._detect_fk_columns(
        src_cols=["supplier_id", "amount"],
        tgt_entity_class="Supplier",
        tgt_dataset_name="supplier_database",
    )
    assert candidates and candidates[0][0] == "supplier_id"
