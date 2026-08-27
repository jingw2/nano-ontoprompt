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
- execution only when the plan's `managed_action_binding_id` and binding
  version still resolve to the same published, non-revoked binding and
  connection target; any binding or connection drift is rejected before a
  transaction begins;
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

Cross-transport parity is an explicit contract, tiered per the Milestone 2/3
scope amendment below (v1; not the original undifferentiated requirement).
Given equivalent verified principals, snapshot, inputs, and policy state, all
four transports — REST, SDK, MCP, and the reference Agent — must produce the
same normalized semantic result: decision, reason code, snapshot/release
pins, evidence citations, rule outcome, and result body. REST and the Python
SDK are additionally held to a byte-identical canonical `plan_hash` for
equivalent action proposals. MCP and the reference Agent are compatibility/
reference adapters (see "Product boundary and principles" above): they must
match the same normalized decision content but are not required to produce a
byte-identical `plan_hash` in v1 — a coding agent implementing Task 19 must
not treat MCP or the reference Agent's internal serialization as needing
hash-level parity with REST/SDK. The normalization and hash exclude HTTP/MCP
envelopes, transport metadata, tracing/request IDs, generated storage IDs,
and presentation-only fields; those values must not create semantic
divergence.

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
  semantic result on all four transports; equivalent action proposals have
  the same canonical plan hash independent of transport metadata between
  REST and the Python SDK, and the same normalized decision (without a
  byte-identical hash requirement) for MCP and the reference Agent — tiered
  parity per the Milestone 2/3 scope amendment.
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
- For both database dialects, integration tests must reject execution before
  any transaction when the plan's `managed_action_binding_id` or binding
  version drifts, the binding changes from published to draft or revoked, or
  the fixed connection binding/target drifts; the tests must verify that no
  target row is changed and that the rejection is structured.
- Security tests prove an Agent cannot supply SQL, identifiers, connection
  targets, or secrets, and no production write bypasses snapshot-backed
  Sandbox, dual-principal policy evaluation, idempotency, or audit.
- A low-risk reversible deterministic plan can execute automatically; a
  higher-risk or ambiguous plan requires HITL; stale, unauthorized, invalid,
  unsupported, or failed-precondition plans are rejected with a structured
  reason.
- The end-to-end trace connects investigation evidence and pipeline lineage
  to the Sandbox diff, decision, production receipt, and reconciliation state.

## Cross-milestone real-model business journey acceptance

The following is the binding acceptance contract for the product path that a
seed enterprise will actually use. It is a final Phase 2/3 gate in addition
to the component, transport, database, refresh, and operator gates above.
The gate has three independent business journeys that share one execution
substrate and one evidence contract:

```text
versioned multimodal inputs
  -> Pipeline execution and Curated review
  -> real DeepSeek ontology creation/completion
  -> published ontology release
  -> governed MCP descriptors and ontology data grants
  -> Agent creation and binding (release + tools + model version)
  -> Playwright login and browser conversation
  -> citations + tool trace + audit verification
  -> low-risk reversible Sandbox automatic execution
  -> high-risk exact-plan HITL (approve / reject / expire)
  -> isolated production-like writeback receipt and reconciliation evidence
```

### Shared real-model gate contract

- The only model accepted by this gate is the official DeepSeek model ID
  `deepseek-v4-flash-vision-exp`. The gate performs an exact `/models`
  preflight against the canonical origin `https://api.deepseek.com`, requires that ID
  in the returned model list, sends that exact ID in every ontology and Agent
  request, and requires the response's model ID to match. A missing model,
  endpoint incompatibility, response mismatch, or unavailable
  `DEEPSEEK_API_KEY` is a failure. There is no fallback to another model.
- The production client has no configurable model `base_url` or endpoint
  environment variable. It constructs only `https://api.deepseek.com/models`
  and `https://api.deepseek.com/chat/completions`, validates HTTPS with the
  exact official hostname and no userinfo/alternate port/query/fragment, and
  disables redirects (a non-official `Location` is a failure). Unit tests may
  inject an `httpx` transport, but that transport is never configurable by the
  real gate; the official key can therefore be sent only to the canonical
  DeepSeek origin.
