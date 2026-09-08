# Ontexus Enterprise Read Authorization Design

**Status:** Approved design

**Date:** 2026-09-08

## 1. Purpose

Add enterprise-grade read authorization to Ontexus for customer-managed,
single-enterprise deployments. The design protects Ontology schema, objects,
properties, relations, search results, and read-only logic when accessed from
the Ontexus UI, REST APIs, built-in Agents, or external Agent frameworks.

The first supported external framework is AgentScope, but the integration
boundary is standard HTTP Model Context Protocol (MCP) plus OAuth. The design
must also work with other compliant frameworks without moving authorization
logic into those frameworks.

## 2. Confirmed scope

### 2.1 Deployment and identity

- Each customer runs an independent Ontexus deployment.
- This is not a multi-tenant SaaS authorization design.
- The existing `security_domain` remains the top-level deployment security
  boundary and may represent an internal security realm.
- The first release continues to use Ontexus local users.
- The identity layer exposes an adapter interface for future OIDC, SAML, or
  LDAP integration, but those providers are not implemented in this project.
- Enterprise organization modeling is limited to one primary department per
  user plus zero or more user groups.

### 2.2 Agent delegation

- An external Agent always acts on behalf of a logged-in Ontexus user.
- Service-account and autonomous machine identity access are out of scope.
- An Agent never supplies an authoritative permission list.
- Ontexus authenticates the user, computes effective access, issues delegated
  credentials, enforces every data access, and writes the audit trail.
- The Agent framework only performs OAuth/MCP integration, carries credentials,
  reads an advisory authorization summary, and invokes tools.

### 2.3 Authorization scope

- This project governs read access only.
- Existing write, Action, approval, and publication behavior is not redesigned.
- Existing design-plane capabilities remain `discover`, `read`, `edit`, and
  `publish`.
- Data-plane read capabilities are `read_schema`, `read_instances`,
  `traverse_relations`, `execute_read_logic`, and `export_data`.
- Existing write-related capabilities continue to behave as they do today and
  are not exposed by the new external read-only MCP surface.

## 3. Existing foundation and identified gap

Ontexus already contains much of the required foundation:

- `User` has a closed platform role and a `security_domain_id`.
- `OntologyProjectAccessGrant` governs design and lifecycle authority.
- `OntologyDataGrant` stores data capabilities, entity/property/relation/action
  allowlists, a validity window, policy revision, and a restricted row policy.
- The policy module defines a closed capability vocabulary and a restricted
  row-policy DSL.
- `OAuthClient` and `RuntimeDelegatedCredential` already model a registered
  Agent acting on behalf of a user with an audience-bound, short-lived token.
- `AgentOntologyBinding` limits an Agent version's Ontology access.
- `ToolGateway` is the intended common entry point for Agent tool calls.

The critical gap is enforcement. The current Tool Gateway checks that an active
grant contains a capability, while the downstream Ontology read functions can
still return complete `row_data`. The stored entity, property, relation, and row
restrictions are not consistently compiled into SQL, Cypher, and vector-search
execution. The first implementation milestone must close this gap before adding
more policy authoring features.

## 4. Chosen architecture

Use the existing Ontexus authorization foundation and add framework-agnostic
Policy Decision and Policy Enforcement boundaries. Do not introduce OpenFGA or
OPA as a required deployment dependency in the first release.

```text
Local user login
       |
Department/group membership + authorization policies
       |
Subject Context Builder
       |
OAuth delegated credential for the Agent
       |
REST / built-in Agent / protected MCP Server
       |
Policy Decision Point (PDP)
       |
AuthorizationDecision + QueryConstraints
       |
Policy Enforcement Point (PEP)
       |
SecureOntologyReader
       |
PostgreSQL / Neo4j / ChromaDB
       |
Redacted result + decision audit
```

OpenFGA or another ReBAC service may later be attached behind a relationship
resolver interface if organization sharing becomes substantially more complex.
OPA or another policy engine may later be attached behind the PDP interface.
Neither option changes the PEP or storage adapters.

## 5. Security invariants

1. Ontexus is the only authority for effective permissions.
2. Agent prompt context is advisory and never authorizes a request.
3. No grant means deny.
4. Unknown capabilities, resources, policy fields, operators, or query forms
   fail closed.
5. Every protected result is filtered before it reaches an Agent or browser.
6. REST, built-in Agents, MCP, search, graph traversal, aggregation, and export
   use the same PDP and Secure Ontology Reader.
