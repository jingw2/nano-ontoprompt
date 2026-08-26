# Ontexus: Enterprise Agent Decision & Governance Infrastructure

## Status

Approved product positioning and implementation roadmap. This document
defines the contracts and scope for stabilization, Phase 2, and Phase 3; it
is intentionally not a general Agent-platform roadmap.

## Product definition

**Ontexus is enterprise infrastructure for Agent-assisted business decisions
and governed execution.** It turns enterprise data into versioned, governed
domain semantics, then exposes a common runtime that enterprise-owned
Copilots and Agents can use to investigate, decide, and act safely.

The primary user is the data or ontology engineer. Business experts
collaborate to validate the domain model and approve material actions, while
the enterprise remains free to supply its own Copilot or Agent experience.

The product chain is:

```text
Enterprise data
  -> Pipeline and versioned semantic foundation
  -> Semantic Runtime (API / Python SDK / MCP)
  -> Snapshot-backed Sandbox and policy decision
  -> Automatic execution or HITL
  -> Governed production writeback
```

## Product boundary and principles

Ontexus is not a general-purpose Agent Builder, chat product, or replacement
for an enterprise's existing Copilot UX. Agent configuration and the built-in
Agent UI remain useful as a reference implementation, integration verifier,
and operator surface, but they are not the product's defining layer.

The core promise is:

> Any enterprise Agent can access business facts and perform business actions
> only through the current semantic, identity, and governance boundaries.

The implementation follows four boundaries:

- The Pipeline and semantic foundation provide the facts, versions, quality,
  and evidence that a decision is allowed to use.
- One transport-neutral Semantic Runtime owns semantic investigation, policy,
  action-plan creation, and audit behavior.
- API, Python SDK, MCP, and the built-in Agent are adapters to that Runtime;
  none has a separate authorization or action implementation.
- Production writes use published, managed actions and a server-side writer;
  an Agent never supplies SQL, database identifiers, connection targets, or
  secrets.

## Architecture

```text
Enterprise data
  -> Data & Semantic Foundation
  -> Decision Runtime
  -> Governed Execution
  -> Enterprise Copilot / Agent
```

### Data & Semantic Foundation

Owns reliable business context:

- Data connections, pipeline execution, transformations, data quality, and
  version lineage.
- Ontologies containing entities, relations, rules, actions, evidence, and
  publication state.
- Immutable ontology releases that define a semantic contract.
- Immutable semantic snapshots that define the exact facts and provenance
  available to a decision.

#### SemanticSnapshot contract

`OntologyRelease` and `SemanticSnapshot` are separate contracts. A release is
the versioned semantic/schema contract; it is not permanently tied to one
batch of data. A `SemanticSnapshot` is an immutable materialized view of a
release and must bind all of the following:

- one `ontology_release_id`;
- the complete set of referenced `dataset_version` IDs;
- the complete set of originating `pipeline_run` IDs;
- a quality summary and an evidence summary for those inputs; and
- a canonical `materialization_hash` over the release, inputs, and summaries.

A snapshot can reference only completed, governed pipeline inputs and a
published ontology release. Its contents and provenance cannot be edited in
place; a new materialization creates a new snapshot. Investigations and
action plans pin a `semantic_snapshot_id` (and derive/include its release
identity), never an ontology release alone. Runtime responses expose both
snapshot and release citations so a result can be reproduced against the
same facts and semantic rules.

### Decision Runtime

Owns governed decision support:

- Semantic investigation, retrieval, explanation, and evidence retrieval.
- Domain-rule evaluation and action eligibility against a pinned snapshot.
- A single contract exposed by REST/API, Python SDK, and MCP.
- Delegated Semantic Access, in which every request has both a registered
  Agent/service identity and a delegated end-user identity.

The effective permission is the intersection of:

```text
Agent capability ∩ user entitlement ∩ runtime policy
```

Agent capability controls which integrations, ontology capabilities, tools,
and actions an Agent may invoke. User entitlement controls the data scope and
business authorization of the represented person. Runtime policy evaluates
the snapshot, release, rule constraints, data state, and risk controls.

#### Trustworthy Delegated Semantic Access

The v1 protocol uses registered identities and credential-derived delegation:

1. An enterprise registers an Agent/service identity in a tenant and security
   domain, with its capabilities and allowed Runtime audience.
2. The service authenticates with its service credential. A user is
   authenticated through the configured identity provider, then a short-lived
   delegated credential is issued using OAuth 2.0 token-exchange semantics.
