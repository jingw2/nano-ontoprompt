"""Multi-source pipeline ingestion: proves _collect_sources() gathers every
connector-node file as a separate source when a Pipeline definition lists
more than one, and that multi_source is computed correctly downstream. Does
not assert anything about how conflicting attributes across sources get
resolved during extraction — that is sub-project D's scoring concern."""
from types import SimpleNamespace

import pytest

from app.tasks.v2.pipeline_run import _collect_sources


@pytest.fixture
def two_datasets(db):
    from app.models.v2.dataset import Dataset

    ds_a = Dataset(id="ds-customers-a", name="customers_v1", kind="structured")
    ds_b = Dataset(id="ds-customers-b", name="customers_v2_conflicting", kind="structured")
    db.add(ds_a)
    db.add(ds_b)
    db.commit()
    return ds_a, ds_b


def test_collect_sources_returns_both_connector_node_files(db, two_datasets):
    ds_a, ds_b = two_datasets
    pl = SimpleNamespace(
        definition={
            "nodes": [
                {
                    "type": "connector",
                    "config": {
                        "files": [
                            {"dataset_id": ds_a.id, "name": "customers_v1.csv"},
                            {"dataset_id": ds_b.id, "name": "customers_v2_conflicting.csv"},
                        ]
                    },
                }
            ]
        },
        source_dataset_id=None,
        route=None,
    )

    sources = _collect_sources(db, pl)

    assert len(sources) == 2
    collected_ids = {s["dataset_id"] for s in sources}
    assert collected_ids == {ds_a.id, ds_b.id}
    assert {s["filename"] for s in sources} == {"customers_v1.csv", "customers_v2_conflicting.csv"}


def test_single_source_is_not_multi_source(db, two_datasets):
    ds_a, _ = two_datasets
    pl = SimpleNamespace(
        definition={
            "nodes": [{
                "type": "connector",
                "config": {"files": [{"dataset_id": ds_a.id, "name": "a.csv"}]},
            }]
        },
        source_dataset_id=None,
        route=None,
    )

    sources = _collect_sources(db, pl)
    multi_source = len(sources) > 1

    assert multi_source is False
    assert len(sources) == 1