7. Filtering occurs before pagination, sorting, aggregation, truncation, and
   result counts are calculated.
8. A visible relation requires an allowed relation type and visible endpoints.
9. A successful empty query is distinct from an authorization denial.
10. A network connection or MCP session never extends token validity.
11. Authorization failure never falls back to unfiltered data.
12. The system cannot retract data already delivered to a model; therefore the
    first read must be authorized correctly.

## 6. Organization and subject model

### 6.1 Tables

```text
departments
- id
- security_domain_id
- code
- name
- parent_id (nullable; simple department tree)
- status: active | inactive
- revision
- created_at / updated_at

user_groups
- id
- security_domain_id
- name
- description
- status: active | inactive
- revision
- created_at / updated_at

user_group_members
- group_id
- user_id
- created_by
- created_at

authorization_subjects
- id
- security_domain_id
- subject_type: user | department | group
- user_id (nullable)
- department_id (nullable)
- group_id (nullable)
- created_at
```

`authorization_subjects` has exactly one of `user_id`, `department_id`, or
`group_id`. Each referenced entity has one subject row. Grants reference a
subject rather than duplicating three grant schemas.

Add these fields to `users`:

```text
department_id (nullable)
auth_source: local | oidc | ldap | saml
external_subject (nullable)
policy_epoch (monotonically increasing integer)
```

The first release writes `auth_source=local`. Future identity providers map an
external subject to the same internal user and authorization subject, so the
authorization model does not change when SSO is added.

### 6.2 Subject expansion

For each request, the effective subject set is:

```text
the current user subject
+ the active primary department subject
+ every active group subject containing the user
```

Inactive departments and groups contribute no authority. Membership changes,
policy publication, and user deactivation increment `policy_epoch` for affected
users.

## 7. Policy model and composition

### 7.1 Design-plane grants

Continue to use `ontology_project_access_grants` for Ontology discovery,
definition read, edit, and publish authority. Department and group subjects may
receive only `discover` and `read`; `edit` and `publish` remain direct-user
grants governed by the existing lifecycle and role ceiling. This lets an
enterprise expose an Ontology catalog through organization membership without
changing who may modify or publish it.

### 7.2 Data-plane policies

Evolve `ontology_data_grants` into versioned data policies with:

```text
id
logical_policy_id
subject_id
ontology_id
effect: allow | deny
capabilities
entity_allowlist
property_allowlist
relation_allowlist
row_policy
policy_version
revision
status: draft | active | revoked | expired
valid_from / valid_until
created_by / revoked_by
created_at / updated_at
```

Use explicit `['*']` for an unrestricted scope. `null` is not a valid new-policy
scope because its meaning is ambiguous. Migration maps legacy `null` values to
`['*']` according to the legacy behavior proven by tests.

### 7.3 Composition rules

```text
effective capability =
    platform role ceiling
  intersection Agent binding ceiling
  intersection OAuth scope ceiling
  intersection union(matching Allow capabilities)
  minus union(matching Deny capabilities)
```

- Allow policies from the user, department, and groups are unioned.
- Any matching Deny wins over any Allow, including a direct user Allow.
- Entity and relation scopes use Allow union minus Deny union.
- Properties are evaluated only after the object is visible, then use Allow
  union minus Deny union.
- Row access uses:

```text
(AllowPredicate1 OR AllowPredicate2 OR ...)
AND NOT (DenyPredicate1 OR DenyPredicate2 OR ...)
```

- No matching Allow predicate produces `FALSE`.
- The result of policy composition is deterministic and independent of policy
  creation order.

### 7.4 Restricted row-policy DSL

Continue the existing structured DSL. Add actor references needed by the
organization model:

```text
actor.user_id
actor.role
actor.security_domain_id
actor.department.id
actor.department.code
actor.group_ids
actor.authentication_time
actor.token_id
```

Support bounded Boolean composition and typed comparison operations:

```text
eq, ne, in, lt, lte, gt, gte, is_null, not_null,
starts_with, contains, and, or
```

`starts_with` applies only to strings. `contains` applies to a list containing a
scalar operand or a string containing a string operand. Any other operand/type
combination is a compilation error rather than an implicit coercion.

The compiler validates field existence and type against the published Ontology
schema. Administrators cannot submit SQL, Cypher, Python, or other executable
expressions. Policy size, nesting, operand size, and compilation time are
bounded.

