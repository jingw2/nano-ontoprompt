from app.models.user import User
from app.models.ontology import OntologyProject
from app.models.file import UploadedFile
from app.models.prompt import Prompt
from app.models.model_config import ModelConfig
from app.models.entity import Entity
from app.models.logic import LogicRule
from app.models.action import Action
from app.models.relation import Relation
from app.models.extraction_task import ExtractionTask
from app.models.rules_config import RulesConfig
from app.models.audit_task import AuditTask
from app.models.entity_instance import EntityInstance
from app.models.security_domain import SecurityDomain
from app.models.auth_refresh import AuthRefreshFamily, AuthRefreshToken
from app.models.ontology_release import OntologyRelease
from app.models.governance_audit import (
    GovernanceAuditLog,
    GovernanceAuditOutbox,
    GovernanceAuditChainHead,
)
from app.models.entity_property_definition import (
    EntityPropertyDefinition,
    OntologyMigrationFinding,
)
from app.models.ontology_access import OntologyProjectAccessGrant


def load_all_models():
    """Register every ORM model implemented in the current milestone."""
    from app.database import Base
    from app.models.v2 import action, connection, curated, dataset, logic, mapping, pipeline  # noqa: F401
    from app.models.security_domain import SecurityDomain  # noqa: F401
    from app.models.auth_refresh import AuthRefreshFamily, AuthRefreshToken  # noqa: F401
    from app.models.ontology_release import OntologyRelease  # noqa: F401
    from app.models.governance_audit import (  # noqa: F401
        GovernanceAuditLog,
        GovernanceAuditOutbox,
        GovernanceAuditChainHead,
    )
    from app.models.entity_property_definition import (  # noqa: F401
        EntityPropertyDefinition,
        OntologyMigrationFinding,
    )
    from app.models.ontology_access import OntologyProjectAccessGrant  # noqa: F401

    return Base.metadata

__all__ = [
    "User",
    "OntologyProject",
    "UploadedFile",
    "Prompt",
    "ModelConfig",
    "Entity",
    "LogicRule",
    "Action",
    "Relation",
    "ExtractionTask",
    "RulesConfig",
    "AuditTask",
    "EntityInstance",
    "SecurityDomain",
    "AuthRefreshFamily",
    "AuthRefreshToken",
    "OntologyRelease",
    "GovernanceAuditLog",
    "GovernanceAuditOutbox",
    "GovernanceAuditChainHead",
    "EntityPropertyDefinition",
    "OntologyMigrationFinding",
    "OntologyProjectAccessGrant",
    "load_all_models",
]
