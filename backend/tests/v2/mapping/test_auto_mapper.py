"""AutoMapper 单元测试"""
import pytest
from unittest.mock import MagicMock, patch
from app.services.v2.mapping.auto_mapper import AutoMapper, MappingSuggestion, LinkSuggestion


def make_mapper():
    db = MagicMock()
    return AutoMapper(db)


COLUMNS = ["order_id", "customer_id", "product_name", "amount", "order_date", "is_active"]
SAMPLE_ROWS = [
    {"order_id": "ORD-001", "customer_id": "C-100", "product_name": "Widget A",
     "amount": "1200.00", "order_date": "2024-01-15", "is_active": "true"},
]


# ── 规则回退测试 ──────────────────────────────────────────────────────

def test_rule_based_entity_class_camel_case():
    mapper = make_mapper()
    suggestion = mapper._rule_based_suggest("supply_order", COLUMNS)
    assert suggestion.entity_class == "SupplyOrder"


def test_rule_based_all_columns_mapped():
    mapper = make_mapper()
    suggestion = mapper._rule_based_suggest("orders", COLUMNS)
    mapped_cols = [fm.column_name for fm in suggestion.field_mappings]
    assert set(mapped_cols) == set(COLUMNS)


def test_rule_based_pk_detection_id_suffix():
    mapper = make_mapper()
    suggestion = mapper._rule_based_suggest("orders", COLUMNS)
    assert suggestion.primary_key_column == "order_id"


def test_rule_based_type_guessing():
    mapper = make_mapper()
    suggestion = mapper._rule_based_suggest("orders", COLUMNS)
    fm_map = {fm.column_name: fm.property_type for fm in suggestion.field_mappings}
    assert fm_map["order_date"] == "datetime"
    assert fm_map["amount"] == "float"
    assert fm_map["is_active"] == "boolean"
    assert fm_map["order_id"] == "integer"


# ── LLM 回退到规则 ────────────────────────────────────────────────────

def test_suggest_falls_back_to_rules_on_llm_error():
    mapper = make_mapper()
    # LLM 调用失败时回退到规则建议
    with patch.object(mapper, '_llm_suggest', side_effect=Exception("No API key")):
        suggestion = mapper.suggest_field_mapping("orders", COLUMNS, SAMPLE_ROWS)
    assert isinstance(suggestion, MappingSuggestion)
    assert suggestion.entity_class == "Orders"
    assert len(suggestion.field_mappings) == len(COLUMNS)


def test_llm_suggest_uses_configured_model():
    mapper = make_mapper()
    payload = {
        "entity_class": "SupplierOrder",
        "entity_class_cn": "供应商订单",
        "description": "供应商订单实体",
        "primary_key_column": "order_id",
        "field_mappings": [
            {"column": "order_id", "property": "order_id", "type": "string", "confidence": 0.9, "reason": "主键"},
        ],
    }
    with patch("app.services.model_config_selector.select_llm_model_config", return_value=object()), \
         patch("app.services.model_config_selector.llm_call_kwargs", return_value={
             "provider": "compatible",
             "api_key": "test-key",
             "api_base": "https://api.deepseek.com",
             "model": "deepseek-v4-flash",
         }), \
         patch("app.services.llm_service._call_llm", return_value=__import__("json").dumps(payload)) as mock_call:
        suggestion = mapper._llm_suggest("supplier_orders", ["order_id"], SAMPLE_ROWS, "供应链")

    assert suggestion.entity_class == "SupplierOrder"
    assert mock_call.call_args.kwargs["provider"] == "compatible"
    assert mock_call.call_args.kwargs["api_base"] == "https://api.deepseek.com"
    assert mock_call.call_args.kwargs["model"] == "deepseek-v4-flash"


# ── 链接建议测试 ──────────────────────────────────────────────────────

def test_suggest_links_detects_foreign_key():
    mapper = make_mapper()
    src_cols = ["order_id", "customer_id", "amount"]
    tgt_cols = ["id", "name", "email"]
    links = mapper.suggest_links("orders", src_cols, "customers", tgt_cols)
    assert len(links) == 1
    assert links[0].source_fk_column == "customer_id"
    assert links[0].confidence >= 0.8


def test_suggest_links_no_fk():
    mapper = make_mapper()
    links = mapper.suggest_links("orders", ["name", "amount"], "customers", ["id", "name"])
    assert links == []


def test_to_class_name_snake_case():
    assert AutoMapper._to_class_name("supply_chain_order") == "SupplyChainOrder"


def test_to_class_name_kebab():
    assert AutoMapper._to_class_name("clean-orders") == "CleanOrders"


def test_guess_type_variants():
    assert AutoMapper._guess_type("created_at") == "datetime"
    assert AutoMapper._guess_type("unit_price") == "float"
    assert AutoMapper._guess_type("is_deleted") == "boolean"
    assert AutoMapper._guess_type("description") == "string"