## 8. Subject Context and authorization decision

`SubjectContextBuilder` produces an immutable trusted context after validating
the local user, security domain, active organization memberships, Agent client,
audience, and policy epoch.

```json
{
  "sub": "user-id",
  "actor": "registered-agent-client-id",
  "security_domain_id": "domain-id",
  "department_id": "department-id",
  "group_ids": ["group-a", "group-b"],
  "platform_role": "viewer",
  "authentication_time": "2026-09-08T10:00:00Z",
  "policy_epoch": 17,
  "policy_digest": "sha256:...",
  "audience": "https://ontexus.example.com/mcp"
}
```

The PDP interface is transport independent:

```python
authorize(
    subject: SubjectContext,
    action: str,
    resource: ResourceRef,
    environment: RequestEnvironment,
) -> AuthorizationDecision
```

An allowed decision includes a stable `decision_id`, policy revision evidence,
and typed query constraints. It never includes protected row values.

```json
{
  "allowed": true,
  "decision_id": "authz-...",
  "policy_revision": 23,
  "constraints": {
    "entity_types": ["Supplier", "PurchaseOrder"],
    "properties": {
      "Supplier": ["id", "name", "region"]
    },
    "relations": ["SUPPLIED_BY"],
    "row_expression": {
      "property": "region",
      "op": "eq",
      "value_from": "actor.department.code"
    }
  }
}
```

## 9. Enforcement architecture

### 9.1 Enforcement points

- Route entry checks the operation and coarse capability.
- Tool Gateway verifies delegated user and Agent authority.
- `SecureOntologyReader` compiles and applies object, property, row, and
  relation constraints to the actual data request.

FastAPI route guards alone are insufficient because they cannot safely project
fields or filter rows. No protected route or tool may directly call SQLAlchemy
entity queries, `Neo4jService.run_cypher`, or a Chroma collection. They call the
Secure Ontology Reader.

### 9.2 PostgreSQL

The policy compiler emits parameterized SQLAlchemy expressions. Property
projection happens in SQL rather than by reading full JSON and deleting fields
in Python. PostgreSQL RLS adds a defense-in-depth baseline for security domain,
Ontology, entity type, and object visibility.

Database identities are separated:

- migration owner: schema migration only;
- runtime role: handles application requests, has no `BYPASSRLS`, and does not
  own protected tables;
- worker role: has only the permissions required by its queues.

Each transaction sets trusted request attributes with `SET LOCAL`; connection
pool reuse must not retain a previous user's context. Fine-grained application
query compilation and RLS must be parity-tested. RLS errors fail closed.

### 9.3 Neo4j

- Compile entity, row, and relation constraints into every Cypher query.
- Require both endpoints to be visible before returning a relation.
- Require every node and edge in a returned path to be visible.
- Do not expose arbitrary raw Cypher to delegated Agents.
- NL2Cypher produces a restricted query representation; Ontexus injects policy
  constraints after generation and validates the final query.
- If Ontexus cannot prove that a query is constrained, it rejects the query.

### 9.4 ChromaDB and semantic search

The vector store is a candidate generator, not an authorization authority:

1. Pre-filter by Ontology and allowed entity types.
2. Retrieve a bounded, over-fetched candidate batch.
3. Authorize candidate object IDs through PostgreSQL/Secure Ontology Reader.
4. Rank, truncate, count, and return only the authorized candidates.
5. If the secure scan bound is reached, return an incomplete authorized result
   rather than filling the page with unauthorized objects.

Do not encode the complete authorization policy into the vector index in the
first release, because policy changes would require unsafe or expensive index
rebuilds.

## 10. OAuth and MCP delegation

Ontexus acts as both OAuth authorization server and MCP resource server. Add or
complete:

```text
GET  /.well-known/oauth-protected-resource
GET  /.well-known/oauth-authorization-server
GET  /oauth/authorize
POST /oauth/token
POST /oauth/revoke
POST /mcp
```

Use Authorization Code with PKCE for human delegation. Bind tokens to the
registered Agent client and exact MCP resource audience. The token contains
only minimal identity and lifecycle claims:

```json
{
  "iss": "https://ontexus.company.example",
  "sub": "user-id",
  "act": {"sub": "agent-client-id"},
  "aud": "https://ontexus.company.example/mcp",
  "scope": "ontexus:context ontexus:ontology:read",
  "jti": "credential-id",
  "policy_epoch": 17,
  "iat": 1788832800,
  "exp": 1788833100
}
```

