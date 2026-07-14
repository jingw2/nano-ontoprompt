from app.services.v2.pipeline.base import PipelineContext
from app.services.v2.pipeline.engine import execute_route_a
from app.tasks.v2.pipeline_run import _pipeline_runtime_config, _source_runtime_route


class DummyPipeline:
    spec = {}
    definition = {
        "nodes": [
            {
                "id": "transform_1",
                "type": "transform",
                "config": {
                    "path": "wide_table",
                    "steps": [
                        {"op": "apply_split", "params": {
                            "split_config": {
                                "orders": ["order_id", "customer_id"],
                                "amounts": ["order_id", "amount"],
                            }
                        }},
                    ],
                },
            }
        ],
        "edges": [],
    }


def test_pipeline_runtime_config_uses_transform_node_steps():
    route, spec = _pipeline_runtime_config(DummyPipeline())

    assert route == "A"
    assert spec["path"] == "wide_table"
    assert spec["wide_table_split"]["enabled"] is True
    assert spec["wide_table_split"]["split_config"]["orders"] == ["order_id", "customer_id"]


def test_route_a_wide_split_writes_split_tables_to_context():
    ctx = PipelineContext(dataset_id="ds-1", version_no=1, route="A", spec={
        "wide_table_split": {
            "enabled": True,
            "split_config": {
                "orders": ["order_id", "customer_id"],
                "amounts": ["order_id", "amount"],
            },
        }
    })
    rows = [
        {"order_id": "O-1", "customer_id": "C-1", "amount": "10"},
        {"order_id": "O-2", "customer_id": "C-2", "amount": "20"},
    ]

    output, ctx = execute_route_a(ctx, rows)

    assert list(ctx.meta["split_tables"].keys()) == ["orders", "amounts"]
    assert output == ctx.meta["split_tables"]["orders"]
    assert ctx.meta["split_tables"]["amounts"][0] == {"order_id": "O-1", "amount": "10"}


def test_auto_transform_preserves_per_source_route_before_pipeline_default():
    assert _source_runtime_route({"kind": "unstructured", "route": "C"}, None, "A") == "C"
    assert _source_runtime_route({"kind": "semi", "route": "B"}, None, "A") == "B"
    assert _source_runtime_route({"kind": "structured", "route": "A"}, None, "A") == "A"


def test_explicit_transform_route_overrides_source_route():
    assert _source_runtime_route({"kind": "unstructured", "route": "C"}, "A", "A") == "A"
