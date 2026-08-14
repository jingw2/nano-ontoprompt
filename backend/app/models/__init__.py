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
from app.models.entity_instance_relation import EntityInstanceRelation
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
from app.models.model_version import (
    ModelConfigVersion,
    ModelCredential,
    ModelMigrationFinding,
)
from app.models.agent import Agent, AgentVersion, AgentAccessGrant
from app.models.ontology_data_grant import OntologyDataGrant
from app.models.agent_config import (
    ApplicationStateSchemaRegistry,
    ApplicationStateSchemaVersion,
    ToolProvider,
    ToolConnection,
    ToolConnectionVersion,
    PromptGeneration,
    AgentOntologyBinding,
    AgentExternalToolBinding,
    AgentRetrievalSource,
)
from app.models.agent_index_outbox import AgentIndexOutbox
from app.models.agent_runtime import (
    AgentSession,
    AgentTurn,
    AgentMessage,
    AgentTurnDispatchOutbox,
    RuntimeArtifact,
    AgentTurnCheckpoint,
    AgentTurnCheckpointWrite,
    AgentNodeExecution,
    AgentModelInvocation,
    AgentRuntimeEvent,
    AgentToolExecution,
    AgentApproval,
    AgentReconciliationCase,
    AgentClarificationRequest,
    AgentApplicationStateSnapshot,
    AgentStreamTicket,
    AgentPurgeJob,
    AgentPurgeMarker,
)


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
    from app.models.entity_instance_relation import EntityInstanceRelation  # noqa: F401
    from app.models.entity_property_definition import (  # noqa: F401
        EntityPropertyDefinition,
        OntologyMigrationFinding,
    )
    from app.models.ontology_access import OntologyProjectAccessGrant  # noqa: F401
    from app.models.model_version import (  # noqa: F401
        ModelConfigVersion,
        ModelCredential,
        ModelMigrationFinding,
    )
    from app.models.agent import Agent, AgentVersion, AgentAccessGrant  # noqa: F401
    from app.models.ontology_data_grant import OntologyDataGrant  # noqa: F401
    from app.models.agent_config import (  # noqa: F401
        ApplicationStateSchemaRegistry,
        ApplicationStateSchemaVersion,
        ToolProvider,
        ToolConnection,
        ToolConnectionVersion,
        PromptGeneration,
        AgentOntologyBinding,
        AgentExternalToolBinding,
        AgentRetrievalSource,
    )
    from app.models.agent_index_outbox import AgentIndexOutbox  # noqa: F401
    from app.models.agent_runtime import (  # noqa: F401
        AgentSession,
        AgentTurn,
        AgentMessage,
        AgentTurnDispatchOutbox,
        RuntimeArtifact,
        AgentTurnCheckpoint,
        AgentTurnCheckpointWrite,
        AgentNodeExecution,
        AgentModelInvocation,
        AgentRuntimeEvent,
        AgentToolExecution,
        AgentApproval,
        AgentReconciliationCase,
        AgentClarificationRequest,
        AgentApplicationStateSnapshot,
        AgentStreamTicket,
        AgentPurgeJob,
        AgentPurgeMarker,
    )

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
    "EntityInstanceRelation",
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
    "ModelConfigVersion",
    "ModelCredential",
    "ModelMigrationFinding",
    "Agent",
    "AgentVersion",
    "AgentAccessGrant",
    "OntologyDataGrant",
    "ApplicationStateSchemaRegistry",
    "ApplicationStateSchemaVersion",
    "ToolProvider",
    "ToolConnection",
    "ToolConnectionVersion",
    "PromptGeneration",
    "AgentOntologyBinding",
    "AgentExternalToolBinding",
    "AgentRetrievalSource",
    "AgentSession",
    "AgentTurn",
    "AgentMessage",
    "AgentTurnDispatchOutbox",
    "AgentIndexOutbox",
    "RuntimeArtifact",
    "AgentTurnCheckpoint",
    "AgentTurnCheckpointWrite",
    "AgentNodeExecution",
    "AgentModelInvocation",
    "AgentRuntimeEvent",
    "AgentToolExecution",
    "AgentApproval",
    "AgentReconciliationCase",
    "AgentClarificationRequest",
    "AgentApplicationStateSnapshot",
    "AgentStreamTicket",
    "AgentPurgeJob",
    "AgentPurgeMarker",
    "load_all_models",
]