- Access-token lifetime defaults to five minutes.
- Agent connection establishment does not consume or extend the token.
- Every HTTP MCP request carries and validates the token.
- A session-bound rotating refresh token avoids interactive login every five
  minutes.
- The first release does not grant offline access.
- Logout, user/Agent deactivation, consent revocation, or policy-epoch change
  invalidates refresh and delegated credentials.
- On `401`, a client refreshes and retries at most once.
- A long-running request authorized at start may finish within a bounded runtime;
  retrieval of an asynchronous result requires fresh authorization.
- Protected streams have a bounded lifetime and cannot preserve expired access.

### 10.1 Framework-neutral MCP surface

Expose generic tools rather than dynamically generating one tool per Ontology
object type:

```text
ontexus.list_ontologies
ontexus.get_ontology_schema
ontexus.search_objects
ontexus.get_object
ontexus.traverse_relations
ontexus.evaluate_read_logic
ontexus.get_authorization_context
```

Also expose the advisory context as an MCP resource:

```text
ontexus://authorization/context
```

Providing both a Resource and a Tool accommodates frameworks that currently
consume only MCP Tools. Both read the same service. The context helps the model
avoid predictably denied calls but never authorizes a later call.

The first complete end-to-end clients are AgentScope and LangChain/LangGraph.
Use the standard MCP Python and TypeScript clients as protocol baselines. Add an
AutoGen smoke test and integration guidance for CrewAI, LlamaIndex, and Semantic
Kernel. Frameworks without interactive OAuth support may receive a short-lived
token from their host application; they do not receive Ontexus policy logic.

### 10.2 Agent/Ontology binding

An Agent binding is a maximum ceiling, not an entitlement. Effective access is:

```text
user/department/group data policy
intersection Agent Ontology binding
intersection OAuth scope
```

Change the current single-binding uniqueness constraint to
`(agent_version_id, ontology_id)` so one immutable Agent version can bind
multiple Ontologies. Each binding carries capability, entity, and relation
ceilings.

## 11. Frontend information architecture

Do not place every permission feature inside an Ontology.

```text
Permission Management
|- Users
|- Departments
|- Groups
|- Policies
`- Audit

Ontology / <name>
`- Security & Access
   |- Overview
   |- Authorized subjects
   |- Object and property policies
   |- Row policies
   |- Relation policies
   `- Access simulation

Agent / <name>
|- Ontology Access
`- MCP Integration
   |- Client configuration
   |- Redirect URI
   |- OAuth scopes
   |- Authorized users
   `- Active sessions