- Every trusted same-repository branch pull request runs the real-model gate
  as a blocking job. The workflow uses `pull_request`, not
  `pull_request_target`; a pull request whose head repository is not the base
  repository fails with `TRUSTED_BRANCH_REQUIRED`, and an absent secret fails
  with `DEEPSEEK_API_KEY_REQUIRED`. The gate never converts either condition
  into a skip or a successful no-op. The first gate step compares the exact
  `github.event.pull_request.head.repo.full_name` with
  `github.repository`; the job has no push trigger and rejects any non-PR
  invocation.
- A network timeout or HTTP 429 receives exactly one bounded exponential
  backoff retry. The second failure, every other HTTP/provider error, malformed
  response, semantic assertion failure, seed failure, or browser failure is a
  hard failure with no additional retry. There is no unbounded Agent/tool
  loop.
- Each journey has an explicit fixed-cost budget. `logical_model_calls` counts
  semantic provider completions, while `http_attempts` counts actual provider
  requests. The state machine is exactly: (1) one ontology completion,
  (2) one Agent-turn initial completion that selects one MCP/query tool, and
  (3) one final completion after that single tool result. Thus one journey
  permits exactly `logical_model_calls == 3` and exactly one tool round; the
  low-risk result and high-risk action plan are both assertions on that final
  response. Approval, rejection, expiry, and reconciliation branches reuse
  the persisted action plan and make no further model call. A timeout/429
  retry does not consume a new logical call, but does consume one additional
  HTTP attempt; each logical call has at most two attempts, so one journey has
  `http_attempts <= 6`. Across the three journeys the hard maxima are
  `9 logical_model_calls` and `18 http_attempts`. The gate fails if either
  counter exceeds its budget; it must not silently reduce assertions or
  substitute a canned answer.
- Model output is never compared as a full string. Each journey declares a
  semantic minimum: required entity/relation/rule/action keys, source
  citations, stable tool descriptor IDs, expected decision/risk class, and
  acceptable case-folded keywords or numeric/structured predicates. The
  validator may accept varied wording only when all required business
  invariants hold; a generic non-empty answer is insufficient.
- The artifact bundle is an allowlisted `JourneyArtifact` object containing
  only `schema_version`, run/journey IDs, fixture-manifest hash, requested
  and observed model IDs, logical-call/HTTP-attempt/retry counters and UTC
  timestamps, semantic-validation outcomes, state-transition names, stable
  citation/tool/audit/trace IDs, and paths to sanitized browser reports or
  screenshots. A fixture-derived forbidden-value set contains every raw
  source cell, document/input string, prompt-injection sentinel, secret,
  authorization/Bearer/JWT form, PII pattern, and production URL; recursive
  byte scanning rejects any forbidden value before upload. Raw model
  responses/prompts, source rows/documents, headers, keys, and protected
  data are never retained. Browser traces, screenshots, and logs are treated
  as potentially leaking source/PII content and must be redacted into the
  allowlist format or omitted. Artifacts are uploaded on success and failure
  only after the sanitized scanner passes.

### Journey execution order and ownership

The journey registry is an executable contract. Every case record declares an
`execution_mode` and the plural `test_targets` field with exactly one target
descriptor. Deterministic normal, edge, security/governance,
runtime-resilience, and writeback cases use `execution_mode: "deterministic"`
and one exact pytest target. The `normal-pipeline-release` case for each of
`supply_chain`, `finance`, and `credit` uses
`execution_mode: "real_model_browser"` and one exact Playwright target; these
three real-model normal journeys are separate from the deterministic registry
cases and are never selected by the deterministic runner.

The sole registry runner is
`backend/tests/runtime/run_registered_cases.py`, invoked as:

```text
python -m tests.runtime.run_registered_cases --manifest <manifest> --report <report>
```

