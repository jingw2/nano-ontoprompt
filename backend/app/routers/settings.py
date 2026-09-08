from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.deps import get_db, get_current_user, require_editor
from app.models.rules_config import RulesConfig

router = APIRouter()

class RuleUpdate(BaseModel):
    rule_key: str
    rule_value: str

@router.get("/rules")
def get_rules(db: Session = Depends(get_db), _=Depends(get_current_user)):
    rules = db.query(RulesConfig).order_by(RulesConfig.rule_key).all()
    return {"data": [
        {"id": r.id, "rule_key": r.rule_key, "rule_value": r.rule_value,
         "rule_label_cn": r.rule_label_cn, "rule_label_en": r.rule_label_en, "editable": r.editable}
        for r in rules
    ]}

@router.put("/rules")
def update_rules(body: List[RuleUpdate], db: Session = Depends(get_db), _=Depends(require_editor)):
    for update in body:
        rule = db.query(RulesConfig).filter(RulesConfig.rule_key == update.rule_key, RulesConfig.editable == True).first()
        if rule:
            rule.rule_value = update.rule_value
    db.commit()
    return {"message": "Rules updated"}