3. The delegated credential contains verified actor/Agent and user subjects,
   `audience`, `scope`, `security_domain`, issue/expiry times, and a unique
   token identifier. The server controls its TTL and supports revocation or
   introspection by token, user session, or registered Agent.
4. The Runtime verifies issuer/signature, audience, scope, expiry, revocation,
   Agent registration, and same-security-domain membership before evaluating
   capabilities and user entitlements.

REST, Python SDK, MCP OAuth, and the built-in Agent all use this same
verification path. `agent_id` or `user_id` supplied in a request body, query,
or MCP argument is never treated as identity authority; principals are derived
only from validated credentials. Missing, mismatched, expired, or revoked
delegation is denied with a structured reason code.

#### Investigation result contract

`investigate` returns a structured result even when access is denied:

```text
InvestigationResult {
  decision: ALLOW | DENY
  reason_code
  semantic_snapshot_id
  ontology_release_id
  evidence_citations[]
  rule_outcome
  result
  correlation_id
}
```

`ALLOW` means the request was authorized and evaluated; it may contain an
empty result set. `DENY` is distinct from “no matching data” and must expose a
stable reason code without leaking protected result content. The response is
the same normalized semantic result over every transport.

#### Action-plan contract

`create_action_plan` validates Delegated Semantic Access, the pinned snapshot,
the action's policy, and its current preconditions, then persists one
immutable plan without performing a production write. For a writable action,
the plan references the published managed action binding; read-only plans may
omit that reference. The plan includes:

- the semantic snapshot and ontology release pin;
- Agent and delegated-user principals derived from the verified credential;
- input facts, evidence citations, and rule outcomes used for the proposal;
- the immutable `managed_action_binding_id` and binding version, frozen typed
  parameters, normalized target primary-key tuple (or the final result of
  resolving a selector), the target's `before_image_hash` and `version_hash`,
  predicted diff, and impact scope;
- risk classification, policy decision, precondition hashes, expiry, and an
  idempotency key; and
- a canonical `plan_hash` over semantic plan fields, including the binding
  identity/version, frozen typed parameters, normalized target, and target
  before-image/version hashes.

An action plan is the unit of approval for one exact immutable proposal, not
ongoing write authority. It contains no database passwords, tokens, or other
connection secrets.

### Governed Execution

Owns the transition from a proposed operation to a production change:

- A v1 Sandbox simulates a managed action against a pinned
  `SemanticSnapshot`, producing an immutable diff, impact summary, and
  precondition hashes without production side effects.
- Policy routes an eligible plan to automatic execution or exact-plan HITL.
- A server-side production writer revalidates identity, policy, snapshot,
  action binding, and production preconditions before execution.
- Execution records the outcome, audit evidence, idempotency state, and any
  reconciliation case.

#### Sandbox v1 boundary

Sandbox v1 is **snapshot-backed action simulation**. It may evaluate a
published action's typed inputs against the snapshot and calculate expected
before/after state, affected rows, rule outcomes, and impact. It is not an
arbitrary Agent code sandbox, prompt sandbox, or tool sandbox; it does not run
untrusted Agent code, execute arbitrary prompts/tools, or call production
connectors during simulation. Broader Agent behavior simulation is outside
this roadmap.

#### Managed action binding and database safety

Every writable action is published with a fixed, versioned binding. The
binding fixes the `managed_action_binding_id`, connection target identity, SQL
dialect, table, primary-key columns, writable columns, and version/precondition
column or expression. During plan creation, a target selector is resolved
against the pinned snapshot and its normalized final primary-key tuple is
frozen in the plan; a plan must always carry that tuple (or an equivalent
frozen selector result) and the target's before-image/version hashes. Only
typed business parameters may be supplied when creating the plan. Execution
accepts only the plan identity/hash and uses its frozen parameters and target;
an override is rejected. Agents cannot provide SQL, table/column identifiers,
connection targets, transaction options, or a replacement target selector for
execution.

Phase 3 v1 supports allowlisted, parameterized, single-target row updates in
PostgreSQL and MySQL. The writer must enforce:

- server-side minimum-privilege credentials, kept out of plans and audit
  records;
- dialect-specific transactions, statement timeouts, and exact row-count
  checks;
- optimistic locking against the published version precondition;
- execution only against the plan-frozen primary-key tuple and frozen typed
  parameters, with rejection when either caller parameters differ or the
  target's before-image or version hash no longer matches; runtime Agent
  parameters must never reselect the target row;
- idempotency and execution fencing shared by automatic and HITL paths; and
- rejection of arbitrary SQL, DDL, multi-target transactions, and destructive
  deletes.