It selects only deterministic cases, executes every case's single target
exactly once, makes zero model calls, writes a sanitized per-case report, and
fails closed on a missing or duplicate target, a non-zero result, or a skip.
Target collection/listing is supplemental and cannot satisfy acceptance.

The final gate has one mandatory order. First, the deterministic registry
runner must pass and its zero-model-call report must be validated. Second,
`prepare_journey(journey_id)` runs for all three journeys and owns only
Pipeline execution, Curated approval, one real multimodal ontology
completion, release publication, MCP descriptor/grant creation, and immutable
model configuration selection; it returns `AgentBindingOptions` and never
creates an Agent or binding, starts a turn, invokes a tool, evaluates
governance, creates an approval, or executes Sandbox/writeback. Third, the
three named Playwright tests create and bind the Agent through
`AgentCreateWizard`, then own the sole Agent turn: one initial model
completion, exactly one governed tool call/result, and one final model
completion. The browser also owns the low-risk automatic Sandbox outcome and
the independent exact-plan HITL approve, reject, and expired outcomes. No API
runner creates the Agent, persists its binding, or runs its turn.

Only after all three browser tests finish, `verify_journey(journey_id)` runs
for all three journeys. It reads persisted binding, trace, citation, tool,
audit, Sandbox receipt, HITL receipt/status, target hashes, and model-budget
records; it performs no model call or mutation and fails on missing evidence or
an invalid one-tool/three-logical-call budget. Only after verification passes
may the allowlisted artifact scanner, report validation, and upload run.

Preparation contributes one logical model call per journey and the browser
turn contributes two, for exactly three logical calls and at most six HTTP
attempts per journey. Deterministic registry execution and post-browser
verification contribute zero model calls; the three journeys together are
capped at nine logical calls and eighteen HTTP attempts.

### Journey-specific business contracts

All three journeys use one tabular source, one policy/report document, and a
deterministically rendered visual page derived from an existing PDF or
document. Existing files remain read-only inputs; the runtime fixture manifest
records their hashes and refers to them instead of copying them. New rows,
document projections, and identifiers use synthetic values only.

| Journey | Reused source assets and visual input | Semantic minimum | Low-risk automatic path | High-risk HITL path |
| --- | --- | --- | --- | --- |
| Supply chain | `test_data/供应链/inventory_transactions.csv`, `supplier_database.xlsx`, `procurement_policy.docx`, and a fixed rendered page from `warehouse_management.pdf` | Entities `Supplier`, `PurchaseOrder`, `InventoryItem`, `Warehouse`; relations `SUPPLIES`, `PLACED_WITH`, `CONTAINS`, `BELOW_SAFETY_STOCK`; rule `inventory_below_safety_stock`; action descriptors for `risk_label` and `purchase_order_price_update`; evidence includes inventory and policy source hashes | Recommendation, supplier/inventory risk label, and snapshot-backed Sandbox simulation execute automatically and are reversible | Purchase price or purchase-order production update requires the exact immutable plan hash and HITL approval; reject and expire leave the target unchanged |
| Finance | `test_data/财务/financial_data.xlsx`, `cash_flow.csv`, `expense_reports.csv`, `month_end_close.docx`, and a fixed rendered page from `audit_report.pdf` | Entities `Account`, `Invoice`, `Expense`, `CostCenter`, `AccountingPeriod`; relations `POSTED_TO`, `BELONGS_TO`, `DUPLICATES`, `EXCEEDS_BUDGET`; rules `duplicate_invoice` and `expense_over_budget`; action descriptors for `risk_label` and `journal_entry`; evidence includes accounting-period and audit source hashes | Recommendation, expense risk label, and cash-flow Sandbox scenario execute automatically and are reversible | Journal/accounting adjustment or funds/payment action requires exact-plan HITL; reject and expire produce no ledger/funds change |
| Credit | `test_data/信贷/贷款申请记录.csv`, `客户档案信息.csv`, `还款流水.csv`, `风控审批政策.docx`, and a fixed rendered page from `贷后催收报告.pdf` | Entities `Borrower`, `LoanApplication`, `Repayment`, `CreditLine`, `RiskAssessment`; relations `APPLIES_FOR`, `HAS_REPAYMENT`, `ASSESSED_AS`, `USES_CREDIT_LINE`; rule `credit_score_limit`; action descriptors for `risk_label` and `credit_limit_update`; evidence includes application, repayment, and policy source hashes | Recommendation, borrower risk label, and credit-limit Sandbox simulation execute automatically and are reversible | Credit approval, credit-limit, or approval-status production update requires exact-plan HITL; reject and expire produce no state change |

