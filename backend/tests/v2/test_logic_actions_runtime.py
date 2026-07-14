from app.models.entity import Entity
from app.models.action import Action
from app.models.logic import LogicRule
from app.models.v2.logic import OntologyLogicRule
from app.models.v2.action import OntologyActionRun, OntologyActionType
from app.routers.actions import publish_actions
from app.routers.logic import publish_logic_rules
from app.routers.v2.logic_actions import (
    ActionReviewRequest,
    ActionRunRequest,
    LogicReviewRequest,
    LogicTestRequest,
    review_action_type,
    review_logic_rule,
    run_action_type,
    test_logic_rule as run_logic_rule_test,
)


def test_run_published_set_property_action(db):
    ontology_id = "ont-runtime-1"
    entity = Entity(
        id="entity-1",
        ontology_id=ontology_id,
        name_cn="Order 1",
        name_en="Order",
        type="Order",
        properties={"status": "pending"},
    )
    action = OntologyActionType(
        id="action-1",
        ontology_id=ontology_id,
        name="Change Order status",
        action_category="state_transition",
        target_entity_type="Order",
        parameters=[{"name": "status", "type": "string", "required": True}],
        effects=[{"action": "set_property", "property": "status"}],
        status="published",
        enabled=True,
    )
    db.add(entity)
    db.add(action)
    db.commit()

    result = run_action_type(
        ontology_id,
        action.id,
        ActionRunRequest(target_object_id=entity.id, parameters={"status": "approved"}),
        db,
    )

    db.refresh(entity)
    run = db.query(OntologyActionRun).filter(OntologyActionRun.id == result["run_id"]).first()
    assert result["status"] == "completed"
    assert entity.properties["status"] == "approved"
    assert run.status == "completed"
    assert run.before_snapshot[entity.id]["status"] == "pending"
    assert run.after_snapshot[entity.id]["status"] == "approved"


def test_logic_review_and_executable_test(db):
    rule = OntologyLogicRule(
        id="logic-1",
        ontology_id="ont-runtime-2",
        name="Amount positive",
        logic_type="validation",
        target_entity_type="Order",
        expression={"operator": "gt", "field": "amount", "value": 0},
        status="draft",
        enabled=True,
    )
    db.add(rule)
    db.commit()

    review = review_logic_rule(
        "ont-runtime-2",
        "logic-1",
        LogicReviewRequest(status="reviewed", enabled=True, notes="approved"),
        db,
    )
    result = run_logic_rule_test(
        "ont-runtime-2",
        "logic-1",
        LogicTestRequest(row={"amount": "12.5"}),
        db,
    )

    assert review["status"] == "reviewed"
    assert result["status"] == "completed"
    assert result["passed"] is True
    assert db.query(OntologyLogicRule).filter_by(id="logic-1").first().source_ref["review_notes"] == "approved"


def test_action_review_updates_submission_criteria(db):
    action = OntologyActionType(
        id="action-review-1",
        ontology_id="ont-runtime-3",
        name="Reviewable action",
        action_category="crud",
        effects=[],
        status="draft",
        enabled=True,
    )
    db.add(action)
    db.commit()

    result = review_action_type(
        "ont-runtime-3",
        "action-review-1",
        ActionReviewRequest(
            status="reviewed",
            submission_criteria=[{"type": "required_param", "name": "reason"}],
            notes="needs reason",
        ),
        db,
    )

    db.refresh(action)
    assert result["status"] == "reviewed"
    assert action.submission_criteria[0]["name"] == "reason"
    assert action.side_effects[0]["type"] == "review_note"


def test_action_runtime_rejects_missing_required_parameter(db):
    action = OntologyActionType(
        id="action-criteria-1",
        ontology_id="ont-runtime-4",
        name="Create Order",
        action_category="crud",
        target_entity_type="Order",
        parameters=[{"name": "data", "type": "object", "required": True}],
        effects=[{"action": "create_object", "entity_type": "Order"}],
        status="published",
        enabled=True,
    )
    db.add(action)
    db.commit()

    try:
        run_action_type("ont-runtime-4", action.id, ActionRunRequest(parameters={}), db)
    except Exception as exc:
        assert "missing_parameter" in str(exc)
    else:
        raise AssertionError("Expected action submission to fail")


def test_v1_logic_publish_syncs_v2_status(db):
    ontology_id = "ont-runtime-5"
    db.add(LogicRule(
        id="logic-v1",
        ontology_id=ontology_id,
        name_cn="Mapping Rule: Supplier",
        name_en="mapping_supplier",
        formula="mapping",
        enabled=True,
        status="draft",
    ))
    db.add(OntologyLogicRule(
        id="logic-v2",
        ontology_id=ontology_id,
        name="Mapping Rule: Supplier",
        logic_type="mapping",
        expression={},
        enabled=True,
        status="draft",
    ))
    db.commit()

    publish_logic_rules(ontology_id, db)

    assert db.query(LogicRule).filter_by(id="logic-v1").first().status == "published"
    assert db.query(OntologyLogicRule).filter_by(id="logic-v2").first().status == "published"


def test_v1_action_publish_syncs_v2_status(db):
    ontology_id = "ont-runtime-6"
    db.add(Action(
        id="action-v1",
        ontology_id=ontology_id,
        name_cn="Create Supplier",
        name_en="create_supplier",
        enabled=True,
        status="draft",
    ))
    db.add(OntologyActionType(
        id="action-v2",
        ontology_id=ontology_id,
        name="Create Supplier",
        action_category="crud",
        effects=[],
        enabled=True,
        status="draft",
    ))
    db.commit()

    publish_actions(ontology_id, db)

    assert db.query(Action).filter_by(id="action-v1").first().status == "published"
    assert db.query(OntologyActionType).filter_by(id="action-v2").first().status == "published"