For reversible changes, a before-image or compensating descriptor is retained
according to data policy. Rollback is a new action plan subject to the same
binding, authorization, policy, Sandbox, and HITL/automatic decision; it is
never an implicit privileged write.

## Risk-based execution policy

Every production action is classified by risk and determinism.

| Class | Conditions | Outcome |
| --- | --- | --- |
| Automatic | Low risk, reversible, deterministic, policy-approved, valid dual-principal access, and unchanged preconditions | Execute without per-action HITL; retain full audit and reconciliation. |
| Human-approved | High impact, irreversible, sensitive, ambiguous, or above policy threshold | Require HITL approval of the exact immutable action-plan hash. |
| Rejected | Missing authorization, stale snapshot/policy/identity, failed precondition, unsupported binding, or failed Sandbox validation | Do not execute; return a structured explanation. |

HITL approval grants execution of one exact plan. Plans expire and must be
rebuilt if their semantic snapshot, policy, identity, or relevant production
preconditions change. Automatic execution does not bypass any of those
checks.

## Canonical execution flow

```text
Investigate (snapshot + evidence + rules)
  -> Sandbox simulation
  -> immutable action plan
  -> risk/policy decision
  -> automatic execution OR exact-plan HITL
  -> production writeback
  -> audit + reconciliation
```

Production writeback is limited to approved managed connectors/actions. The
first database implementation is PostgreSQL and MySQL; additional enterprise
system connectors must use the same managed-binding contract and are outside
this stabilization roadmap.

## Public Runtime interfaces

The following are one semantic contract, not separate feature paths:

- REST/API endpoints under `/api/v2/runtime` for investigation, action-plan
  creation, plan retrieval, and execution-status retrieval.
- Python SDK v1 with a `RuntimeClient` that accepts a credential/delegation
  provider and exposes typed `investigate`, `create_action_plan`,
  `get_action_plan`, and `get_execution_status` methods. It returns typed
  `InvestigationResult` and `ActionPlan` values and typed errors carrying
  `decision`, `reason_code`, `correlation_id`, and snapshot information. The
  SDK contains no alternate policy or write implementation.
- MCP tools that are thin adapters for the same Runtime methods and verified
  OAuth delegation context. Existing MCP read/proposal entry points remain
  compatibility adapters while they migrate to the shared service.
- The built-in Agent runtime calling the same service, retained as a
  reference client and operator surface.

Cross-transport parity is an explicit contract. Given equivalent verified
principals, snapshot, inputs, and policy state, REST, SDK, MCP, and the
reference Agent must produce the same normalized semantic result: decision,
reason code, snapshot/release pins, evidence citations, rule outcome, and
result body. Equivalent action proposals must produce the same canonical
`plan_hash`. The normalization and hash exclude HTTP/MCP envelopes, transport
metadata, tracing/request IDs, generated storage IDs, and presentation-only
fields; those values must not create semantic divergence.

## Implementation milestones

Milestones are sequential release gates. Every listed item closes a required
runtime/release contract or a known current-version failure; unrelated Agent
features and broad refactors are deferred.

### Milestone 1 — Stabilize the current version

Implementation:

- Repair `docker-compose.v2.yml` and `docker-compose.agent.yml`: remove the
  stale `0017_mcp_write_requests` migration-head assumption, use the image's
  current build-manifest/migration source of truth, and run migrations before
  backend/workers against a fresh database. Dependents wait for successful
  migration completion, not merely database health.
- Restore the frontend TypeScript CI build by fixing the OAuth consent test's
  `window.location` mock without weakening type checking.
- Update schema-startup contracts for the current migration head and memory
  tables, and set explicit pytest-asyncio loop-scope configuration.
- Run frontend CI once outside the Python-version matrix while retaining the
  backend Python 3.11/3.12 matrix.
- Align README runtime requirements and Docker migration instructions with the
  actual frontend package and startup behavior.

Acceptance:

- A fresh `docker compose -f docker-compose.v2.yml up --build` reaches healthy
  frontend/backend services against an empty database; the agent Compose
  configuration validates the same migration contract.
- Backend tests, frontend `npm run test:ci`, focused build-manifest and
  schema-startup tests, and Compose configuration validation pass with no
  stale-head or missing-table failures.
- Stabilization changes do not modify existing untracked user data or add
  unrelated product behavior.

### Milestone 2 — Unified Semantic Runtime

Implementation:

- Add the immutable `SemanticSnapshot` contract and lineage capture for the
  complete dataset-version/pipeline-run set, quality/evidence summaries, and
  materialization hash. Require snapshot pins in investigations and action
  plans.