Each journey must prove the following states against the same run's IDs:

- Pipeline and Curated review succeed before ontology extraction is accepted;
  the resulting DatasetVersion/PipelineRun lineage is pinned to the
  published release and later SemanticSnapshot.
- The real model creates or completes the ontology from the multimodal parts,
  the semantic minimum validator passes, and a human-review/publish step
  creates an immutable release. An incomplete, duplicate, malformed, or
  semantically insufficient model result fails the journey.
- MCP descriptors are generated from that release, expose stable descriptor
  IDs, and are granted only through an active ontology data grant. The Agent
  binding must contain the release ID, selected MCP/query/action tools, and an
  immutable model configuration version whose model ID is exactly
  `deepseek-v4-flash-vision-exp`.
- The browser logs in as the fixture operator, opens `AgentCreateWizard`,
  selects the already-published ontology release, granted MCP descriptors,
  and exact model configuration, then creates the Agent and verifies the
  persisted binding before sending the governed business question. It shows
  an answer with the expected semantic keywords, release/snapshot citation,
  tool invocation, and correlation-linked audit trace. API seeding may stop at
  Pipeline/Curated and the published ontology/descriptor/grant baseline; an
  API-created Agent or binding is not browser acceptance, nor is a screenshot
  script.
- The low-risk case produces an immutable Sandbox diff and automatic receipt
  without per-action HITL. The high-risk case creates an approval for one
  exact plan hash. Separate fixed checks approve it and verify the isolated
  target receipt, reject it and verify no target mutation, and present an
  expired approval and verify no target mutation. The test target is a
  disposable PostgreSQL production-like fixture; no real enterprise system is
  contacted. PostgreSQL and MySQL writer parity remains covered by the Phase
  3 dialect integration matrix.

### Required coverage matrix

The journey corpus and its registered tests must cover every row below. The
normal path uses the real model; deterministic transport doubles are used for
failure injection so resilience tests do not consume uncontrolled model
budget.

| Dimension | Required cases and assertions |
| --- | --- |
| Normal business data | Non-empty Pipeline, approved Curated rows, ontology minimums, release, MCP grant, Agent binding, cited decision, and one automatic Sandbox receipt for each journey |
| Data edges | Empty/no-match result remains `ALLOW` with an empty result; duplicate entities/invoices/applications are deduplicated; missing policy field, malformed numeric value, schema drift, equal watermark, and source/document disagreement produce an explicit quality or semantic outcome rather than silent data loss |
| Model/semantic edges | Exact model preflight, response-model mismatch, malformed structured output, missing required relation/rule/action, unsupported modality, insufficient citation, and a semantically irrelevant answer all fail closed with a diagnostic reason |
| Security/governance | Missing/expired/revoked grant, stale release/snapshot, cross-tenant/domain access, caller identity spoofing, unauthorized MCP descriptor, prompt injection in an input document, arbitrary SQL/secret/target override, and policy denial never expose protected data or execute a write |
| Runtime resilience | One timeout retry, one 429 retry, second retry failure, non-retryable provider error, MCP timeout, worker retry/DLQ, duplicate turn/idempotency, SSE disconnect/reconnect, cancellation, and stale precondition preserve durable state and produce traceable outcomes |
| Writeback governance | Low-risk reversible recommendation/risk label/Sandbox scenario runs automatically; high-risk purchase/order, accounting/funds, or credit/limit/approval action requires exact-plan HITL; approve writes the isolated target, reject/expire do not, unknown outcome creates reconciliation, and rollback is a new governed plan |

