"""Legacy Logic rule/Action CRUD (app/routers/logic.py, app/routers/actions.py)
must keep a same-id v2 mirror row in sync (app/services/publication/legacy_sync.py) —
publication and the Agent tool catalog read only the v2 tables, and a
`simple_llm`-mode ontology's Logic rules/Actions only ever exist in the
legacy tables otherwise, which made them invisible to Agent tool binding."""
from sqlalchemy import text

from app.models.v2.action import OntologyActionType
from app.models.v2.logic import OntologyLogicRule


def _v2_logic_row(db, rule_id):
    db.expire_all()
    return db.get(OntologyLogicRule, rule_id)


def _v2_action_row(db, action_id):
    db.expire_all()
    return db.get(OntologyActionType, action_id)


def test_create_logic_rule_creates_a_v2_mirror_with_the_same_id(client, auth_headers, ontology, db):
    oid = ontology["id"]
    r = client.post(f"/api/v1/ontologies/{oid}/logic", json={
        "name_cn": "超额审批规则", "function_type": "derived_property", "definition": "amount > 1000",
    }, headers=auth_headers)
    assert r.status_code == 201, r.text
    rule_id = r.json()["data"]["id"]

    mirror = _v2_logic_row(db, rule_id)
    assert mirror is not None
    assert mirror.name == "超额审批规则"
    assert mirror.logic_type == "derived_property"
    assert mirror.enabled is True
    assert mirror.expression == {"definition": "amount > 1000"}


def test_update_logic_rule_updates_the_v2_mirror_in_place(client, auth_headers, ontology, db):
    oid = ontology["id"]
    rule_id = client.post(f"/api/v1/ontologies/{oid}/logic", json={
        "name_cn": "Old Name", "definition": "d1",
    }, headers=auth_headers).json()["data"]["id"]

    r = client.put(f"/api/v1/ontologies/{oid}/logic/{rule_id}", json={"name_cn": "New Name"}, headers=auth_headers)
    assert r.status_code == 200, r.text

    mirror = _v2_logic_row(db, rule_id)
    assert mirror.name == "New Name"
    # updating never creates a second mirror row
    count = db.execute(text(
        "SELECT COUNT(*) FROM v2_ontology_logic_rules WHERE id = :id"
    ), {"id": rule_id}).scalar_one()
    assert count == 1


def test_delete_logic_rule_deletes_its_v2_mirror(client, auth_headers, ontology, db):
    oid = ontology["id"]
    rule_id = client.post(f"/api/v1/ontologies/{oid}/logic", json={
        "name_cn": "Doomed Rule",
    }, headers=auth_headers).json()["data"]["id"]
    assert _v2_logic_row(db, rule_id) is not None

    r = client.delete(f"/api/v1/ontologies/{oid}/logic/{rule_id}", headers=auth_headers)
    assert r.status_code == 204
    assert _v2_logic_row(db, rule_id) is None