```

The global Agent Integration administration view lists all clients and sessions
for bulk revocation. Agent-specific configuration remains on the Agent detail
page.

### 11.1 Policy authoring

Use a guided editor:

1. Choose user, department, or group.
2. Choose read capabilities.
3. Choose object types.
4. Choose visible properties.
5. Build row conditions from validated fields and operators.
6. Choose relation types.
7. Set validity and publish.

The lifecycle is `draft -> validate -> simulate -> publish`. Publishing creates
an immutable revision. Rollback activates a prior revision without rewriting
history. The first release does not add an approval workflow.

### 11.2 Explanation and simulation

Administrators can simulate one user against selected sample objects and see
the contributing Allow and Deny policies. Ordinary users see only a redacted
"My access" summary: discoverable Ontologies, visible object types, tool
categories, validity, and non-sensitive grant sources.

The frontend removes unauthorized navigation and controls and clears cached
queries after authorization changes, but frontend behavior is never a security
boundary.

## 12. API shape

### 12.1 Organization

```text
GET/POST   /api/v1/admin/departments
PATCH      /api/v1/admin/departments/{department_id}
POST       /api/v1/admin/departments/{department_id}/deactivate
GET/POST   /api/v1/admin/groups
PATCH      /api/v1/admin/groups/{group_id}
POST       /api/v1/admin/groups/{group_id}/members
DELETE     /api/v1/admin/groups/{group_id}/members/{user_id}
PATCH      /api/v1/admin/users/{user_id}/organization
GET        /api/v1/admin/users/{user_id}/effective-access
```

### 12.2 Ontology security

Keep existing design access-grant routes. Add Ontology-scoped data policies:

```text
GET/POST /api/v1/ontologies/{ontology_id}/data-policies
GET      /api/v1/ontologies/{ontology_id}/data-policies/{policy_id}
POST     /api/v1/ontologies/{ontology_id}/data-policies/{policy_id}/validate
POST     /api/v1/ontologies/{ontology_id}/data-policies/{policy_id}/simulate
POST     /api/v1/ontologies/{ontology_id}/data-policies/{policy_id}/publish
POST     /api/v1/ontologies/{ontology_id}/data-policies/{policy_id}/revoke
GET      /api/v1/ontologies/{ontology_id}/effective-access
POST     /api/v1/ontologies/{ontology_id}/access-explanations
```

Keep `/api/v1/ontology-data-grants` temporarily as a compatibility facade over
the same service and mark it deprecated. Do not maintain a second policy path.

### 12.3 Agent integration

```text
GET/PUT    /api/v1/agents/{agent_id}/ontology-bindings
GET/POST   /api/v1/agents/{agent_id}/mcp-client
PATCH      /api/v1/agents/{agent_id}/mcp-client
POST       /api/v1/agents/{agent_id}/mcp-client/revoke
GET        /api/v1/agents/{agent_id}/authorizations
POST       /api/v1/agents/{agent_id}/authorizations/{authorization_id}/revoke
```

All mutations use `base_revision` optimistic concurrency. Records with audit
value are revoked or archived, not hard-deleted.

## 13. Error semantics

| Condition | Result |
| --- | --- |
| Missing, expired, or wrong-audience token | `401 invalid_token` |
| Insufficient OAuth scope | `403 insufficient_scope` |
| User/Agent lacks resource authority | `403 ACCESS_DENIED` |
| Resource existence must be hidden | `404 RESOURCE_NOT_FOUND` |
| Token policy epoch is stale | `401 AUTHORIZATION_CONTEXT_CHANGED` |
| Authorized query has no matches | successful empty collection |
| Policy enforcement is unavailable | `503 POLICY_ENFORCEMENT_UNAVAILABLE` |
| Query cannot be safely constrained | `403 UNSUPPORTED_SECURE_QUERY` |

Responses expose stable reason codes and `decision_id`, not hidden policy text,
pre-filter counts, sensitive property names, or protected values.

## 14. Caching, revocation, and audit

Cache active memberships, compiled policy ASTs, and schema-validation results.
Do not cache final protected query results in the authorization layer.

Cache keys include:

```text
user_id + agent_id + policy_epoch + ontology_id + policy_revision
```

An organization or policy transaction increments affected users' policy epochs,
invalidates Redis cache entries, and revokes affected delegated credentials.

Audit events cover:

- organization and membership changes;
- policy create, validate, simulate, publish, rollback, and revoke;
- OAuth consent, token refresh, and revocation;
- authorization decisions for protected operations;
- secure-query adapter and enforcement failures.

Use the existing governance audit outbox so audit persistence does not add a
synchronous remote dependency. Mask tokens, row values, and sensitive request
inputs.

## 15. Delivery decomposition

### Phase 1: Secure Ontology access

- Subject Context, PDP, and decision contract.
- Secure Ontology Reader.
- Enforce existing user grants in SQL reads and relation traversal.
- Route REST and Tool Gateway through the common reader.
- Decision audit and stable failures.

This phase is a prerequisite for all other phases.

### Phase 2: Enterprise organization and policy control plane

- Departments, groups, memberships, authorization subjects, and policy epoch.
- Subject-aware Allow/Deny policies.
- Draft, validation, simulation, publication, rollback, and explanations.
- Permission Management and Ontology Security frontend surfaces.

### Phase 3: Protected, framework-neutral MCP

- OAuth discovery, PKCE, audience binding, delegated-token lifecycle.
- Streamable HTTP MCP server, generic tools, and authorization context.
- Multi-Ontology Agent bindings and Agent MCP management UI.
- AgentScope and LangGraph E2E; standard client contract tests; AutoGen smoke.

### Phase 4: Defense in depth and production hardening

- PostgreSQL role separation and RLS.
- Neo4j policy compilation and safe query restrictions.
- Chroma authorization intersection.
- Performance, observability, audit retention, and security gates.
- Identity-provider interface without a concrete SSO implementation.

Each phase receives its own implementation plan and review gate. No phase may
temporarily enable an unfiltered fallback.

## 16. Verification matrix

### 16.1 Policy composition

Cover the Cartesian combinations of platform role, direct user grant,
department grant, group grants, Allow/Deny, validity/status, Agent binding, and
OAuth scope. Required cases include:

- user Allow plus group Deny;
- department Allow plus user Deny;
- two groups allowing different entities;
- visible row with hidden properties;
- allowed relation whose endpoint is hidden;
- user authority without Agent authority and the reverse;
- authorized empty result.

### 16.2 Adversarial security

- prompt injection requesting that authorization be ignored;
- model-supplied user, department, or group identity;
- cross-Ontology tool parameter substitution;
- wrong audience and cross-client token use;
- token replay, expiry, refresh rotation, logout, and revocation;
- direct REST access attempting to bypass MCP;
- arbitrary Cypher and unsafe NL2Cypher;
- inference through ordering, pagination, counts, errors, or relation IDs;
- sensitive audit-log access.

### 16.3 Transport parity

For the same subject and query, React REST, the built-in Agent, AgentScope MCP,
and LangGraph MCP must return identical visible object/property/relation sets
and compatible reason codes.

### 16.4 Storage parity

- SQL and Neo4j evaluate equivalent supported policies consistently.
- Connection pool reuse never retains another subject's RLS context.
- Neo4j or vector-store failure either uses an authorized PostgreSQL fallback
  or returns a closed failure.
- Vector over-fetch never returns an unauthorized result to fill a page.

## 17. Proposed performance acceptance targets

- Cached authorization decision P95 at or below 15 ms.
- Uncached authorization decision P95 at or below 50 ms.
- Authorization change effective within 2 seconds.
- Ordinary protected list-query P95 overhead no greater than 30 percent against
  the same dataset and query without fine-grained filters.
- Token refresh is transparent to the user when the user session remains valid.
- Audit uses an outbox and does not wait for an external sink.

These are initial engineering targets, not contractual customer SLAs. Rebaseline
them with representative customer datasets; never disable filtering to meet a
latency target.

## 18. Migration and rollout

Use Expand, Migrate, Shadow, Enforce, Contract:

1. Add new tables and nullable compatibility columns without removing legacy
   structures.
2. Create a user authorization subject for each existing user.
3. Migrate existing user grants to user-subject policies.
4. In a staging environment, run old and new capability decisions in shadow
   mode and audit differences. Fine-grained row/property enforcement is never
   shadow-only in production because the legacy reader is not a sufficient
   security boundary.
5. Resolve decision differences, then deploy Secure Ontology Reader enforcement
   atomically with the production schema migration. If the new enforcement path
   is unavailable, protected reads fail closed.
6. Add departments and groups.
7. Add protected MCP OAuth and tools.
8. Mark legacy data-grant endpoints deprecated.
9. Remove old paths only after at least one stable compatibility release.

The production configuration has no "authorization unavailable means allow"
switch.

## 19. External references

- [Model Context Protocol authorization specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)
- [OpenFGA authorization through organization context](https://openfga.dev/docs/modeling/organization-context-authorization)
- [OpenFGA search with permissions](https://openfga.dev/docs/interacting/search-with-permissions)
- [Google Zanzibar paper](https://research.google/pubs/zanzibar-googles-consistent-global-authorization-system/)
- [PostgreSQL row security policies](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)
- [Palantir object and property security policies](https://www.palantir.com/docs/foundry/object-permissioning/object-security-policies)
- [OPA deployment model](https://www.openpolicyagent.org/docs/deploy)
- [LangChain MCP adapters](https://docs.langchain.com/oss/python/langchain/mcp)
- [Microsoft AutoGen MCP Workbench](https://microsoft.github.io/autogen/stable/reference/python/autogen_ext.tools.mcp.html)
- [LlamaIndex MCP guide](https://docs.llamaindex.ai/en/stable/module_guides/mcp/)
- [Semantic Kernel MCP plugins](https://learn.microsoft.com/en-us/semantic-kernel/concepts/plugins/adding-mcp-plugins)

## 20. Explicit non-goals

- SaaS multi-tenancy.
- Service accounts or unattended Agent identities.
- A concrete OIDC, SAML, or LDAP connector.
- Replacing the existing write/Action/approval model.
- Arbitrary administrator-authored executable policy code.
- Mandatory OpenFGA, OPA, Cedar, or another external policy service.
- Recovering or deleting data already delivered to a model before a later
  authorization change.