### Runtime fixture layout and reuse boundary

Create these directories without modifying the existing domain corpus:

```text
test_data/runtime/supply_chain/
  manifest.json  inputs.json  semantic_minima.json  dialogues.json
  governance.json  case_matrix.json  reproducibility.json
test_data/runtime/finance/
  manifest.json  inputs.json  semantic_minima.json  dialogues.json
  governance.json  case_matrix.json  reproducibility.json
test_data/runtime/credit/
  manifest.json  inputs.json  semantic_minima.json  dialogues.json
  governance.json  case_matrix.json  reproducibility.json
```

`inputs.json` contains read-only source references, synthetic tabular rows,
the rendered visual-page reference, media types, and SHA-256 hashes.
`semantic_minima.json` contains the required names/relations/rules/actions,
acceptable keywords, numeric predicates, and source citation IDs.
`dialogues.json` contains one governed Agent turn whose final response has a
normal investigation/automatic-action result and one high-risk proposal. The
turn has one initial completion, exactly one MCP/query tool result, and one
final completion; its budget is `logical_model_calls: 3`,
`max_tool_rounds_per_turn: 1`, and `max_http_attempts: 6`. The approval,
rejection, and expiry checks reuse that persisted high-risk plan without
calling the model again.
`governance.json` contains exact expected states for automatic, approved,
rejected, and expired plans, including target-before/after hashes and the
absence of a write for rejected/expired paths. `case_matrix.json` maps the
normal, edge, security/governance, runtime-resilience, and writeback cases to
backend/API and Playwright targets. `reproducibility.json` fixes seed,
canonical JSON rules, UTC timestamps, source hashes, prompt/schema version,
model ID, and the derived manifest hash.

The existing `test_data/供应链/`, `test_data/财务/`, and `test_data/信贷/`
assets are reusable evidence inputs, not complete runtime acceptance data:
they lack a release/grant/Agent binding contract, expected semantic minima,
governance outcomes, deterministic dialogue cases, and artifact hashes. The
new per-journey files add those contracts and may derive one fixed visual
page during fixture generation; no production data or secret is introduced.

### Final acceptance decision

The three journeys are independent cases but share the same CI job and
fixture/runtime infrastructure. The job is `PASS` only when all three report
`status=passed`, every declared case has a non-skipped registered backend or
Playwright target, all requested/observed model IDs equal
`deepseek-v4-flash-vision-exp`, the retry count never exceeds one, all
artifacts pass redaction scanning, and every automatic/HITL/writeback
invariant above is present. Any missing secret, model mismatch, skip, seed
soft-failure, failed semantic predicate, missing citation/tool/audit event,
or unauthorized side effect is `FAIL`.

## Milestone 2/3 scope amendment (2026-08-27)

Milestone 1 shipped and passed its own acceptance criteria in this
repository, including a real `docker compose up --build` against an empty
database (not only unit tests) — verified by
`scripts/verify_m1_stabilization_gate.sh`, wired into CI as the
`m1-stabilization-gate` job. That experience surfaced three real defects
(missing `pgcrypto` extension, an `alembic_helpers` import-path bug, an
unpinned CI npm version) that no unit or static test had caught, because
none of them exercised a live database/Compose/CI environment. Two
standing rules follow from that experience, and apply to every milestone
below, not only M1:

- **Milestone 1 is a hard prerequisite.** No Milestone 2 task begins work
  against a checkout where the M1 gate (including the live Compose smoke
  test) is not green.
- **Real execution is required evidence, not only unit tests.** Every
  milestone-ending task must include at least one check against a live,
  non-mocked dependency (a real database via Compose, a real broker-backed
  worker, or a real browser) in addition to unit tests, before that
  milestone is considered acceptance-complete. A seed customer now depends
  on this system, which raises the cost of a defect that only unit tests
  would have missed.