def test_toggle_logic_rule_disables_the_v2_mirror_without_touching_a_published_status(client, auth_headers, ontology, db):
    oid = ontology["id"]
    rule_id = client.post(f"/api/v1/ontologies/{oid}/logic", json={
        "name_cn": "Toggle Me",
    }, headers=auth_headers).json()["data"]["id"]

    r = client.post(f"/api/v1/ontologies/{oid}/logic/{rule_id}/toggle", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    mirror = _v2_logic_row(db, rule_id)
    assert mirror.enabled is False
    assert mirror.status == "disabled"

    # simulate the mirror having been published through the v2 pathway —
    # disabling via the legacy toggle must not clobber that
    db.execute(text("UPDATE v2_ontology_logic_rules SET status = 'published' WHERE id = :id"), {"id": rule_id})
    db.commit()
    client.post(f"/api/v1/ontologies/{oid}/logic/{rule_id}/toggle", headers=auth_headers)  # re-enable
    client.post(f"/api/v1/ontologies/{oid}/logic/{rule_id}/toggle", headers=auth_headers)  # disable again
    mirror = _v2_logic_row(db, rule_id)
    assert mirror.enabled is False
    assert mirror.status == "published"  # untouched


def test_create_action_creates_a_v2_mirror_with_the_same_id(client, auth_headers, ontology, db):
    oid = ontology["id"]
    r = client.post(f"/api/v1/ontologies/{oid}/actions", json={"name_cn": "创建订单"}, headers=auth_headers)
    assert r.status_code == 201, r.text
    action_id = r.json()["data"]["id"]

    mirror = _v2_action_row(db, action_id)
    assert mirror is not None
    assert mirror.name == "创建订单"
    assert mirror.enabled is True


def test_delete_action_deletes_its_v2_mirror(client, auth_headers, ontology, db):
    oid = ontology["id"]
    action_id = client.post(f"/api/v1/ontologies/{oid}/actions", json={
        "name_cn": "Doomed Action",
    }, headers=auth_headers).json()["data"]["id"]
    assert _v2_action_row(db, action_id) is not None

    r = client.delete(f"/api/v1/ontologies/{oid}/actions/{action_id}", headers=auth_headers)
    assert r.status_code == 204
    assert _v2_action_row(db, action_id) is None


def test_editing_a_rule_that_already_has_a_mapping_service_mirror_updates_it_instead_of_duplicating(client, auth_headers, ontology, db):
    """pipeline_mapping's mapping_service.py dual-writes a legacy row AND a
    v2 row under two INDEPENDENT ids (correlated only by ontology_id+name).
    Editing that legacy row through the ordinary Logic tab endpoint must
    find and update the existing mirror by name, not spawn a second one
    keyed by the legacy row's own id."""
    oid = ontology["id"]
    rule_id = client.post(f"/api/v1/ontologies/{oid}/logic", json={
        "name_cn": "映射规则: 供应商",
    }, headers=auth_headers).json()["data"]["id"]

    # simulate mapping_service.py's own independent v2 row for the same rule
    # (as if pipeline_mapping had already dual-written this before the legacy
    # row above was ever touched again) — different id, same ontology+name
    preexisting_mirror_id = "11111111-1111-1111-1111-111111111111"
    db.add(OntologyLogicRule(
        id=preexisting_mirror_id, ontology_id=oid, name="映射规则: 供应商",
        logic_type="mapping", expression={}, enabled=True, status="draft", version=1,
    ))
    db.commit()
    # the create above already made ITS OWN mirror at id=rule_id (before the
    # pre-existing one was added) -- delete it to model the real timeline
    # where mapping_service.py's row came first
    db.execute(text("DELETE FROM v2_ontology_logic_rules WHERE id = :id"), {"id": rule_id})
    db.commit()

    r = client.put(f"/api/v1/ontologies/{oid}/logic/{rule_id}", json={"description": "updated"}, headers=auth_headers)
    assert r.status_code == 200, r.text

    rows = db.execute(text(
        "SELECT id, description FROM v2_ontology_logic_rules WHERE ontology_id = :o AND name = '映射规则: 供应商'"
    ), {"o": oid}).mappings().all()
    assert len(rows) == 1  # no duplicate
    assert rows[0]["id"] == preexisting_mirror_id  # the existing mirror was reused
    assert rows[0]["description"] == "updated"


def test_ontology_tool_catalog_exposes_logic_and_action_created_via_the_legacy_api(client, auth_headers, ontology, db):
    """End-to-end: a Logic rule/Action created the ordinary way (the same
    endpoints the Ontology detail page's Logic/Actions tabs call, and the
    same tables the simple_llm extraction task writes to) now shows up as
    an Agent-bindable tool descriptor."""
    oid = ontology["id"]
    rule_id = client.post(f"/api/v1/ontologies/{oid}/logic", json={
        "name_cn": "超额审批规则",
    }, headers=auth_headers).json()["data"]["id"]
    action_id = client.post(f"/api/v1/ontologies/{oid}/actions", json={
        "name_cn": "创建订单",
    }, headers=auth_headers).json()["data"]["id"]

    from app.services.agent.catalog import ontology_tool_catalog
    catalog = ontology_tool_catalog(db, oid)
    by_id = {t["descriptor_id"]: t for t in catalog["tools"]}
    assert by_id[f"logic:{rule_id}"]["name"] == "超额审批规则"
    assert by_id[f"action:{action_id}"]["name"] == "创建订单"