- Add the shared credential verification and Runtime context for registered
  Agent/service identity, credential-derived user delegation, audience,
  scope, TTL, revocation, and same-security-domain checks. Reject
  caller-supplied identity IDs as authority.
- Implement the shared `investigate` service with structured ALLOW/DENY,
  stable reason code, snapshot/release pins, evidence citations, and rule
  outcome; implement `create_action_plan` as an immutable, non-writing
  proposal path.
- Expose the service through versioned REST/API, Python SDK v1, MCP adapters,
  and the reference Agent. Keep existing endpoints as compatibility adapters
  where required, with no duplicated policy logic.
- Add normalized-result and canonical-plan-hash parity tests that exclude
  transport metadata.

Phase 2 acceptance:

- An external enterprise Agent can investigate and create a governed action
  plan through both REST/SDK and MCP using the same verified delegated access,
  snapshot, evidence, rule, and policy semantics.
- Tests prove denial for missing/expired/revoked or cross-domain delegation,
  Agent-only or user-only authorization, policy denial, and stale snapshot or
  lineage; authorized empty results remain ALLOW rather than being confused
  with denial.
- Equivalent cross-transport investigations return the same normalized
  semantic result, and equivalent action proposals have the same canonical
  plan hash independent of transport metadata.
- Phase 2 performs no production write; all action plans remain immutable
  proposals for Phase 3 execution.

### Milestone 3 — Sandbox and governed PostgreSQL/MySQL writeback

Implementation:

- Add a versioned Sandbox record linked to an action plan and snapshot,
  capturing dual principals, managed binding, expected rows, before/after
  diff, impact summary, rule/policy result, expiry, and precondition hashes.
  Simulation is snapshot-backed and has no production side effects.
- Add the risk-policy evaluator for automatic, human-approved, and rejected
  outcomes. Automatic execution is limited to low-risk, deterministic,
  reversible, policy-approved plans with unchanged preconditions; all other
  permitted writes require HITL approval of one exact plan hash.
- Publish and enforce managed action bindings with fixed connection identity,
  dialect, table, primary key, writable columns, and version precondition.
  Implement the first server-side writer for parameterized single-target row
  updates in PostgreSQL and MySQL with minimum-privilege secrets, per-dialect
  transactions/timeouts/row-count checks, optimistic locking, idempotency,
  and execution fencing.
- Unify automatic and approved execution through that writer and record audit,
  receipts, and reconciliation. Recheck credentials, policy, snapshot,
  binding, and target-row preconditions immediately before commit. Unknown
  outcomes create reconciliation cases and are never blindly replayed.
- Expose operator surfaces for Sandbox/action-plan diff, approval queue,
  execution receipt, reconciliation status, and lineage trace. Rollback is
  represented only as a new governed action plan.

Phase 3 acceptance:

- PostgreSQL and MySQL integration tests cover authorized automatic update,
  HITL update, exact-plan approval, stale-plan rejection, policy/identity/
  snapshot drift, precondition conflict, row-count mismatch, idempotent retry,
  unknown-outcome reconciliation, rollback-plan creation, and rejection when
  action parameters or selector resolution drift from the plan-frozen target
  primary-key tuple or its before-image/version hashes.
- Security tests prove an Agent cannot supply SQL, identifiers, connection
  targets, or secrets, and no production write bypasses snapshot-backed
  Sandbox, dual-principal policy evaluation, idempotency, or audit.
- A low-risk reversible deterministic plan can execute automatically; a
  higher-risk or ambiguous plan requires HITL; stale, unauthorized, invalid,
  unsupported, or failed-precondition plans are rejected with a structured
  reason.
- The end-to-end trace connects investigation evidence and pipeline lineage
  to the Sandbox diff, decision, production receipt, and reconciliation state.

## Non-goals for this roadmap

- Competing with generic multi-Agent orchestration, chat application, or Agent
  Builder platforms.
- A separate business-policy implementation for MCP versus API/SDK.
- Treating an ontology release without a pinned SemanticSnapshot as sufficient
  evidence for a decision or write.
- An arbitrary Agent code, prompt, or tool sandbox.
- Agent-provided SQL, identifiers, connection targets, secrets, or
  ungoverned production writes.
- Arbitrary SQL/DDL, multi-target transactions, or destructive deletes in the
  first PostgreSQL/MySQL writer.
- A broad connector catalog or unrelated refactoring that does not improve
  release correctness, the shared Runtime contract, or governed execution.