With a seed customer now in place, the following scope changes were made to
the Milestone 2/3 implementation plan to front-load correctness on the path
the seed customer will actually exercise, and to defer speculative
generality this roadmap does not yet have evidence to justify. These are
plan-level (task-breakdown and interface) changes; the milestone-level
acceptance criteria above are otherwise unchanged, with the two exceptions
called out explicitly:

1. **Event-driven refresh drops ordered CDC/sequence-partition delivery
   from v1 scope.** `RefreshPolicy.event_driven` is implemented via managed
   webhook and managed outbox adapters only, using the same
   `watermark_primary_key`/`opaque_source_cursor` cursor contracts as batch
   and polling refresh — at-least-once delivery with event-ID dedupe, no
   partition/sequence ordering or gap-detection machinery. A dedicated
   `sequence_partition` cursor contract and CDC adapter remain a known,
   named future extension, revisited when *either* of two conditions is
   met — not only infrastructure readiness: (a) a real CDC-producing source
   exists to design against, or (b) a contracted customer's requirements
   explicitly need ordered delivery, gap detection, or a strict freshness
   SLA that at-least-once-with-dedupe cannot satisfy. Waiting for (b) to
   surface only after a production customer hits an ordering/gap incident
   would be reactive; this is a standing trigger to revisit proactively, not
   a floor on how bad the incident has to be first. Building ordering/gap/
   replay machinery against a synthetic local double before either trigger
   fires was judged premature generality.
2. **Cancellation is simplified for v1.** A cancellation request is
   best-effort and race-tolerant: it is honored if the run has not yet
   reached a durable terminal outcome, and is silently superseded (reported
   as "already finished") if the outcome commits first. Phase 2 drops the
   `outcome_committed_at` marker and the distinct `CANCELLATION_TOO_LATE`/
   `CANCELLATION_NOT_APPLICABLE` reason codes; it never claims to roll back
   a committed outcome, which was the only correctness property the more
   elaborate mechanism protected.
3. **The Python-versus-alternate-runtime capacity/extraction decision is
   deferred**, not implemented, until real Phase 2 production load exists
   against the seed customer's workload. A synthetic local capacity harness
   measured before real traffic exists would not produce a decision worth
   trusting; the named Celery queues and worker limit contracts Phase 2
   already ships are sufficient to measure real load later without a
   rewrite.
4. **Cross-transport parity is tiered.** REST and the Python SDK remain
   byte-identical (same canonical `plan_hash`, same normalized result).
   MCP and the reference built-in Agent — already framed above as
   compatibility adapters and a reference implementation, not the product's
   defining layer — must match the same normalized *decision*
   (ALLOW/DENY, reason code, snapshot pin, evidence), but are not held to
   byte-identical hash parity in v1.
5. **PostgreSQL and MySQL writer coverage are both required in Phase 3, on
   the original schedule** — reconsidered and explicitly reaffirmed, not
   cut.
6. **An additional real-execution integration checkpoint runs at the end of
   the refresh subsystem** (after durable refresh contracts, scheduling,
   polling, and event ingestion are implemented and before the semantic
   Runtime work begins), modeled on the M1 gate: a live Compose environment
   drives one real batch refresh and one real event-driven refresh through
   the actual Celery queues and asserts a durable `DatasetVersion` lands.
   This is in addition to, not instead of, the Milestone 2 release gate.

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
- Ordered CDC/sequence-partition event delivery and a real CDC producer
  adapter (deferred per the Milestone 2/3 scope amendment above until a real
  CDC source exists to design against).
- A measured Python-versus-alternate-runtime capacity/extraction decision
  before real Phase 2 production load exists (deferred per the amendment
  above).
- Byte-identical cross-transport hash parity for MCP and the reference
  Agent (tiered per the amendment above; REST/SDK parity is unchanged).
