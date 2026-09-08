# Ontexus Agent Semantic Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Stabilize the current Ontexus release and implement the approved enterprise Agent decision and governance infrastructure: versioned semantic snapshots, one transport-neutral Runtime, trusted delegated access, snapshot-backed simulation, and governed PostgreSQL/MySQL writeback.

**Architecture:** The existing pipeline and ontology release become the data and semantic foundation. A durable refresh layer normalizes batch, micro-batch polling, and bounded event-driven changes into versioned DatasetVersion/PipelineRun inputs with cursor and lineage state. A shared RuntimeService owns snapshot-pinned investigation, policy evaluation, action-plan creation, and execution contracts; REST, Python SDK, MCP, and the built-in Agent are adapters to it. Phase 3 adds an immutable managed-action plan, snapshot simulation, risk routing, and a server-side row writer with one exact target and parameter set.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL 16, MySQL 8, Redis/Celery, Python 3.11/3.12, React 19, TypeScript 6, Vitest, Playwright 1.60, and the existing OAuth/PKCE implementation.

**Spec:** docs/superpowers/specs/2026-08-26-agent-semantic-infrastructure-design.md

## Global Constraints

- The design spec is the business authority; this plan does not add a general Agent Builder, chat product, arbitrary Agent sandbox, connector catalog, or unrelated refactor.
- Python remains >=3.11; backend CI keeps Python 3.11 and 3.12; frontend CI uses Node 22.14.0 and npm 11.2.0.
- Python remains both the Phase 2 control plane and governed execution plane. This plan does not rewrite the backend from Python to Go, Java, or Rust; a separate connector executor may be proposed only by the capacity decision in Task 10A and must preserve the refresh and snapshot contracts.
- FastAPI request handlers only authenticate/authorize, validate request shape, persist a durable command/status record, and enqueue a durable ID. They never pull a connector, materialize CDC, run Pandas/PyArrow transforms, execute a replay loop, or perform managed writes inline.
- Celery is the Phase 2 execution boundary. Named queues/routes are `refresh.schedule`, `refresh.poll`, `refresh.event`, `refresh.replay`, `artifact.extraction`, `agent.interactive`, and `housekeeping`; a refresh task message contains only its durable `run_id` and task name, never source arguments, cursors, credentials, SQL, or payloads.
- Local/reference Compose topology uses explicit queue-bound worker roles and profiles in both Compose files. Worker role names alone are not queue isolation; every worker command passes `-Q` with its allowlisted queue set, bounded concurrency/prefetch/time limits, late acknowledgement, and worker-loss redelivery for refresh tasks. Compose is a reference topology, not a claim of Kubernetes or autoscaling support.
- The per-source/resource lease remains the correctness owner under backpressure. Queue depth, oldest queued age, run lag, retry, DLQ, broker, and worker readiness are observable without exposing message payloads, credentials, or PII; queue saturation retains a durable queued/backpressured run and never drops or executes it inline.
- Graceful shutdown and soft timeout leave a durable retryable run, while explicit cancellation leaves a durable terminal `cancelled` run that can be safely superseded only by a new run; both preserve the prior source cursor and make at-least-once redelivery safe through the existing lease, fencing, idempotency, and dedupe contracts.
- Cancellation is a durable, best-effort, race-tolerant state transition (see the Milestone 2/3 Scope Amendment for the full contract): a queued run may be cancelled immediately; a running run records `cancel_requested_at`, `cancel_requested_by`, and `cancel_reason`, and the worker transitions it to `cancelled` at the next safe point (a connector page boundary or before tentative materialization) if the run has not already reached a durable terminal outcome. If the outcome commits first, the cancellation request is superseded and reported as `already_terminal`; this is never reported as a successful cancellation and never claims to roll back production state.
- PostgreSQL 16 remains the primary application database. Phase 3 writer coverage includes PostgreSQL and MySQL 8 with synthetic databases only.
- Every Runtime entry point calls the same RuntimeService; adapters contain transport mapping only and never implement alternate authorization, policy, hashing, or writes. REST and the Python SDK are held to byte-identical canonical-hash parity; MCP and the reference built-in Agent are compatibility/reference adapters and match the same normalized decision (ALLOW/DENY, reason code, snapshot pin, evidence) without a byte-identical hash requirement (Task 19).
- Enterprise refresh supports only the explicit `RefreshPolicy` values `batch`, `micro_batch`, and `event_driven`; all ingestion is at-least-once, cursor/checkpoint-driven, idempotent, and produces immutable DatasetVersion/PipelineRun lineage before a new SemanticSnapshot. Every mode uses the `watermark_primary_key` or `opaque_source_cursor` cursor contract; there is no per-partition sequence/ordering contract in this plan (deferred, see amendment).
- `RefreshSourceState` is the authoritative singleton for each `(source_id, resource)`. Claims and cursor changes are lease/fencing guarded; a stale worker can never advance a cursor.
- A source configuration change is one transaction: it increments the authoritative `config_version`, changes the cursor contract/configuration, clears the active source lease, and invalidates its existing fencing token. Every outcome compares the run-frozen revision/contract before lease/owner/fencing validation; a run that claimed the prior revision can only fail closed with typed `CONFIGURATION_DRIFT`, cannot create lineage, and cannot advance a cursor.
- Event-driven refresh is limited to managed webhook and managed outbox adapters in v1; an ordered CDC adapter contract is a deferred future extension (see amendment). This plan is not an arbitrary stream-processing platform and does not implement or accept arbitrary Kafka/broker sources.
- A source cursor advances only after the input DatasetVersion is durable and the associated PipelineRun outcome is recorded; overlap re-reads and retries must not mutate an existing DatasetVersion or SemanticSnapshot.
- Milestone 1 (Tasks 2-5) must stay green — including the live `scripts/verify_m1_stabilization_gate.sh` Compose smoke test, not only unit tests — before any Milestone 2 task begins; this repository's M1 work is complete and verified. Every milestone-ending task (Task 10's mid-milestone checkpoint, Task 20, Task 28, and the final business-journey Task 33) additionally requires at least one real, non-mocked execution check (a live Compose service, a live broker-backed worker, a real model, or a real browser), because M1 itself surfaced three defects (missing `pgcrypto`, an `alembic_helpers` import-path bug, an unpinned CI npm version) that only a live environment — not unit tests — caught.
- SemanticSnapshot is immutable and binds one published ontology release, the complete dataset-version set, the complete originating pipeline-run set, quality and evidence summaries, and a canonical materialization hash.
- Runtime identity is derived only from a verified credential. Request-body, query-string, or MCP agent_id/user_id values never establish authority.
- Effective access is Agent capability ∩ user entitlement ∩ runtime policy; missing, mismatched, expired, revoked, or cross-security-domain delegation returns a structured denial.
- InvestigationResult always carries ALLOW or DENY, stable reason_code, snapshot/release pins, evidence citations, rule outcome, result, and correlation ID. An authorized empty result is ALLOW.
- Writable plans contain no secrets. A published managed binding fixes connection target, dialect, table, primary-key columns, writable columns, and version precondition; execution accepts only plan identity and hash.
- Phase 3 v1 permits only allowlisted, parameterized, single-target row updates. Arbitrary SQL, DDL, multi-target transactions, and destructive deletes are rejected.
- Automatic execution requires low risk, reversibility, determinism, policy approval, valid dual-principal access, and unchanged preconditions. Other permitted writes require exact-plan HITL approval.
- Rollback is a new governed action plan. Unknown outcomes create reconciliation cases and are never blindly replayed.
- Tests use deterministic no-PII fixtures. Existing untracked files under ref/ and test_data/ are read-only inputs and must not be added, rewritten, or deleted. The runtime corpus may use only public test sentinels such as runtime/runtime, vault:runtime-db, and example.invalid; these are not real credentials and must never be used outside test infrastructure.
- Runtime fixture manifests never contain raw access tokens, refresh tokens, signing keys, private keys, cloud credentials, production URIs, or real email/phone values. Test-only database passwords remain only in the isolated database Compose file and are scanned as the literal public sentinel runtime.
- The fixture registry is authoritative: every case has an `execution_mode` and exactly one runnable target in its plural `test_targets` list for its declared normal, edge, security/governance, runtime-resilience, or writeback coverage. Deterministic cases use one pytest target and are executed by the sole registry runner; the three normal business-journey cases use one exact Playwright target with `execution_mode == "real_model_browser"` and are excluded from the deterministic runner. Database cases name both postgresql and mysql, parity cases name rest, sdk, mcp, and reference-agent, refresh cases name their refresh mode and source contract, and E2E cases name a Playwright target. Task 28 adds a registry-driven runner that executes every deterministic registered case and fails on a missing/duplicate target, non-zero result, or skip; the final Task 33 gate asserts both exact target coverage and execution results. Collect/list checks are supplemental and never the acceptance evidence.
- The final acceptance corpus has three independent fixed-cost journeys: `supply_chain`, `finance`, and `credit`. Each journey runs versioned multimodal inputs through Pipeline/Curated review, real DeepSeek ontology create/complete and publication, governed MCP descriptor/grant creation, Agent creation/binding, browser conversation, citation/tool/audit checks, low-risk automatic Sandbox execution, and high-risk exact-plan HITL approve/reject/expire checks.
- The only real-model gate model is `deepseek-v4-flash-vision-exp`. The production client has no model endpoint/base override: it validates and calls only the canonical HTTPS origin `https://api.deepseek.com`, with redirects disabled. The gate performs an exact official DeepSeek `/models` preflight, requires `DEEPSEEK_API_KEY`, sends and verifies that exact model ID, and never falls back. A timeout or HTTP 429 receives exactly one exponential-backoff retry; all other errors and the second retry failure fail the job. Unit tests inject an `httpx` transport only, and the CI contract rejects any model endpoint override.
- The real business-journey gate runs as a blocking job for trusted same-repository `pull_request` branches. Fork pull requests fail with `TRUSTED_BRANCH_REQUIRED`; a missing secret fails with `DEEPSEEK_API_KEY_REQUIRED`; neither condition is skipped or converted into a successful no-op. The workflow must not use `pull_request_target` to expose the secret to untrusted code.
- Real-model assertions use per-journey semantic minima, acceptable keywords, structured predicates, citation IDs, tool descriptors, risk classes, and audit invariants rather than full-string output comparison. The fixed state machine is one ontology completion plus one Agent turn with one initial completion, exactly one tool call/result, and one final completion: exactly `3` `logical_model_calls` per journey. Timeout/429 retries do not add logical calls but do add `http_attempts`; each call has at most two attempts, so each journey is capped at `6` HTTP attempts and the three-journey gate at `9` logical calls/`18` HTTP attempts. Exceeding either fails the gate.
- The final journey artifact is a schema-validated allowlist object containing only redacted model-response structure, requested/observed model IDs, timestamps/attempt counts, fixture hashes, backend trace IDs/event types, MCP audit IDs, Playwright trace references, and sanitized synthetic screenshots. A fixture-derived forbidden-value set scans model echo, prompt-injection text, raw fixture cells, secret/header/JWT forms, PII patterns, and production URLs before upload. Raw inputs, prompts, protected rows, credentials, authorization headers, and browser traces/screenshots/logs with source content never enter artifacts; unsafe browser evidence is redacted or omitted. Artifacts are uploaded on success and failure only after scanning.
- Every task follows TDD: add a focused failing test, run the named command and record the failure, implement the smallest change, run the named passing command, then commit only the listed files.

## Milestone 2/3 Scope Amendment (2026-08-27)

This subsection is a historical decision record only. Coding agents must
follow the active task bodies and release gates below; the removed identifiers
and contracts quoted here are not implementation requirements.

This amendment is binding and supersedes any conflicting text in Tasks 1,
6, 8, 9, 10, 19, 20, 27, and 28 below (superseded passages are trimmed to
point back here; where an old passage was missed, this section wins).
Rationale lives in the spec's own "Milestone 2/3 scope amendment" section;
this section is the concrete engineering contract an implementer follows.

**1. Simplified cancellation (replaces `outcome_committed_at`,
`CancellationTooLateError`, `CancellationNotApplicableError`,
`CANCELLATION_TOO_LATE`, `CANCELLATION_NOT_APPLICABLE` everywhere they
appeared in Tasks 6, 8, 9, 10, 27, 28):**

- `RefreshRun.status` keeps the state machine
  `queued|running|cancel_requested|cancelled|succeeded|failed|dead_lettered`.
  `RefreshRun` does **not** have an `outcome_committed_at` field.
- `request_refresh_cancellation(db: Session, *, run_id: str, requested_by: str, reason: str, now: datetime) -> RefreshRun`
  locks the run and authoritative `RefreshSourceState`, validates the run's
  frozen `config_version`, and validates operator authorization. For
  `queued` it transitions directly to terminal `cancelled`. For `running`
  it records `cancel_requested_at`, `cancel_requested_by`, `cancel_reason`,
  and the claimed fencing token, and sets status to `cancel_requested`. For
  `cancel_requested` it returns the same durable request unchanged
  (idempotent). For any already-terminal status (`succeeded`, `failed`,
  `dead_lettered`, `cancelled`) it makes **no mutation** and returns the
  run with `already_terminal=True`; this is the only terminal-state result
  and is never reported as a successful cancellation.
- `assert_refresh_not_cancelled(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, now: datetime) -> None`
  is unchanged in purpose: connector page boundaries, event-inbox/batch
  boundaries, and tentative-materialization boundaries call it, and it
  raises `RefreshCancellationRequested` when status is `cancel_requested`.
  A worker that observes this before its outcome transaction commits rolls
  back tentative materialization and calls `finalize_refresh_cancellation`
  before acknowledging; a worker whose outcome transaction commits first
  simply finishes as `succeeded`/`failed`/`dead_lettered`, and a
  cancellation request that arrives afterward gets the `already_terminal`
  result above. No commit marker is written or checked to distinguish
  these cases — the run's plain terminal `status` is sufficient.
- `finalize_refresh_cancellation(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, now: datetime) -> RefreshRun`
  is unchanged: it transitions `cancel_requested` to terminal `cancelled`,
  clears the lease, is idempotent for an already-`cancelled` run, and
  rejects a stale worker with `RefreshFencingError`.
- `record_refresh_outcome` re-checks `assert_refresh_not_cancelled` in its
  transaction immediately before writing lineage and the cursor CAS, exactly
  as before, but no longer writes an `outcome_committed_at` marker — the
  transaction committing at all, with status `succeeded`, is the durable
  fact.
- API: `POST /api/v2/refresh/runs/{run_id}/cancel` returns `202` with the
  durable `cancel_requested`/`cancelled` state on a live run, and `200`
  with `{"status": "<terminal status>", "already_terminal": true}` — not a
  `409` and not a typed error code — when the run had already reached a
  terminal outcome. `RefreshStatus`/`RefreshRun` schemas carry no
  `outcome_committed_at` field.
- UI: the operator surface shows a cancel control only for an authorized
  active run, disables it after any terminal state, and for a request that
  arrives too late shows a plain "run already finished; cancellation had no
  effect" message — not a distinct error state.

**2. Event-driven refresh: webhook + outbox only (replaces
`ManagedCdcAdapter`, `PartitionConsumeStatus`, `RefreshPartitionState`,
`sequence_partition`, `LocalCdcProducerDouble`, gap/hold/timeout/N+1
handling in Tasks 1, 6, 9, 10, 20, 27, 28):**

- The only supported `cursor_contract` values are `watermark_primary_key`
  and `opaque_source_cursor`, used by all three `RefreshPolicy` values
  (`batch`, `micro_batch`, `event_driven`). There is no
  `RefreshPartitionState` table and no per-partition checkpoint anywhere in
  this plan.
- `ManagedWebhookAdapter.verify_and_normalize(...)` and
  `ManagedOutboxAdapter.normalize(...)` (Task 9) keep their signature/
  timestamp/schema checks and normalize into `ChangeEnvelope` exactly as
  originally specified, minus the `partition`/`sequence` fields.
- `EventIngestService.accept(db: Session, envelope: ChangeEnvelope, *, lease_owner: str, now: datetime) -> IngestReceipt`
  persists the inbox idempotently keyed on `(source_id, resource,
  event_id)` (unchanged dedupe semantics), then treats the event exactly
  like a polling delta page: it claims the source run/fencing token, calls
  `assert_refresh_not_cancelled`, and calls `record_refresh_outcome` using
  whichever `cursor_contract` (`watermark_primary_key` or
  `opaque_source_cursor`) the source is configured with. There is no
  partition lock, no expected-next-sequence check, and no
  `held_gap`/`gap_timeout`/`replayed` inbox status; the only inbox
  processing states are `received`, `duplicate`, `processed`, and
  `dead_lettered`.
- `PartitionConsumeStatus`, `ManagedCdcAdapter`, and
  `LocalCdcProducerDouble` are not implemented in this plan. A future CDC
  adapter and its ordering contract are an explicit non-goal here (spec
  amendment item 1) and get their own brainstorming cycle when a real CDC
  source exists to design against, *or* when a contracted customer's
  requirements explicitly need ordered delivery, gap detection, or a strict
  freshness SLA the at-least-once-with-dedupe webhook/outbox path cannot
  satisfy — whichever trigger fires first. The second trigger is deliberately
  proactive: it does not wait for a production ordering/gap incident to
  justify revisiting the decision.
- Task 9's title and scope shrink to: "Add bounded event-driven refresh
  adapters (webhook + outbox) with inbox dedupe." Its fixture cases drop
  `cdc-ordering`, `two-partitions`, `n-plus-one-held`, `n-arrives-releases`,
  and `gap-timeout` and keep `expired-webhook`, `replay-attack`,
  `schema-drift`, `config-drift-late-finish`, and `duplicate`.

**3. Task 10A is deferred, not implemented** (full replacement task body
below supersedes Task 10A's original Files/Interfaces/Steps). No capacity
harness, capacity profile fixture, live-worker-consumption proof, or ADR is
built in this plan. `RefreshWorkerLimits` and the named Celery queues from
Task 6A remain in place and are the contract a future capacity measurement
task would reuse.

**4. Task 19 transport parity is tiered**, per the updated Global
Constraints bullet above: REST/SDK stay byte-identical; MCP/reference-Agent
match the normalized decision only.

**5. Both PostgreSQL and MySQL writer coverage stay exactly as scoped** in
Tasks 24-26; this was reconsidered and explicitly reaffirmed, not cut.

**6. A mid-milestone real-execution checkpoint is added to Task 10's Step
4** (end of the refresh subsystem, before Milestone 2B's Runtime work
begins): a live Compose environment drives one real batch refresh and one
real event-driven (webhook) refresh through the actual Celery queues and
asserts a durable `DatasetVersion` lands, modeled on
`scripts/verify_m1_stabilization_gate.sh`. This is in addition to the
Milestone 2 and Task 28 release gates, not a replacement for them.

## File and Interface Map

| Area | Files | Responsibility |
| --- | --- | --- |
| Stabilization | docker-compose.v2.yml, docker-compose.agent.yml, .github/workflows/agent-mvp.yml, backend/scripts/verify_build_manifest.py, backend/pyproject.toml, README files | Current migration source of truth, service ordering, CI matrix, runtime documentation |
| Enterprise refresh | backend/app/models/v2/refresh.py, backend/app/schemas/refresh.py, backend/app/services/v2/incremental/*, backend/app/services/v2/scheduler/*, backend/app/tasks/topology.py, backend/app/tasks/v2/refresh_tasks.py, backend/app/tasks/celery_app.py, backend/app/routers/v2/refresh.py, backend/tests/v2/incremental/*, backend/tests/agent/test_celery_topology.py | Durable source refresh state, schedules, cursors, Python/Celery execution topology, backpressure/operability, polling, bounded event ingestion (webhook/outbox), retries/DLQ/replay, freshness, and refresh operations |
| Semantic foundation | backend/app/models/semantic_snapshot.py, backend/app/services/runtime/snapshots.py, backend/app/services/runtime/lineage.py, backend/app/schemas/runtime_snapshot.py | Immutable snapshot, complete input lineage, quality/evidence summaries, materialization hash |
| Runtime identity and policy | backend/app/models/runtime_identity.py, backend/app/services/runtime/credentials.py, backend/app/deps/runtime.py, backend/app/services/runtime/policy.py | Registered service identity, token-exchange delegation, verified context, intersection policy |
| Runtime contracts | backend/app/schemas/runtime.py, backend/app/models/runtime_plan.py, backend/app/services/runtime/service.py, backend/app/services/runtime/canonical.py | Investigation, action-plan, stable denial, canonical semantic results and hashes |
| Adapters | backend/app/routers/v2/runtime.py, sdk/ontexus_runtime/*, backend/app/services/mcp_tools.py, backend/app/services/runtime/reference_agent.py | REST, SDK, MCP, and built-in Agent adapters over one service |
| Governed execution | backend/app/models/managed_action.py, backend/app/models/sandbox.py, backend/app/models/runtime_execution.py, backend/app/services/runtime/sandbox.py, backend/app/services/runtime/risk.py, backend/app/services/runtime/execution.py, backend/app/services/runtime/writers/* | Managed binding, snapshot simulation, risk/HITL, writes, audit, idempotency, reconciliation, rollback |
| Test data | test_data/runtime/* | Versioned deterministic fixtures, generation manifest, synthetic PostgreSQL/MySQL databases, API/MCP/Playwright cases |
| Verification | backend/tests/runtime/*, backend/tests/runtime/integration/*, backend/tests/v2/incremental/*, backend/tests/agent/test_celery_topology.py, frontend/src/test/e2e/runtime-governance.spec.ts | Unit, integration, parity, security, drift, rollback, reconciliation, refresh topology/operability, and browser acceptance |
| Business journey acceptance | test_data/runtime/{supply_chain,finance,credit}/*, backend/evals/business_journeys/*, backend/tests/acceptance/*, scripts/run_business_journey_gate.sh | Fixed multimodal journey manifests, exact DeepSeek vision gate, semantic validators, full API state transition evidence, redacted artifacts, and three non-skippable browser journeys |
| Journey browser surface | frontend/src/test/e2e/business-journeys.spec.ts, frontend/src/test/e2e/fixtures/businessJourneys.ts, frontend/src/pages/agents/new/AgentCreateWizard.tsx, frontend/src/pages/agents/detail/ToolConfigTab.tsx, frontend/src/pages/agents/application/* | Browser login, Agent/release/tool binding, dialogue, citation/trace/audit evidence, Sandbox automatic action, and HITL approve/reject/expire interaction |

## Test-data Inventory and Generation Contract

### Existing assets that can be reused

The current corpus is sufficient for data-ingestion and ontology-authoring regression coverage:

- test_data/供应链/, test_data/信贷/, test_data/医疗/, test_data/教育/, test_data/财务/, test_data/法律/, test_data/营销/, and test_data/HR/ provide realistic domain documents and tabular inputs.
- test_data/documents/ provides compact cross-domain documents and CSV/XLSX inputs for pipeline smoke tests.
- test_data/edge_cases/{boundary,csv_structural,malformed,semantic,multi_source}/ and test_data/generators/* provide deterministic parser, structural, malformed, semantic, and multi-source cases.
- test_data/api/* and test_data/db/seed.sql provide existing API payload patterns and ontology/user seed conventions.
- backend/tests/{agent,v2,evals}/ provides backend test fixtures and current migration, pipeline, publication, OAuth, MCP, and evaluation patterns.
- frontend/src/test/e2e/, frontend/src/test/e2e/fixtures/, frontend/e2e-review/, and frontend/playwright.config.ts provide browser setup, authenticated fixtures, route helpers, and existing operator-flow conventions.

The existing corpus does not contain durable refresh lifecycle state. Its rows and schemas can be reused as source payloads, but refresh tests require a separate generated case file and synthetic source metadata; no existing production-like connection or event stream is treated as a refresh oracle.

### Required additions

The current assets do not cover the new contracts. Add a separate runtime corpus with synthetic IDs and no real credentials:

- Snapshot cases: valid published release with multiple dataset versions and pipeline runs, authorized empty result, incomplete lineage, failed or ungoverned pipeline input, stale snapshot, release drift, and reordered input lists.
- Identity cases: valid delegation, missing credential, malformed signature, wrong audience, missing scope, expired token, revoked token, inactive Agent, inactive user, cross-domain token, Agent-only capability, and user-only entitlement.
- Runtime cases: evidence citations, rule outcomes, ALLOW with data, ALLOW with no matches, structured DENY, policy denial, immutable read-only plan, writable plan binding, expired plan, and stable denial codes.
- Execution cases: low-risk reversible automatic update, high-risk exact-hash HITL update, ambiguous/rejected plan, binding draft/revoked state, binding version drift, connection-target drift, parameter/selector drift, before-image/version conflict, row-count zero/two, idempotent retry, timeout/unknown outcome, reconciliation, and rollback-plan creation.
- Refresh cases: normal non-empty batch, empty batch, late event, equal-watermark/different-primary-key, duplicate event, out-of-order event, failed retry, cursor non-advance, DLQ/replay, expired webhook, replay attack, schema drift, source configuration drift after claim, T+1 timezone/business-calendar run, bounded backfill, cancel-before-pull, cancel-inflight-page, and cancel-after-tentative-materialization. (Per the Milestone 2/3 Scope Amendment, ordered CDC/sequence-partition cases and a distinct `cancel-after-durable-outcome`-too-late case are not part of v1 scope; a cancellation request arriving after a run's outcome has already committed is the plain `already_terminal` case, not a separate fixture family.)
- Database cases: identical logical rows and version columns in PostgreSQL and MySQL, plus a second fixture whose target row changes between plan and execution.
- Transport cases: equivalent REST, SDK, MCP, and reference-Agent requests with different envelopes, request IDs, and presentation metadata.
- Business journey cases: three fixed multimodal journeys (`supply_chain`, `finance`, `credit`) covering normal, edge, security/governance, runtime-resilience, automatic Sandbox, and exact-plan HITL approve/reject/expire outcomes. These are the final full-stack cases and must not be represented by a mock-only eval or a self-skipping browser spec.

The existing domain directories are reusable source inputs, not complete
journey fixtures. The journey generator must reference them read-only and add
the missing runtime contract around them:

| Journey | Reused inputs | Generated additions |
| --- | --- | --- |
| `supply_chain` | `test_data/供应链/inventory_transactions.csv`, `supplier_database.xlsx`, `procurement_policy.docx`, `warehouse_management.pdf` | Synthetic source rows, one fixed PDF visual-page projection, semantic minima for `Supplier`/`PurchaseOrder`/`InventoryItem`/`Warehouse`, dialogue and purchase-order governance cases |
| `finance` | `test_data/财务/financial_data.xlsx`, `cash_flow.csv`, `expense_reports.csv`, `month_end_close.docx`, `audit_report.pdf` | Synthetic accounting rows, one fixed PDF visual-page projection, semantic minima for `Account`/`Invoice`/`Expense`/`CostCenter`/`AccountingPeriod`, dialogue and journal/funds governance cases |
| `credit` | `test_data/信贷/贷款申请记录.csv`, `客户档案信息.csv`, `还款流水.csv`, `风控审批政策.docx`, `贷后催收报告.pdf` | Synthetic borrower/application rows, one fixed PDF visual-page projection, semantic minima for `Borrower`/`LoanApplication`/`Repayment`/`CreditLine`/`RiskAssessment`, dialogue and credit-limit/approval governance cases |

Create the following per-journey files in addition to the generic runtime
fixture files. Every file is deterministic, hash-addressed, and scanned for
secrets/PII:

- `test_data/runtime/supply_chain/{manifest.json,inputs.json,semantic_minima.json,dialogues.json,governance.json,case_matrix.json,reproducibility.json}`
- `test_data/runtime/finance/{manifest.json,inputs.json,semantic_minima.json,dialogues.json,governance.json,case_matrix.json,reproducibility.json}`
- `test_data/runtime/credit/{manifest.json,inputs.json,semantic_minima.json,dialogues.json,governance.json,case_matrix.json,reproducibility.json}`

Add these deterministic files; generated output is checked by a manifest and is never populated with real secrets:

- test_data/runtime/README.md
- test_data/runtime/generate_runtime_fixtures.py
- test_data/runtime/manifest.json
- test_data/runtime/fixtures/snapshots.json
- test_data/runtime/fixtures/identities.json
- test_data/runtime/fixtures/runtime_cases.json
- test_data/runtime/fixtures/execution_cases.json
- test_data/runtime/fixtures/transport_cases.json
- test_data/runtime/fixtures/refresh_cases.json
- test_data/runtime/registry.py
- test_data/runtime/playwright_seed.json
- test_data/runtime/db/docker-compose.yml
- test_data/runtime/db/postgres/schema.sql
- test_data/runtime/db/postgres/seed.sql
- test_data/runtime/db/mysql/schema.sql
- test_data/runtime/db/mysql/seed.sql
- test_data/runtime/test_fixture_manifest.py

The generator exposes generate(seed: int, output_dir: Path) -> dict and a CLI with --seed 20260826, --output, and --check. --check regenerates into a temporary directory and compares canonical bytes and manifest hashes. Every fixture case has a stable case_id, expected decision or error code, expected hash where applicable, a layers list, a test_targets list, and a coverage list naming the contract it exercises. A target is one of:

- pytest: a full pytest node ID such as backend/tests/runtime/test_runtime_service.py::test_create_action_plan_is_immutable_and_non_writing;
- sdk: a full SDK pytest node ID such as sdk/tests/test_runtime_client.py::test_sdk_injects_delegation_and_decodes_investigation; or
- playwright: a spec path and exact test title such as frontend/src/test/e2e/runtime-governance.spec.ts::operator approves the exact plan hash and sees the receipt.

registry.py exposes targets_for(case_id: str) -> list[TestTarget], target_is_executable(target: TestTarget) -> bool, and assert_case_registry(case: Mapping[str, object]) -> None. Every case's `test_targets` list has exactly one descriptor. TestTarget has a kind plus an exact target; a Playwright target stores both the spec path and exact title. The registry validates descriptor syntax and `target_is_executable` runs the exact pytest node or Playwright title; a collect/list probe is supplemental and never acceptance evidence. assert_case_registry checks the manifest/registry bidirectional mapping, required fields, descriptor syntax, exact-one-target cardinality, and layer metadata; refresh cases additionally require `refresh_mode` in `{batch, micro_batch, event_driven}`, a `source_contract` in `{watermark_primary_key, opaque_source_cursor}`, an expected cursor/terminal outcome, and an optional typed `error_code`. Cancellation cases additionally declare `cancel_outcome` in `{requested, cancelled, already_terminal}` and `cursor_outcome`. The configuration-drift case must use `error_code == CONFIGURATION_DRIFT` and `cursor_outcome == unchanged`.

### Coverage matrix

| Test purpose | Fixture source | Required assertions |
| --- | --- | --- |
| Normal pipeline and ontology | Existing domain corpus plus snapshots.json | Completed lineage, published release, quality/evidence summaries, reproducible materialization |
| Source refresh | refresh_cases.json plus synthetic source rows/events and both SQL seeds | Batch/micro-batch/event-driven modes, source cursor monotonicity, at-least-once dedupe, schedule/SLA, retry/DLQ/replay, lag/provenance, configuration-revision drift fencing, and durable cancellation outcomes |
| Edge and boundary | Existing edge_cases/* plus reordered, empty, expired, and multi-input runtime cases | Empty result is allowed, hash is order-independent, limits and expiry are deterministic |
| Negative and security | identities.json, runtime_cases.json, execution_cases.json | Stable denial codes, no protected data leakage, no caller identity spoofing, no secret/SQL acceptance |
| Unit and function | JSON cases loaded by backend/tests/runtime/test_*.py and backend/tests/v2/incremental/test_*.py | Pure cursor ordering, envelope normalization, schedule calculation, dedupe, freshness, canonicalization, policy, target freezing, risk classification, and error mapping |
| API/SDK/MCP/reference parity | transport_cases.json | REST/SDK: same normalized result and canonical plan_hash, independent of envelopes and tracing IDs. MCP/reference-Agent: same normalized decision (Task 19 tiered parity) |
| PostgreSQL/MySQL integration | Both SQL seed directories and db/docker-compose.yml | Row update, exact row count, optimistic locking, timeout, binding drift, idempotency, unknown outcome |
| Python/Celery topology and operability | test_refresh_task_dispatch.py/test_refresh_operability.py | Exact named queues/routes, run-ID-only messages, bounded concurrency/prefetch/ack/time settings, graceful retry, queue depth/oldest age/run lag/retry/DLQ/readiness, and refresh saturation isolation from API/Runtime and agent/interactive dispatch |
| Cancellation and interruption | refresh_cases.json plus both dialect fixtures, test_refresh_cancellation.py, and refresh API/E2E targets | Best-effort fenced cancel request, cancel-before/inflight/after-tentative-materialization behavior, unchanged cursor/lineage, retry-safe soft timeout/shutdown, and the plain `already_terminal` result for a request that arrives after a run's outcome already committed |
| Playwright E2E | playwright_seed.json plus registry Playwright targets | Refresh schedule, config version/contract, cursor/lag, cancellation requested/cancelled/already-terminal visibility, configuration-drift failed status, failed/DLQ replay status, investigation citations, sandbox diff, automatic/HITL states, receipt, reconciliation, rollback-plan visibility |
| Three business journeys | test_data/runtime/{supply_chain,finance,credit}/* plus the business-journey registry | For each journey: multimodal Pipeline input, Curated approval, real DeepSeek ontology/release, MCP descriptors/grant, Agent release/tool/model binding, browser dialogue, semantic keywords/predicates, citation/tool/audit trace, automatic low-risk Sandbox outcome, and high-risk HITL approve/reject/expire outcomes |
| Real DeepSeek gate | backend/evals/business_journeys/* and scripts/run_business_journey_gate.sh | Exact `/models` model presence and response model ID, required secret/trusted-branch checks, one timeout/429 retry only, fixed call/tool budgets, no fallback, redacted artifacts, and zero skips |
| Case-to-test traceability | manifest.json plus registry.py | Task 1 validates descriptor/schema/metadata binding; Task 28 and final Task 33 verify every target is collectable/listable, with required dialects, transports, refresh modes, journey IDs, and Playwright markers |

### Task 1: Create the runtime test-data inventory and generator

- [ ] **Deliverable:** A generated, reviewable, deterministic runtime corpus exists without modifying any existing fixture.

**Files:**

- Create: test_data/runtime/README.md
- Create: test_data/runtime/generate_runtime_fixtures.py
- Create: test_data/runtime/manifest.json
- Create: test_data/runtime/fixtures/snapshots.json
- Create: test_data/runtime/fixtures/identities.json
- Create: test_data/runtime/fixtures/runtime_cases.json
- Create: test_data/runtime/fixtures/execution_cases.json
- Create: test_data/runtime/fixtures/transport_cases.json
- Create: test_data/runtime/fixtures/refresh_cases.json
- Create: test_data/runtime/registry.py
- Create: test_data/runtime/playwright_seed.json
- Create: test_data/runtime/db/docker-compose.yml
- Create: test_data/runtime/db/postgres/schema.sql
- Create: test_data/runtime/db/postgres/seed.sql
- Create: test_data/runtime/db/mysql/schema.sql
- Create: test_data/runtime/db/mysql/seed.sql
- Create: test_data/runtime/test_fixture_manifest.py

**Interfaces:**

- Produces generate(seed: int, output_dir: Path) -> dict and validate_manifest(output_dir: Path) -> None.
- Produces fixture records with case_id, expected, coverage, layers, execution_mode, and exactly one-element test_targets; uses synthetic tenants tenant-acme and tenant-beta, synthetic users, and synthetic row IDs.
- Produces PostgreSQL and MySQL schemas with the same managed_targets logical table, primary key target_id, writable field status, optimistic-lock field row_version, and synthetic refresh_source_rows/refresh_event_log tables containing stable source/resource/cursor/sequence fixtures.
- Produces test_data/runtime/db/docker-compose.yml with PostgreSQL published on host port 55432 and MySQL published on host port 53306, both with healthchecks on the project-scoped `runtime_fixture` network. Integration test jobs use a unique Compose project, refuse an occupied reserved port, and always run `down -v --remove-orphans`; these ports and volumes are isolated test infrastructure, never production.
- Produces a registry entry for every case. Each database case has dialects exactly ["mysql", "postgresql"], each parity case has transports exactly ["mcp", "reference-agent", "rest", "sdk"], each refresh case has one exact polling/event/schedule pytest target, and each E2E case has one Playwright target. Each case has exactly one `test_targets` element; deterministic cases use `execution_mode: "deterministic"`, while only the three normal journey cases use `execution_mode: "real_model_browser"` and the exact Task 32 Playwright title.
- Produces refresh cases with `refresh_mode` in `batch`, `micro_batch`, or `event_driven`, a `source_contract` in `watermark_primary_key` or `opaque_source_cursor`, and an expected `cursor_outcome` in `advanced`, `unchanged`, or `dead_lettered`. Cancellation cases include `cancel_outcome` in `requested`, `cancelled`, or `already_terminal`, `cursor_outcome`, and any expected `error_code`. The `config-drift-late-finish` case includes `error_code: CONFIGURATION_DRIFT`, `cursor_outcome: unchanged`, and exact backend/Playwright targets for the late-finish test.
- Produces test_no_pii_or_real_secret() in test_fixture_manifest.py. Its deterministic scan rejects `-----BEGIN .*PRIVATE KEY-----`, bearer/JWT strings, non-sentinel access_token/refresh_token values, `AKIA[0-9A-Z]{16}` or other cloud-account markers, `postgresql://`/`mysql://` production hosts, cloud-provider URIs, non-`.invalid` email domains, and phone patterns such as `[+]?[0-9][0-9 ()-]{8,}`; runtime/runtime, vault:runtime-db, and example.invalid are explicitly test-only sentinels.

- [ ] **Step 1: Write the failing manifest test.**

    def test_runtime_fixture_generation_is_deterministic(tmp_path):
        first = generate(20260826, tmp_path / "first")
        second = generate(20260826, tmp_path / "second")
        assert first["manifest_version"] == 1
        assert first["files"] == second["files"]
        validate_manifest(tmp_path / "first")

    def test_no_pii_or_real_secret():
        scan_runtime_corpus(Path("test_data/runtime"))

    def test_every_case_has_a_valid_registered_target_descriptor():
        for case in load_cases(Path("test_data/runtime/manifest.json")):
            assert_case_registry(case)
            targets = targets_for(case["case_id"])
            assert len(targets) == 1
            assert [target.to_dict() for target in targets] == case["test_targets"]

    def test_case_registry_metadata_is_complete():
        for case in load_cases(Path("test_data/runtime/manifest.json")):
            if "database" in case["layers"]:
                assert set(case["dialects"]) == {"mysql", "postgresql"}
            if "parity" in case["layers"]:
                assert set(case["transports"]) == {"mcp", "reference-agent", "rest", "sdk"}
            if "refresh" in case["layers"]:
                assert case["refresh_mode"] in {"batch", "micro_batch", "event_driven"}
                assert case["source_contract"] in {"watermark_primary_key", "opaque_source_cursor"}
                assert case["cursor_outcome"] in {"advanced", "unchanged", "dead_lettered"}
                if case["case_id"].startswith("cancel-"):
                    assert case["cancel_outcome"] in {"requested", "cancelled", "already_terminal"}
                if case["case_id"] == "config-drift-late-finish":
                    assert case["error_code"] == "CONFIGURATION_DRIFT"
                    assert case["cursor_outcome"] == "unchanged"
            assert case["execution_mode"] in {"deterministic", "real_model_browser"}
            assert len(case["test_targets"]) == 1
            if case["execution_mode"] == "deterministic":
                assert case["test_targets"][0]["kind"] == "pytest"
            else:
                assert case["test_targets"][0]["kind"] == "playwright"
                assert "journey completes the governed browser loop" in case["test_targets"][0]["title"]
            if "business_journey" in case["layers"]:
                assert case["journey_id"] in {"supply_chain", "finance", "credit"}
                assert case["model_id"] == "deepseek-v4-flash-vision-exp"
                assert case["skip_allowed"] is False
                assert case["fixture_manifest_sha256"]
                assert case["risk_class"] in {"automatic", "human_approved", "rejected"}

- [ ] **Step 2: Run the focused test to verify it fails.**

    Run: python -m pytest test_data/runtime/test_fixture_manifest.py -q

    Expected: FAIL because the runtime generator, registry, manifest contract, and no-secret checks do not exist.

- [ ] **Step 3: Implement the generator and fixtures.**

    Use one fixed seed, canonical JSON with sorted keys and UTF-8, fixed UTC timestamps, explicit expected outcomes for every normal, edge, negative, security, drift, rollback, reconciliation, PostgreSQL, MySQL, parity, SDK, MCP, reference-Agent, refresh, cancellation, and Playwright case, and no real credential material. `refresh_cases.json` must include stable cases named `normal`, `empty`, `late`, `equal-watermark`, `duplicate`, `out-of-order`, `failed-retry`, `cursor-nonadvance`, `dlq-replay`, `expired-webhook`, `replay-attack`, `schema-drift`, `config-drift-late-finish`, `backfill`, `t1-timezone`, `cancel-before-pull`, `cancel-inflight-page`, and `cancel-after-tentative-materialization`; `playwright_seed.json` must include schedule/cursor/config-version/lag, cancellation-requested/cancelled/already-terminal, and dead-letter/replay/configuration-drift states. `README.md` must document the simplified cancellation state machine (queued/running/cancel_requested/cancelled, no commit marker) and the safe-point/no-progress invariant. The generator must fail if a fixture lacks case_id, expected, coverage, layers, or test_targets. Store only public test sentinels runtime/runtime and vault:runtime-db in isolated test infrastructure; never write a signed token, private key, production URI, cloud account, real email, or real phone to a manifest or fixture. The scanner applies the same rules to every generated JSON, SQL, YAML, and Markdown byte, while allowing only those named sentinels.

    Register every case in registry.py with exactly one exact target descriptor and an explicit execution_mode. Deterministic cases are the only cases executed by the Task 28 runner and use one pytest target; the three normal journey cases use `real_model_browser` plus the exact Task 32 Playwright title and are executed only by the browser gate. Mark database cases with both dialects, parity cases with all four transports, refresh cases with their mode/source-contract/cursor-outcome metadata plus typed `error_code` for configuration drift, and E2E cases with an exact Playwright spec/title. The cancellation targets include unit/API/integration cases for cancel-before-pull, cancel-inflight-page, and cancel-after-tentative-materialization. The `config-drift-late-finish` target includes `backend/tests/v2/incremental/test_refresh_contract.py::test_configuration_upgrade_returns_configuration_drift_before_fence_check_without_lineage_or_progress`; if it is an E2E case its single target is the exact Playwright spec/title. Implement test_no_pii_or_real_secret() as a deterministic corpus scan and make the manifest test call it.

- [ ] **Step 4: Run generation and the focused test.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT" && python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime/generated)

    (cd "$REPO_ROOT" && python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime/generated --check)

    (cd "$REPO_ROOT" && python -m pytest test_data/runtime/test_fixture_manifest.py -q)

    (cd "$REPO_ROOT" && python -m pytest test_data/runtime/test_fixture_manifest.py::test_no_pii_or_real_secret -q)

    (cd "$REPO_ROOT" && python -m pytest test_data/runtime/test_fixture_manifest.py::test_every_case_has_a_valid_registered_target_descriptor -q)

    Expected: generation is idempotent, no PII or real-secret pattern is found, every manifest case has exactly one registry representation, and every target has valid kind/path/title syntax plus required dialect/transport/E2E/refresh metadata. Target collect/list checks run in Task 28 and the final Task 33 gate after the referenced tests exist.

- [ ] **Step 5: Commit only the runtime corpus.**

    git add test_data/runtime

    git commit -m "test: add semantic runtime fixture corpus"

### Milestone 1 — Stabilize the current version

### Task 2: Make build manifests and Compose migrations authoritative

- [ ] **Deliverable:** Both Compose files run a successful migration service before backend and worker services, without a hard-coded stale Alembic revision; every current worker/beat command loads `app.tasks.celery_app`, never the legacy `app.tasks.extraction` Celery application.

**Files:**

- Modify: backend/scripts/verify_build_manifest.py
- Modify: backend/tests/agent/test_build_manifest.py
- Create: backend/tests/agent/test_compose_migration_order.py
- Modify: docker-compose.v2.yml
- Modify: docker-compose.agent.yml

**Interfaces:**

- resolve_alembic_head(alembic_dir: pathlib.Path) -> str reads ScriptDirectory.get_heads() and rejects zero or multiple heads.
- verify_manifest(manifest: dict, expect_head: str | None = None, alembic_dir: pathlib.Path | None = None) -> None verifies the signature, required image roles, and, when an Alembic directory is supplied, compares the manifest head to the resolved current head.
- Both Compose files define a migration service that verifies /manifests/agent.json against /app/alembic and runs python scripts/run_migrations.py upgrade head; backend, API, and Celery services depend on service_completed_successfully.
- Every Celery worker and beat command uses `python -m celery -A app.tasks.celery_app ...`; `app.tasks.extraction` remains an included task module for the artifact queue but is not a Celery application entry point.

- [ ] **Step 1: Add failing structural and head-resolution tests.**

    def test_compose_uses_successful_migration_dependency():
        for compose in COMPOSE_FILES:
            services = load_compose(compose)
            assert "migration" in services
            assert "service_completed_successfully" in str(services["backend"])
            assert "0017_mcp_write_requests" not in compose.read_text()
            assert "app.tasks.celery_app" in compose.read_text()
            assert "-A app.tasks.extraction" not in compose.read_text()

- [ ] **Step 2: Run the stabilization tests to verify the existing failure.**

    Run: (cd backend && python -m pytest tests/agent/test_build_manifest.py tests/agent/test_compose_migration_order.py -q)

    Expected: FAIL on the stale 0017_mcp_write_requests expectation and missing v2 migration dependency.

- [ ] **Step 3: Implement the source-of-truth path.**

    Remove static --expect-head 0017_mcp_write_requests arguments, use manifest plus the actual Alembic directory for fail-closed verification, add the v2 migration service, make every Python service wait for migration completion after database health, and change the current worker/beat entry points to `-A app.tasks.celery_app`. Preserve the guarded launcher and signed-manifest checks; queue-specific `-Q` isolation is added in Task 6A.

- [ ] **Step 4: Run focused tests and Compose validation.**

    Run:

    (cd backend && python -m pytest tests/agent/test_build_manifest.py tests/agent/test_compose_migration_order.py -q)

    docker compose -f docker-compose.v2.yml config --quiet

    docker compose -f docker-compose.agent.yml config --quiet

    Expected: tests and both Compose configuration checks pass; no command contains the stale revision or `app.tasks.extraction` as the Celery application.

- [ ] **Step 5: Commit the migration contract.**

    git add backend/scripts/verify_build_manifest.py backend/tests/agent/test_build_manifest.py backend/tests/agent/test_compose_migration_order.py docker-compose.v2.yml docker-compose.agent.yml

    git commit -m "fix: make compose migrations authoritative"

### Task 3: Repair schema-startup and async test contracts

- [ ] **Deliverable:** Schema tests assert the current migration head and memory tables, and pytest-asyncio has explicit function-scoped loops.

**Files:**

- Modify: backend/pyproject.toml
- Modify: backend/tests/agent/test_schema_startup.py
- Modify: backend/tests/v2/models/test_v2_schema.py
- Create: backend/tests/agent/test_current_schema_contract.py

**Interfaces:**

- Pytest configuration sets asyncio_default_fixture_loop_scope = "function". The pinned pytest-asyncio==0.24.0 (backend/requirements.txt) does not recognize asyncio_default_test_loop_scope (verified against the installed plugin's addini() calls — it is a later-version option); setting it would itself emit a new "Unknown config option" PytestConfigWarning, so it is intentionally not set. Revisit only if pytest-asyncio is deliberately upgraded.
- Schema tests resolve the head from Alembic rather than maintaining a second revision constant and assert the current memory tables, including checkpoint and recall tables.
- The current schema command remains python scripts/run_migrations.py upgrade head followed by python scripts/verify_schema_revision.py.

- [ ] **Step 1: Add the failing current-schema assertions.**

    def test_schema_contract_uses_resolved_head_and_memory_tables():
        assert resolve_current_head() == "0021_mapping_entity_class_cn"
        assert {"agent_turn_checkpoints", "agent_turn_checkpoint_writes"} <= registered_tables()
        assert pytest_config()["asyncio_default_fixture_loop_scope"] == "function"

- [ ] **Step 2: Run the focused schema tests.**

    Run: (cd backend && python -m pytest tests/agent/test_schema_startup.py tests/v2/models/test_v2_schema.py tests/agent/test_current_schema_contract.py -q)

    Expected: FAIL where the old static contract or missing loop configuration is observed.

- [ ] **Step 3: Implement only the current-head and loop-scope corrections.**

    Derive the revision from Alembic, update the expected table set to the actual model registry, and place the single supported pytest-asyncio setting (asyncio_default_fixture_loop_scope) in backend/pyproject.toml. Do not weaken schema assertions or remove PostgreSQL-only checks.

- [ ] **Step 4: Run focused schema verification.**

    Run: (cd backend && python -m pytest tests/agent/test_schema_startup.py tests/v2/models/test_v2_schema.py tests/agent/test_current_schema_contract.py -q)

    Expected: PASS with no stale-head or missing-memory-table failure.

- [ ] **Step 5: Commit the schema contract.**

    git add backend/pyproject.toml backend/tests/agent/test_schema_startup.py backend/tests/v2/models/test_v2_schema.py backend/tests/agent/test_current_schema_contract.py

    git commit -m "test: align current schema contracts"

### Task 4: Restore typed frontend CI and separate it from the Python matrix

- [ ] **Deliverable:** TypeScript CI passes with a type-safe OAuth consent test, and frontend CI runs once outside the backend Python matrix.

**Files:**

- Modify: frontend/src/pages/oauth/OAuthConsentPage.test.tsx
- Modify: .github/workflows/agent-mvp.yml
- Modify: backend/tests/agent/test_ci_contract.py

**Interfaces:**

- The OAuth tests use vi.spyOn(window.location, "assign").mockImplementation(() => undefined) and assert its call; they do not assign an object to window.location and do not use a type-suppression comment.
- The workflow has one frontend-ci job using Node 22.14.0, npm ci, and npm run test:ci; the backend matrix retains only backend commands.
- The CI contract test asserts one frontend job and two backend Python versions.

- [ ] **Step 1: Add the failing type and workflow assertions.**

    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => undefined)
    await userEvent.click(screen.getByTestId('oauth-allow'))
    expect(assign).toHaveBeenCalledWith('https://client.example/cb?code=xyz&state=xyz')

    def test_frontend_ci_is_not_inside_python_matrix():
        workflow = load_workflow()
        assert workflow["jobs"]["frontend-ci"]["strategy"] == {}
        assert workflow["jobs"]["backend-matrix"]["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]

- [ ] **Step 2: Run the existing failing checks.**

    Run:

    (cd frontend && npm run test:ci)

    (cd backend && python -m pytest tests/agent/test_ci_contract.py -q)

    Expected: the current TypeScript build fails on the window.location assignment and the workflow contract reports frontend work inside the matrix.

- [ ] **Step 3: Implement the typed mock and workflow split.**

    Import vi, spy on the existing assign method in each navigation test, restore the spy after each test, and assert the URL argument. Add the standalone frontend job with the pinned Node setup and remove only the frontend step from the Python matrix.

- [ ] **Step 4: Run frontend and contract checks.**

    Run:

    (cd frontend && npm run test:ci)

    (cd backend && python -m pytest tests/agent/test_ci_contract.py -q)

    Expected: TypeScript, lint, unit coverage, and CI structure checks pass.

- [ ] **Step 5: Commit the CI correction.**

    git add frontend/src/pages/oauth/OAuthConsentPage.test.tsx .github/workflows/agent-mvp.yml backend/tests/agent/test_ci_contract.py

    git commit -m "ci: restore typed frontend verification"

### Task 5: Align runtime documentation and close the stabilization gate

- [ ] **Deliverable:** README instructions describe the actual runtime versions and migration-first startup, and the M1 gate is executable against an empty database.

**Files:**

- Modify: README.md
- Modify: README_zh.md
- Create: backend/tests/agent/test_stabilization_gate.py
- Modify: .github/workflows/agent-mvp.yml

**Interfaces:**

- Documentation names Python 3.11/3.12, Node 22.14.0, npm 11.2.0, current Alembic-head resolution, migration service completion, and the exact v2 and Agent Compose commands.
- test_stabilization_gate.py exposes test_compose_has_migration_first_startup() and test_documented_versions_match_package_metadata().
- The workflow invokes backend tests, frontend CI, manifest/schema focused tests, Compose config validation, and the existing agent E2E without changing unrelated evaluation behavior.

- [ ] **Step 1: Add the failing documentation/gate assertions.**

    def test_documented_versions_match_package_metadata():
        assert "Node 22.14.0" in readme_text()
        assert "npm 11.2.0" in readme_text()
        assert "service_completed_successfully" in compose_text()

- [ ] **Step 2: Run the M1 gate before the documentation fix.**

    Run: (cd backend && python -m pytest tests/agent/test_stabilization_gate.py -q)

    Expected: FAIL on version drift or absent migration-first instructions.

- [ ] **Step 3: Update only README runtime and migration instructions and wire the gate.**

    Keep product language consistent with the design: Ontexus is Agent decision and governance infrastructure, while the built-in Agent is a reference/operator surface. Add empty-database startup instructions and place the focused checks in the existing CI workflow.

- [ ] **Step 4: Run M1 checks.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/agent/test_stabilization_gate.py tests/agent/test_build_manifest.py tests/agent/test_schema_startup.py -q)

    (cd "$REPO_ROOT" && docker compose -f docker-compose.v2.yml config --quiet)

    (cd "$REPO_ROOT" && docker compose -f docker-compose.agent.yml config --quiet)

    set -e
    SMOKE_PROJECT=ontexus-m1-smoke
    SMOKE_VOLUME_FILTER="label=com.docker.compose.project=$SMOKE_PROJECT"
    cleanup() {
      docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" down -v --remove-orphans
    }
    trap cleanup EXIT
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" down -v --remove-orphans
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" up --build -d --wait
    MIGRATION_ID="$(docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" ps -q migration)"
    test -n "$MIGRATION_ID"
    test "$(docker inspect -f '{{.State.Status}}' "$MIGRATION_ID")" = "exited"
    test "$(docker inspect -f '{{.State.ExitCode}}' "$MIGRATION_ID")" = "0"
    test -n "$(docker volume ls -q --filter "$SMOKE_VOLUME_FILTER")"
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" ps --status running backend frontend
    curl -fsS http://127.0.0.1:8000/health | python -c 'import json, sys; assert json.load(sys.stdin)["status"] == "ok"'
    curl -fsS http://127.0.0.1:5173/ >/dev/null
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" down -v --remove-orphans
    test -z "$(docker volume ls -q --filter "$SMOKE_VOLUME_FILTER")"

    Expected: PASS. The isolated project name prefixes temporary dedicated Compose volumes, the migration exits successfully before dependents, backend health responds with status ok, frontend responds over HTTP, and the final down -v removes every volume carrying that project label.

- [ ] **Step 5: Commit the stabilization gate.**

    git add README.md README_zh.md backend/tests/agent/test_stabilization_gate.py .github/workflows/agent-mvp.yml

    git commit -m "docs: align runtime startup requirements"

### Milestone 2 — Enterprise Data Refresh and Unified Semantic Runtime

### Workstream 2A — Enterprise Data Refresh and Incremental Lineage

### Task 6: Define durable source refresh contracts and persistence

- [ ] **Deliverable:** A single typed refresh contract persists source policy, authoritative source cursor state, run leases with fencing, idempotency, deduplication, durable input lineage, retry state, dead-letter state, and best-effort cancellation for every supported refresh mode. The v1 contract is source/resource scoped and uses only watermark or opaque cursors; event-driven delivery is limited to managed webhook/outbox ingestion.

**Files:**

- Create: backend/app/schemas/refresh.py
- Create: backend/app/models/v2/refresh.py
- Modify: backend/app/models/v2/connection.py
- Modify: backend/app/models/v2/dataset.py
- Modify: backend/app/models/v2/pipeline.py
- Modify: backend/app/models/v2/__init__.py
- Modify: backend/app/models/__init__.py
- Modify: backend/app/routers/v2/connections.py
- Create: backend/alembic/versions/0022_refresh_contract.py
- Create: backend/app/services/v2/incremental/contract.py
- Create: backend/tests/v2/incremental/test_refresh_contract.py

Migration ownership: `0022_refresh_contract.py` is the single refresh-state migration; it owns `refresh_source_states`, `refresh_schedules`, refresh runs/inbox/outbox/DLQ, frozen config-version/contract fields, dispatch/backpressure state, cancellation request fields/state-transition history, configuration-revision indexes, and their unique keys, lease/fencing indexes, and DatasetVersion/PipelineRun lineage references. Later migrations depend on this head and must not recreate or shadow any authoritative refresh state table.

**Interfaces:**

- `RefreshPolicy` is an enum with exactly `batch`, `micro_batch`, and `event_driven`; a connection/resource may select one mode and one cursor contract: `watermark_primary_key` or `opaque_source_cursor`.
- `SourceCursor` is an immutable value with `source_id`, `resource`, `contract`, `watermark`, `primary_key`, `opaque_value`, and `observed_at`. A watermark cursor compares `(watermark, primary_key)` lexicographically; an opaque cursor compares only the source-provided opaque value.
- `RefreshSourceState` is the authoritative mutable singleton for exactly one `(source_id, resource)`. Its monotonic `config_version` is the source configuration revision. It stores the current cursor, `cursor_contract`, `config_version`, `lease_owner`, `lease_expires_at`, monotonically increasing `fencing_token`, `last_successful_run_id`, and update timestamps. The database uniqueness constraint on `(source_id, resource)` prevents competing authoritative rows.
- `ChangeEnvelope` is an immutable value with `event_id`, `source_id`, `resource`, `operation` (`upsert` or `delete`), normalized `primary_key`, `payload`, `watermark`, `source_cursor`, `schema_hash`, `occurred_at`, and `received_at`.
- `RefreshRun` stores policy, trigger, the frozen source `config_version` and `cursor_contract`, status (`queued|running|cancel_requested|cancelled|succeeded|failed|dead_lettered`), dispatch state (`pending|dispatched|backpressured|publish_failed`), dispatch reason, cursor before/after, input and output DatasetVersion IDs, PipelineRun ID, source provenance, quality summary, lag, duplicate/late counts, retry count, idempotency key (scoped to source/resource/config version), lease owner/expiry, the issued source fencing token, `cancel_requested_at`, `cancel_requested_by`, `cancel_reason`, `cancel_fencing_token`, and terminal timestamps. A committed outcome is represented by the run's plain terminal status. `cancel_requested` is an in-flight request awaiting a safe-point worker transition; `cancelled` is terminal and retry-safe, and a future manual run gets a new run ID. `RefreshInboxEvent`, `RefreshOutboxEvent`, and `RefreshDeadLetter` store durable event identity/hash, source/resource, processing state, delivery attempts, and replay status; inbox processing states are `received`, `duplicate`, `processed`, and `dead_lettered`.
- `PipelineRunInput` is an authoritative association with `pipeline_run_id`, `dataset_version_id`, source cursor/provenance, and input ordinal; it supports multi-source runs while the existing singular `PipelineRun.dataset_version_id` remains a backwards-compatible primary/output pointer and is never the complete lineage set.
- `RefreshSchedule` stores a source or pipeline target, cron expression, IANA timezone, business-calendar identifier and excluded dates, SLA seconds, retry policy, backfill window, enabled state, next due time, last dispatched run, and a uniqueness key for the target.
- `normalize_change_envelope(raw: Mapping[str, object], *, source_id: str, resource: str, received_at: datetime) -> ChangeEnvelope` rejects missing event identity, source/resource mismatch, invalid cursor shape, or schema hash drift.
- `cursor_order(left: SourceCursor, right: SourceCursor) -> int` returns `-1`, `0`, or `1`; `dedupe_key(envelope: ChangeEnvelope) -> str` returns a stable SHA-256 key over source, resource, event identity, cursor, primary key, operation, and canonical payload.
- `ConfigurationDriftError` is a typed `RefreshError` whose stable `reason_code` is exactly `CONFIGURATION_DRIFT`; lease/fencing/order errors remain distinct and must not be used to hide a source configuration revision or cursor-contract mismatch.
- `update_source_configuration(db: Session, *, source_id: str, resource: str, cursor_contract: str, configuration: Mapping[str, object], now: datetime) -> RefreshSourceState` locks the source state in one transaction, atomically increments `config_version`, stores the new non-secret source configuration/contract revision (credentials remain secret references), clears the active source lease, and increments its fencing token so every claim from the prior revision is invalid. The new revision is the only revision eligible for future claims.
- The managed source-configuration update path in `backend/app/routers/v2/connections.py` must call `update_source_configuration` in the same database transaction as the persisted connection/resource revision; it must never mutate connection JSON or cursor-contract fields without incrementing `config_version` and invalidating the existing lease/fence.
- `claim_refresh_run(db: Session, *, source_id: str, resource: str, policy: RefreshPolicy, idempotency_key: str, lease_owner: str, now: datetime, lease_seconds: int) -> RefreshRun` locks the authoritative `RefreshSourceState` with `SELECT ... FOR UPDATE` (or the dialect-equivalent serializable lock), treats `lease_expires_at > now` as active, atomically reuses an active idempotent run for the same owner only within the current source/resource/config-version scope or rejects another active lease, and for a new claim copies the current `config_version` and `cursor_contract` into the run while incrementing and returning the state fencing token. A claim may not be granted from a request-body cursor or from a non-authoritative state row.
- `record_refresh_outcome(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, config_version: int, cursor_contract: str, input_dataset_version_ids: Sequence[str], pipeline_run_id: str, next_cursor: SourceCursor, quality_summary: Mapping[str, object], provenance: Mapping[str, object], now: datetime) -> RefreshRun` starts one transaction and locks `RefreshSourceState`. FIRST, before any lease/owner/fencing check or lineage write, it compares the presented `config_version`/`cursor_contract` with the run-frozen values and authoritative source state. Any mismatch immediately raises `ConfigurationDriftError` with reason code `CONFIGURATION_DRIFT`, rolls back, and leaves DatasetVersion/PipelineRun/`PipelineRunInput` lineage and source cursor untouched. ONLY AFTER the revision/contract comparison matches does it validate `lease_expires_at > now`, exact lease owner, and the source fencing token; those failures use typed lease/fencing errors. It then calls `assert_refresh_not_cancelled` while holding the same lease/fence before writing any lineage association and re-checks it immediately before the cursor CAS. A `RefreshCancellationRequested` rolls back tentative materialization and prevents every durable result/advance. On a matching revision it writes the associations, performs the guarded cursor CAS, and sets status to `succeeded` in that same transaction; if the transaction commits, that plain terminal status is the durable fact — there is no separate commit marker. Any failed CAS rolls back every association and progress update and never advances the cursor.
- `request_refresh_cancellation(db: Session, *, run_id: str, requested_by: str, reason: str, now: datetime) -> RefreshRun` is the only cancellation entry point (full contract in the Milestone 2/3 Scope Amendment). It locks the run and authoritative source state, validates operator authorization, and validates the run's frozen config version against the authoritative state. For `queued` it transitions directly to terminal `cancelled`. For an actively leased `running` run it additionally validates the current lease/fencing token and atomically records `cancel_requested_at`, `cancel_requested_by`, `cancel_reason`, and `cancel_fencing_token`, changing status to `cancel_requested`. For `cancel_requested` it returns the same durable request unchanged. For any already-terminal status (`succeeded`, `failed`, `dead_lettered`, `cancelled`) it makes no mutation and returns the run with `already_terminal=True`; this is a plain result, not a typed error, and is never reported as a successful cancellation. It accepts no cursor, source URL, credential, payload, or worker-supplied authority.
- `finalize_refresh_cancellation(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, now: datetime) -> RefreshRun` checks the requested `cancel_fencing_token` and active owner/fence, and atomically transitions `cancel_requested` to terminal `cancelled`, clears the lease, and records the terminal time. It is idempotent for an already `cancelled` run and rejects a stale worker with `RefreshFencingError`; it never advances the source cursor, DatasetVersion/PipelineRun lineage, or snapshot state.
- `assert_refresh_not_cancelled(db: Session, *, run_id: str, lease_owner: str, fencing_token: int, now: datetime) -> None` locks the run and authoritative source state, verifies owner/fence/configuration, and raises `RefreshCancellationRequested` when status is `cancel_requested`. Connector page boundaries, event-inbox/batch boundaries, tentative materialization boundaries, and `record_refresh_outcome` call this guard before durable result/advance. `record_refresh_outcome(...)` also re-checks the cancellation request in its transaction immediately before lineage association and the cursor CAS; cancellation before commit rolls back the whole outcome. If the outcome transaction wins the race and commits first, the run is simply `succeeded`/`failed`/`dead_lettered`, and a cancellation request that arrives afterward gets the plain `already_terminal` result from `request_refresh_cancellation`.
- `mark_refresh_retryable(db: Session, *, run_id: str, reason: str, now: datetime) -> RefreshRun` performs an idempotent durable `running -> queued` interruption transition, increments `retry_count`, records the non-secret reason, clears the expired worker lease, sets `dispatch_state="pending"`, and leaves cursor-before/after and DatasetVersion/PipelineRun lineage unchanged. A terminal run is returned unchanged; this function never replays a connector inline.

- [ ] **Step 1: Write failing contract, uniqueness, lease, cursor, and configuration-drift tests.**

    `concurrent_refresh_db` must expose two independent database sessions/workers and synchronize their claim/finish calls with a barrier; the two fencing tests below are concurrency tests, not single-session mocks. Use the same transaction isolation and row-lock path as production for PostgreSQL and the supported MySQL integration fixture. The first test must race the two claim calls and assert exactly one success; the second must retain worker A's old token while worker B claims after expiry and commits a newer cursor before worker A finishes. `test_refresh_cancellation_dialects.py` must parameterize both PostgreSQL and MySQL and use two real sessions/workers for cancellation races, including cancel-before-pull, cancel during an in-flight connector page, cancel after tentative materialization but before the outcome transaction, and cancel after a terminal run of each status (`succeeded`, `failed`, `dead_lettered`, `cancelled`), asserting `already_terminal=True` and no progress mutation in every terminal case.

    The test fixture helper `mark_fixture_terminal(db: Session, run_id: str, *, status: Literal["succeeded", "failed", "dead_lettered"]) -> None` commits a terminal state without creating a DatasetVersion, PipelineRun, or lineage association (except for `succeeded`, which uses the normal outcome path); it is defined in the fixture module before the tests below use it.

    def test_watermark_cursor_orders_equal_timestamps_by_primary_key():
        older = SourceCursor(source_id="source-001", resource="orders", contract="watermark_primary_key", watermark="2026-08-26T01:00:00Z", primary_key="100", opaque_value=None, observed_at=FIXED_NOW)
        newer = SourceCursor(source_id="source-001", resource="orders", contract="watermark_primary_key", watermark="2026-08-26T01:00:00Z", primary_key="101", opaque_value=None, observed_at=FIXED_NOW)
        assert cursor_order(older, newer) == -1

    def test_duplicate_event_has_one_durable_inbox_identity(db):
        first = persist_fixture_event(db, event_id="evt-001")
        second = persist_fixture_event(db, event_id="evt-001")
        assert first.id == second.id
        assert count_fixture_inbox_events(db, "evt-001") == 1

    def test_second_worker_claim_is_rejected_while_first_lease_is_active(concurrent_refresh_db):
        first = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-001", lease_owner="worker-a")
        assert first.fencing_token == read_source_state(concurrent_refresh_db).fencing_token
        same = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-001", lease_owner="worker-a")
        assert same.id == first.id
        assert same.fencing_token == first.fencing_token
        with pytest.raises(RefreshLeaseError):
            claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-002", lease_owner="worker-b")

    def test_expired_worker_late_finish_cannot_overwrite_newer_cursor(concurrent_refresh_db):
        old = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-old", lease_owner="worker-a", now=FIXED_NOW, lease_seconds=60)
        new_now = FIXED_NOW + timedelta(minutes=10)
        new = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-new", lease_owner="worker-b", now=new_now)
        record_refresh_outcome(concurrent_refresh_db, run_id=new.id, lease_owner="worker-b", fencing_token=new.fencing_token, config_version=new.config_version, cursor_contract=new.cursor_contract, input_dataset_version_ids=["dataset-version-new"], pipeline_run_id="pipeline-run-new", next_cursor=cursor_fixture("2026-08-26T02:00:00Z", "200"), quality_summary={}, provenance={}, now=new_now)
        with pytest.raises(RefreshFencingError):
            record_refresh_outcome(concurrent_refresh_db, run_id=old.id, lease_owner="worker-a", fencing_token=old.fencing_token, config_version=old.config_version, cursor_contract=old.cursor_contract, input_dataset_version_ids=["dataset-version-old"], pipeline_run_id="pipeline-run-old", next_cursor=cursor_fixture("2026-08-26T01:00:00Z", "199"), quality_summary={}, provenance={}, now=new_now)
        assert read_fixture_cursor(concurrent_refresh_db).primary_key == "200"
        assert list_pipeline_inputs(concurrent_refresh_db, "pipeline-run-old") == []

    def test_configuration_upgrade_returns_configuration_drift_before_fence_check_without_lineage_or_progress(concurrent_refresh_db):
        configure_fixture_source(concurrent_refresh_db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key", config_version=7)
        old = claim_fixture_run(concurrent_refresh_db, source_id="source-001", resource="orders", idempotency_key="refresh-config-old", lease_owner="worker-a", policy=RefreshPolicy.MICRO_BATCH, lease_seconds=600)
        before_cursor = read_fixture_cursor(concurrent_refresh_db)
        upgraded = update_source_configuration(concurrent_refresh_db, source_id="source-001", resource="orders", cursor_contract="watermark_primary_key", configuration={"schema_hash": "schema-v2"}, now=FIXED_NOW + timedelta(minutes=1))
        assert upgraded.config_version == old.config_version + 1
        assert upgraded.lease_owner is None
        assert upgraded.fencing_token > old.fencing_token
        # The old run has an invalidated lease/fence, but the frozen revision/contract mismatch is checked first.
        with pytest.raises(ConfigurationDriftError) as exc:
            record_refresh_outcome(concurrent_refresh_db, run_id=old.id, lease_owner="worker-a", fencing_token=old.fencing_token, config_version=old.config_version, cursor_contract=old.cursor_contract, input_dataset_version_ids=["dataset-version-config-old"], pipeline_run_id="pipeline-run-config-old", next_cursor=cursor_fixture("2026-08-26T02:00:00Z", "200"), quality_summary={}, provenance={}, now=FIXED_NOW + timedelta(minutes=2))
        assert exc.value.reason_code == "CONFIGURATION_DRIFT"
        assert dataset_version_exists(concurrent_refresh_db, "dataset-version-config-old") is False
        assert pipeline_run_exists(concurrent_refresh_db, "pipeline-run-config-old") is False
        assert list_pipeline_inputs(concurrent_refresh_db, "pipeline-run-config-old") == []
        assert read_fixture_cursor(concurrent_refresh_db) == before_cursor

    def test_source_state_is_unique(concurrent_refresh_db):
        persist_fixture_source_state(concurrent_refresh_db, source_id="source-001", resource="orders")
        with pytest.raises(IntegrityError):
            persist_fixture_source_state(concurrent_refresh_db, source_id="source-001", resource="orders")

    def test_pipeline_run_records_all_input_versions(db):
        run = persist_fixture_pipeline_run(db, input_dataset_version_ids=["dataset-version-001", "dataset-version-002"])
        assert list_pipeline_inputs(db, run.id) == ["dataset-version-001", "dataset-version-002"]

    def test_failed_outcome_does_not_advance_source_cursor(db):
        run = fixture_run_with_cursor(db, watermark="2026-08-26T01:00:00Z", primary_key="100")
        mark_fixture_failed(db, run.id)
        assert read_fixture_cursor(db).primary_key == "100"

    def test_fenced_cancellation_request_records_actor_reason_and_safe_point_state(concurrent_refresh_db):
        run = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-cancel-001", lease_owner="worker-a")
        requested = request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="source maintenance", now=FIXED_NOW + timedelta(seconds=1))
        assert requested.status == "cancel_requested"
        assert requested.cancel_requested_by == "operator-001"
        assert requested.cancel_reason == "source maintenance"
        assert requested.cancel_fencing_token == run.fencing_token
        cancelled = finalize_refresh_cancellation(concurrent_refresh_db, run_id=run.id, lease_owner="worker-a", fencing_token=run.fencing_token, now=FIXED_NOW + timedelta(seconds=2))
        assert cancelled.status == "cancelled"
        assert read_fixture_cursor(concurrent_refresh_db) == run.cursor_before
        assert list_pipeline_inputs(concurrent_refresh_db, run.id) == []

    def test_cancellation_request_is_idempotent_and_stale_worker_cannot_finalize(concurrent_refresh_db):
        run = claim_fixture_run(concurrent_refresh_db, idempotency_key="refresh-cancel-002", lease_owner="worker-a")
        requested = request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="stop", now=FIXED_NOW)
        assert request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="stop", now=FIXED_NOW) == requested
        with pytest.raises(RefreshFencingError):
            finalize_refresh_cancellation(concurrent_refresh_db, run_id=run.id, lease_owner="worker-a", fencing_token=run.fencing_token - 1, now=FIXED_NOW)

    @pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "dead_lettered", "cancelled"])
    def test_cancellation_after_any_terminal_state_is_a_plain_already_terminal_result(concurrent_refresh_db, terminal_status):
        run = claim_fixture_run(concurrent_refresh_db, idempotency_key=f"refresh-cancel-{terminal_status}", lease_owner="worker-a")
        mark_fixture_terminal(concurrent_refresh_db, run.id, status=terminal_status)
        before_cursor = read_fixture_cursor(concurrent_refresh_db)
        before_inputs = list_pipeline_inputs(concurrent_refresh_db, run.id)
        result = request_refresh_cancellation(concurrent_refresh_db, run_id=run.id, requested_by="operator-001", reason="too late", now=FIXED_NOW + timedelta(seconds=1))
        assert result.already_terminal is True
        assert result.status == terminal_status
        unchanged = get_refresh_run(concurrent_refresh_db, run.id)
        assert unchanged.status == terminal_status
        if terminal_status != "succeeded":
            assert read_fixture_cursor(concurrent_refresh_db) == before_cursor
            assert list_pipeline_inputs(concurrent_refresh_db, run.id) == before_inputs

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_contract.py -q)

    Expected: FAIL because the refresh schemas, durable models, migration, and contract service do not exist, including authoritative configuration revision/fence invalidation, the fenced cancellation state machine, and the configuration-drift rollback test.

- [ ] **Step 3: Implement the minimum durable contract.**

    Add the typed Pydantic/domain values and SQLAlchemy tables, register all models, and create migration `0022_refresh_contract.py` with `down_revision` set to the current Alembic head resolved by Task 2 (currently `0021_mapping_entity_class_cn` in this checkout). This migration is the single Phase 2 refresh-state migration and must create `refresh_source_states` with unique `(source_id, resource)`, including cursor/contract/config-version fields, lease owner/expiry, and fencing tokens. `RefreshRun` must persist its frozen `config_version` and `cursor_contract`, the `queued|running|cancel_requested|cancelled|succeeded|failed|dead_lettered` status state machine, `cancel_requested_at`, `cancel_requested_by`, `cancel_reason`, `cancel_fencing_token`, and append-only cancellation/run transition records. Add uniqueness on `(source_id, resource, event_id)` and idempotency keys scoped to source/resource/config version, configuration-revision/lease/fencing/expiry/cancellation indexes, append-only event/run state transitions, and JSON columns for opaque cursors/provenance/quality summaries. Store cursor before/after and input DatasetVersion/PipelineRun references explicitly; never treat a request-body cursor or event ID as trusted identity without source validation.

- [ ] **Step 4: Run contract, migration, and model checks.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_contract.py -q)
    (cd "$REPO_ROOT/backend" && python scripts/run_migrations.py upgrade head)
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/models/test_v2_schema.py tests/v2/incremental/test_refresh_contract.py -q)

    Expected: PASS for watermark/opaque cursor contracts, duplicate-event idempotency, unique source state, second-worker claim rejection, expired-worker late-finish fencing, configuration upgrade invalidation, typed `CONFIGURATION_DRIFT` with no lineage/cursor progress, fenced/idempotent cancellation, the plain `already_terminal` result after any terminal state, immutable run state, and cursor non-advance after failure; the migration has one head and registers every refresh table.

- [ ] **Step 5: Commit the refresh contract.**

    git add backend/app/schemas/refresh.py backend/app/models/v2/refresh.py backend/app/models/v2/connection.py backend/app/models/v2/dataset.py backend/app/models/v2/pipeline.py backend/app/models/v2/__init__.py backend/app/models/__init__.py backend/app/routers/v2/connections.py backend/alembic/versions/0022_refresh_contract.py backend/app/services/v2/incremental/contract.py backend/tests/v2/incremental/test_refresh_contract.py
    git commit -m "feat: add durable enterprise refresh contract"

### Task 6A: Align the Python/Celery execution topology and refresh operability

- [ ] **Deliverable:** The existing Python backend remains the Phase 2 control and governed execution plane, but its refresh work is dispatched through named, queue-isolated Celery roles with durable run IDs, bounded worker settings, safe redelivery, backpressure visibility, and no connector work in FastAPI processes.

**Files:**

- Create: backend/app/tasks/topology.py
- Create: backend/app/tasks/v2/interactive_probe.py
- Create: backend/app/services/v2/incremental/operability.py
- Modify: backend/app/tasks/celery_app.py
- Modify: docker-compose.v2.yml
- Modify: docker-compose.agent.yml
- Create: backend/tests/agent/test_celery_topology.py
- Create: backend/tests/agent/test_interactive_probe.py
- Create: backend/tests/v2/incremental/test_refresh_task_dispatch.py
- Create: backend/tests/v2/incremental/test_refresh_operability.py
- Modify: .github/workflows/agent-mvp.yml

**Interfaces:**

- `QUEUE_REFRESH_SCHEDULE`, `QUEUE_REFRESH_POLL`, `QUEUE_REFRESH_EVENT`, `QUEUE_REFRESH_REPLAY`, `QUEUE_ARTIFACT_EXTRACTION`, `QUEUE_AGENT_INTERACTIVE`, and `QUEUE_HOUSEKEEPING` are constants with values `refresh.schedule`, `refresh.poll`, `refresh.event`, `refresh.replay`, `artifact.extraction`, `agent.interactive`, and `housekeeping`. `ALL_QUEUE_NAMES` is the stable tuple containing exactly those seven values.
- `REFRESH_TASK_NAMES` is the stable tuple `("refresh.dispatch_due_schedules", "refresh.connection", "refresh.pipeline", "refresh.poll", "refresh.event", "refresh.replay")`. `TASK_ROUTES` maps `refresh.dispatch_due_schedules` to `refresh.schedule`, `refresh.connection`/`refresh.pipeline`/`refresh.poll` to `refresh.poll`, `refresh.event` to `refresh.event`, and `refresh.replay` to `refresh.replay`. It also maps `app.tasks.extraction.run_extraction`, `app.tasks.audit.run_audit`, `app.tasks.v2.mapping_apply.mapping_apply_task`, and `app.tasks.v2.pipeline_run.pipeline_run_task` to `artifact.extraction`; `agent.turn_execute`, `agent.dispatch_claim`, `agent.dispatch_heartbeat`, and the fixture-only `agent.interactive_probe` to `agent.interactive`; and `agent.dispatch_publish`, `agent.dispatch_watchdog`, `agent.dispatch_sweeper`, `agent.index_consume`, `agent.memory_summary_sweep`, `agent.memory_extraction_sweep`, `agent.memory_vector_sweep`, and `agent.retention_purge` to `housekeeping`.
- `agent.interactive_probe(probe_id: str) -> Mapping[str, str]` is a harmless, deterministic Celery task in `backend/app/tasks/v2/interactive_probe.py` used only by this task's own fake-broker queue-isolation test. It accepts one opaque synthetic probe ID, returns `{ "probe_id": probe_id, "status": "ok" }`, touches no Agent state, connector, database, or production payload, and is disabled/denylisted for production dispatch. The implementation must not expand Agent behavior; it exists solely to prove that a saturated refresh queue does not block the `agent.interactive` queue.
- `RefreshWorkerLimits` is an immutable value with `concurrency: int`, `prefetch_multiplier: int`, `soft_time_limit_seconds: int`, `hard_time_limit_seconds: int`, and `shutdown_grace_seconds: int`. `load_refresh_worker_limits(environ: Mapping[str, str]) -> RefreshWorkerLimits` loads bounded positive values from environment variables, rejects non-positive values and `soft_time_limit_seconds >= hard_time_limit_seconds`, and uses the local reference defaults `2`, `1`, `300`, `360`, and `40` respectively. These are operational defaults, not throughput claims.
- `configure_celery_topology(celery_app: Celery) -> None` installs `task_queues` for `ALL_QUEUE_NAMES`, `task_routes` for every named task, `task_default_queue=housekeeping`, `worker_prefetch_multiplier=1`, task events, and refresh-only task annotations with `acks_late=True`, `reject_on_worker_lost=True`, the bounded soft/hard time limits, and retry-safe delivery. Every refresh task must be registered under the exact names in `REFRESH_TASK_NAMES`.
- `RefreshDispatchMessage` is an immutable value with exactly `run_id: str`, `task_name: str`, and `queue: str`; `RefreshDispatchMessage.to_dict() -> dict[str, str]` returns only those fields. `build_refresh_dispatch_message(*, run_id: str, task_name: str) -> RefreshDispatchMessage` rejects unknown task names, empty IDs, and task/queue mismatches. `enqueue_refresh_run(*, message: RefreshDispatchMessage, send_task: Callable[[str, Sequence[str], str], str]) -> str` calls `send_task(message.task_name, [message.run_id], message.queue)` and returns the broker task ID; it never accepts or serializes a source URL, cursor, credential, SQL, selector, event payload, or arbitrary task arguments.
- `get_refresh_task_options(task_name: str) -> Mapping[str, object]` returns the registered refresh task's `acks_late`, `reject_on_worker_lost`, `soft_time_limit`, and `time_limit` values and rejects non-refresh names.
- `QueueObservation` contains queue name, depth, oldest queued age seconds, and worker-ready boolean. `RefreshQueueMetric` contains queue name, depth, oldest queued age seconds, running count, retry count, dead-letter count, and maximum run lag seconds. `RefreshReadiness` contains database-ready, broker-ready, and refresh-worker-ready booleans plus a derived `ready` boolean. `RefreshOperability` contains a tuple of `RefreshQueueMetric`, `RefreshReadiness`, and observed-at UTC time; it contains no message payload or credential.
- `collect_refresh_operability(db: Session, *, now: datetime, observations: Mapping[str, QueueObservation]) -> RefreshOperability` validates that observations cover every named refresh queue, derives retry/DLQ/run-lag counts only from durable refresh state, and returns queue/backpressure/readiness data. `QueueObservation` is supplied by a broker inspector after it has discarded message bodies; the function never calls a connector or broker with source credentials.
- `mark_refresh_retryable` from Task 6 is the shared interruption boundary for `SoftTimeLimitExceeded` and graceful shutdown; refresh workers call it before acknowledging and hard worker loss relies on late acknowledgement/redelivery plus durable lease expiry. An explicit operator cancellation uses `assert_refresh_not_cancelled` and `finalize_refresh_cancellation` instead, so it reaches terminal `cancelled` only before a durable outcome. The prior cursor/checkpoint remains the only valid retry origin for both paths.
- The v2 Compose reference topology has explicit queue-bound profiles: `refresh_schedule_worker` (`refresh.schedule`), `refresh_poll_worker` (`refresh.poll`), `refresh_event_worker` (`refresh.event`), `refresh_replay_worker` (`refresh.replay`), `artifact_worker` (`artifact.extraction`), `agent_worker` (`agent.interactive`), and `housekeeping_worker` (`housekeeping`), plus one `beat` service. The Agent Compose keeps its existing dispatcher/watchdog/sweeper roles but gives them explicit queue flags and adds the missing refresh and interactive roles. All worker and beat commands use `python -m celery -A app.tasks.celery_app`; role names alone never imply queue isolation.
- Each refresh worker command passes `--concurrency` from the bounded reference setting, `--prefetch-multiplier=1`, and a finite `stop_grace_period`; the service profile documents `REFRESH_WORKER_CONCURRENCY`, `REFRESH_WORKER_PREFETCH_MULTIPLIER`, `REFRESH_SOFT_TIME_LIMIT_SECONDS`, `REFRESH_HARD_TIME_LIMIT_SECONDS`, and `REFRESH_WORKER_SHUTDOWN_GRACE_SECONDS`. The plan does not add Kubernetes manifests or claim autoscaling.

- [ ] **Step 1: Write failing queue, message, worker-setting, Compose, and operability tests.**

    def test_refresh_routes_and_queues_are_explicit():
        assert set(ALL_QUEUE_NAMES) == {
            "refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay",
            "artifact.extraction", "agent.interactive", "housekeeping",
        }
        assert TASK_ROUTES["refresh.poll"]["queue"] == "refresh.poll"
        assert TASK_ROUTES["refresh.event"]["queue"] == "refresh.event"
        assert TASK_ROUTES["refresh.replay"]["queue"] == "refresh.replay"
        assert TASK_ROUTES["agent.interactive_probe"]["queue"] == "agent.interactive"

    def test_interactive_probe_is_harmless_and_fixture_only():
        result = interactive_probe("probe-001")
        assert result == {"probe_id": "probe-001", "status": "ok"}

    def test_refresh_dispatch_message_is_only_a_durable_run_id():
        message = build_refresh_dispatch_message(run_id="run-001", task_name="refresh.poll")
        assert message == RefreshDispatchMessage(run_id="run-001", task_name="refresh.poll", queue="refresh.poll")
        assert set(message.to_dict()) == {"run_id", "task_name", "queue"}

    def test_refresh_worker_limits_are_bounded_and_late_acknowledged():
        limits = load_refresh_worker_limits({})
        assert limits.prefetch_multiplier == 1
        assert limits.soft_time_limit_seconds < limits.hard_time_limit_seconds
        assert get_refresh_task_options("refresh.poll")["acks_late"] is True
        assert get_refresh_task_options("refresh.poll")["reject_on_worker_lost"] is True

    @pytest.mark.parametrize("compose_path", ["docker-compose.v2.yml", "docker-compose.agent.yml"])
    def test_compose_uses_app_celery_and_queue_bound_roles(compose_path):
        text = Path(compose_path).read_text()
        assert "-A app.tasks.celery_app" in text
        assert "-A app.tasks.extraction" not in text
        for queue in ALL_QUEUE_NAMES:
            assert queue in text
        assert "--prefetch-multiplier=1" in text

    def test_refresh_dispatch_rejects_non_durable_arguments():
        with pytest.raises(TypeError):
            build_refresh_dispatch_message(run_id="run-001", task_name="refresh.poll", source_id="source-001")

    def test_operability_has_queue_age_lag_retry_dlq_and_readiness_without_payloads(db):
        result = collect_refresh_operability(db, now=FIXED_NOW, observations=fixture_queue_observations())
        assert result.readiness.ready is True
        assert {item.queue for item in result.queues} == {"refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay"}
        assert all(item.oldest_queued_age_seconds is None or item.oldest_queued_age_seconds >= 0 for item in result.queues)
        assert all("payload" not in item.__dict__ and "credential" not in item.__dict__ for item in result.queues)

    def test_saturated_refresh_queue_does_not_block_agent_interactive_queue(fake_broker):
        fake_broker.fill("refresh.poll")
        assert fake_broker.depth("refresh.poll") > 0
        accepted = fake_broker.send("agent.interactive_probe", ["probe-001"], "agent.interactive")
        assert accepted.queue == "agent.interactive"

- [ ] **Step 2: Run the focused topology tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/agent/test_celery_topology.py tests/v2/incremental/test_refresh_task_dispatch.py tests/v2/incremental/test_refresh_operability.py -q)
    (cd "$REPO_ROOT" && docker compose -f docker-compose.v2.yml config --quiet)
    (cd "$REPO_ROOT" && docker compose -f docker-compose.agent.yml config --quiet)

    Expected: FAIL because Celery has no named queue/route contract, current v2 Compose has one unisolated worker using `app.tasks.extraction`, Agent Compose role names have no refresh queue routes, no bounded refresh delivery settings are asserted, and no queue/lag/readiness collector exists.

- [ ] **Step 3: Implement the Python execution topology and safe dispatch boundary.**

    Add `topology.py` with the exact constants, route map, bounded settings, message builder, and Celery configuration above. Keep `app.tasks.extraction` in the Celery include list only as an artifact task module; the application entry point remains `app.tasks.celery_app`. Use queue-specific task routes instead of relying on worker names, set refresh-only late acknowledgement and worker-loss redelivery, and register events for operator metrics. Add `backend/app/tasks/v2/interactive_probe.py` with the exact harmless `agent.interactive_probe` task; register it only for the `agent.interactive` queue and mark it fixture/diagnostics-only so it does not broaden production Agent behavior. Add `operability.py` to derive queue depth/oldest age from sanitized broker observations and run lag/retry/DLQ/readiness from durable database state. Queue saturation marks a run `dispatch_state="backpressured"` and leaves it `queued`; a failed publish marks `publish_failed` and remains retryable. The dispatcher never places secrets or source arguments in a task message.

    Update both Compose files with the explicit profile/role table above, `-A app.tasks.celery_app`, `-Q` queue allowlists, bounded concurrency/prefetch/time settings, and graceful stop time. Keep the existing migration dependency and guarded build-manifest checks. The v2 default development profile may omit the extra workers, but the `refresh`, `artifact`, `agent`, and `housekeeping` profiles must be fully declared and statically validated; no autoscaler or alternative-language worker is introduced.

- [ ] **Step 4: Run topology, Compose, and operability checks.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/agent/test_celery_topology.py tests/v2/incremental/test_refresh_task_dispatch.py tests/v2/incremental/test_refresh_operability.py -q)
    (cd "$REPO_ROOT/backend" && python -c 'from app.tasks.celery_app import celery_app; queues = {q.name for q in celery_app.conf.task_queues}; assert {"refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay", "artifact.extraction", "agent.interactive", "housekeeping"} <= queues; assert celery_app.conf.task_routes["refresh.poll"]["queue"] == "refresh.poll"')
    (cd "$REPO_ROOT" && docker compose -f docker-compose.v2.yml config --quiet)
    (cd "$REPO_ROOT" && docker compose -f docker-compose.agent.yml config --quiet)

    Expected: PASS for exact queue/route registration, run-ID-only dispatch, late-ack/redelivery/time-bound settings, sanitized queue/lag/retry/DLQ/readiness output, queue saturation isolation, and both Compose files' explicit queue-bound Python roles. The test must not require a live broker or an external CDC producer.

- [ ] **Step 5: Commit the execution topology.**

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    git -C "$REPO_ROOT" add backend/app/tasks/topology.py backend/app/tasks/v2/interactive_probe.py backend/app/services/v2/incremental/operability.py backend/app/tasks/celery_app.py docker-compose.v2.yml docker-compose.agent.yml backend/tests/agent/test_celery_topology.py backend/tests/agent/test_interactive_probe.py backend/tests/v2/incremental/test_refresh_task_dispatch.py backend/tests/v2/incremental/test_refresh_operability.py .github/workflows/agent-mvp.yml
    git -C "$REPO_ROOT" commit -m "feat: isolate Python refresh worker queues"

### Task 7: Persist T+1 schedules and dispatch them through Celery beat

- [ ] **Deliverable:** T+1 and other cron schedules are persisted with enterprise timing controls and Celery beat actually dispatches due refresh runs through `refresh.schedule`/`refresh.poll`; validation alone never reports a schedule as active, and a saturated source remains durably queued with a backpressure reason.

**Files:**

- Create: backend/app/services/v2/scheduler/schedule_service.py
- Create: backend/app/tasks/v2/refresh_tasks.py
- Modify: backend/app/services/v2/scheduler/cron_service.py
- Modify: backend/app/tasks/v2/connection_sync.py
- Modify: backend/app/tasks/celery_app.py
- Modify: backend/app/routers/v2/connections.py
- Create: backend/tests/v2/incremental/test_refresh_schedule.py
- Create: backend/tests/v2/incremental/test_refresh_schedule_integration.py
- Modify: backend/tests/v2/connection/test_scheduler.py

**Interfaces:**

- `ScheduleRequest` contains `target_type` (`connection` or `pipeline`), `target_id`, `cron_expr`, IANA `timezone`, `business_calendar`, `sla_seconds`, `retry_policy`, `backfill_window_seconds`, `max_pending_runs`, and `enabled`.
- `upsert_refresh_schedule(db: Session, request: ScheduleRequest, *, now: datetime) -> RefreshSchedule` validates the five-field cron, timezone, non-negative SLA/backfill, positive `max_pending_runs`, and bounded retry policy, computes the next due instant in UTC, and persists the schedule.
- `dispatch_due_schedules(db: Session, *, now: datetime, lease_owner: str, send_refresh: Callable[[RefreshDispatchMessage], str]) -> list[str]` claims each due schedule once, skips excluded business-calendar dates, creates an idempotent `RefreshRun`, checks the persisted `max_pending_runs` admission limit, advances `next_due_at`, stores `last_dispatched_run_id`, and calls `send_refresh(message)` only after the claim is committed. The message contains the durable `run_id`, the exact refresh task name, and its queue; target/source configuration is reloaded by the worker from the database. When admission fails, it keeps the run `queued` with `dispatch_state="backpressured"`, records `BACKPRESSURE`, and does not pull a connector.
- Celery task `refresh.dispatch_due_schedules()` calls `dispatch_due_schedules` with a worker lease owner on `refresh.schedule`; `refresh.connection(run_id: str)` and `refresh.pipeline(run_id: str)` call the durable refresh service with the single `run_id` on `refresh.poll`. `celery_app.beat_schedule` contains `refresh-schedule-dispatch` at a short fixed interval; dynamic schedules remain in the database rather than being generated as untracked beat entries.
- `sync_connection(connection_id: str, mode: str = "full") -> dict` and `sync_all_connections() -> list[dict]` remain compatibility wrappers over `refresh_tasks`, never `pass`. The connection sync route imports `app.tasks.v2.refresh_tasks.refresh_connection_task` and never references the nonexistent `sync_tasks` module.
- `CronService.schedule_connection_sync` and `schedule_pipeline_run` delegate to the persistence service when given a database session; their return value is `status="scheduled"` only after a row is persisted and the next due time is computed.

- [ ] **Step 1: Write failing schedule and beat-dispatch tests.**

    def test_t_plus_one_schedule_persists_timezone_calendar_sla_and_retry(db):
        schedule = upsert_fixture_schedule(db, cron_expr="0 2 * * *", timezone="Asia/Shanghai", business_calendar=["2026-10-01"], sla_seconds=86400, backfill_window_seconds=172800, max_pending_runs=2)
        assert schedule.timezone == "Asia/Shanghai"
        assert schedule.business_calendar == ["2026-10-01"]
        assert schedule.sla_seconds == 86400
        assert schedule.max_pending_runs == 2
        assert schedule.next_due_at.tzinfo is not None

    def test_due_schedule_is_dispatched_once_after_persistent_claim(db):
        sent = []
        ids = dispatch_due_schedules(db, now=FIXED_NOW, lease_owner="beat-a", send_refresh=lambda message: sent.append(message.to_dict()) or message.run_id)
        assert ids == ["run-001"]
        assert sent == [{"run_id": "run-001", "task_name": "refresh.connection", "queue": "refresh.poll"}]
        assert dispatch_due_schedules(db, now=FIXED_NOW, lease_owner="beat-b", send_refresh=lambda _message: "run-002") == []

    def test_beat_has_real_refresh_dispatch_entry():
        assert celery_app.conf.beat_schedule["refresh-schedule-dispatch"]["task"] == "refresh.dispatch_due_schedules"

    def test_saturated_source_is_queued_with_backpressure_and_never_pulled(db):
        create_fixture_schedule(db, target_id="source-001", max_pending_runs=1)
        create_queued_fixture_run(db, source_id="source-001")
        sent = []
        ids = dispatch_due_schedules(db, now=FIXED_NOW, lease_owner="beat-a", send_refresh=lambda message: sent.append(message.to_dict()) or "broker-id")
        assert ids == []
        assert sent == []
        assert latest_fixture_run(db, source_id="source-001").dispatch_state == "backpressured"
        assert latest_fixture_run(db, source_id="source-001").status == "queued"

    def test_schedule_integration_uses_isolated_database_and_no_shared_schedule_rows(isolated_schedule_db):
        create_fixture_schedule(isolated_schedule_db, target_id="source-isolated")
        dispatch_due_schedules(isolated_schedule_db, now=FIXED_NOW, lease_owner="isolated-beat", send_refresh=record_sent_run)
        assert list_schedule_targets(isolated_schedule_db) == ["source-isolated"]

- [ ] **Step 2: Run the focused schedule tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_schedule.py tests/v2/incremental/test_refresh_schedule_integration.py tests/v2/connection/test_scheduler.py -q)

    Expected: FAIL because the current CronService only parses and logs, no schedule row is stored, Celery beat has no refresh entry, the connection route imports `app.tasks.v2.sync_tasks`, and no source admission/backpressure state or durable run-ID dispatch exists.

- [ ] **Step 3: Implement persistent scheduling and task dispatch.**

    Add schedule persistence and timezone/business-calendar calculation with `zoneinfo`, use a database lease/idempotency key for due dispatch, enforce `max_pending_runs` before publishing, and add retry/backfill/SLA fields without creating an unbounded scheduler. Register the refresh task module in Celery, add the fixed beat dispatcher, replace the connection route's broken import, and make the compatibility wrappers call the same run-ID-only refresh task. Keep schedule dispatch independent of Redis availability by retaining a queued `publish_failed` or `backpressured` run for safe retry; no connector pull occurs from beat or the API request.

- [ ] **Step 4: Run unit, isolated integration, and Compose task-registration checks.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_schedule.py tests/v2/incremental/test_refresh_schedule_integration.py tests/v2/connection/test_scheduler.py -q)
    (cd "$REPO_ROOT/backend" && python -c 'from app.tasks.celery_app import celery_app; assert "refresh.dispatch_due_schedules" in {v["task"] for v in celery_app.conf.beat_schedule.values()}')

    Expected: PASS for T+1 timezone and calendar calculation, retry/backfill/SLA/max-pending persistence, one-shot due dispatch, isolated schedule state, real task registration on `refresh.schedule`/`refresh.poll`, backpressure retention, and fixed compatibility imports.

- [ ] **Step 5: Commit persistent scheduling.**

    git add backend/app/services/v2/scheduler/schedule_service.py backend/app/tasks/v2/refresh_tasks.py backend/app/services/v2/scheduler/cron_service.py backend/app/tasks/v2/connection_sync.py backend/app/tasks/celery_app.py backend/app/routers/v2/connections.py backend/tests/v2/incremental/test_refresh_schedule.py backend/tests/v2/incremental/test_refresh_schedule_integration.py backend/tests/v2/connection/test_scheduler.py
    git commit -m "feat: dispatch persisted refresh schedules"

### Task 8: Implement semi-real-time polling with durable cursors and versioned pipeline inputs

- [ ] **Deliverable:** SQL, REST, and Mongo polling use a shared cursor/overlap/dedupe contract, advance only after durable DatasetVersion and PipelineRun outcomes, and expose retry, DLQ, late-data, and lag state; structured Pipeline runs never read hard-coded version 1.

**Files:**

- Create: backend/app/services/v2/incremental/polling.py
- Modify: backend/app/services/connection/base.py
- Modify: backend/app/services/connection/sql_connector.py
- Modify: backend/app/services/connection/rest_connector.py
- Modify: backend/app/services/connection/mongo_connector.py
- Modify: backend/app/services/v2/dataset_service.py
- Modify: backend/app/tasks/v2/pipeline_run.py
- Modify: backend/app/services/v2/incremental/orchestrator.py
- Modify: backend/app/tasks/v2/refresh_tasks.py
- Create: backend/tests/v2/incremental/test_refresh_polling.py
- Create: backend/tests/v2/incremental/test_refresh_cancellation.py
- Create: backend/tests/v2/incremental/integration/__init__.py
- Create: backend/tests/v2/incremental/integration/test_refresh_polling_dialects.py
- Create: backend/tests/v2/incremental/integration/test_refresh_cancellation_dialects.py
- Modify: backend/tests/v2/connection/test_sql_connector.py
- Modify: backend/tests/v2/connection/test_rest.py
- Modify: backend/tests/v2/connection/test_mongo.py

**Interfaces:**

- `DeltaPage` contains `envelopes: Sequence[ChangeEnvelope]`, `candidate_cursor: SourceCursor`, `source_observed_at`, and `source_lag_seconds`.
- `ConnectorBase.pull_delta(resource: str, *, cursor: SourceCursor | None, overlap_window: timedelta) -> DeltaPage` is the only incremental connector interface. SQL requires server-owned `watermark_column` and `primary_key_column`; REST passes the configured delta/cursor parameter; Mongo uses its configured opaque/ObjectId cursor. A connector without a valid cursor contract falls back to an explicit full batch and records `cursor_outcome="unchanged"`, never guessing a watermark.
- `poll_source(db: Session, *, source_id: str, resource: str, lease_owner: str, now: datetime) -> RefreshRunResult` claims a refresh run, reads the persisted cursor, frozen config version/contract, and source fencing token, pulls the overlap window, normalizes and deduplicates envelopes, persists a new DatasetVersion and PipelineRun, and advances the cursor only after those durable records and their success outcome commit using the returned fencing token/config revision/contract. A source configuration change during the poll returns typed `CONFIGURATION_DRIFT` and leaves the prior run/cursor unchanged.
- `RefreshRunResult` contains run ID, status, frozen config version/contract, input DatasetVersion IDs, PipelineRun ID, cursor before/after, duplicate count, late-event count, retry count, DLQ count, source lag seconds, and provenance.
- Celery task `refresh.poll(run_id: str)` is the only worker entry for polling; it reloads source/resource/configuration from the durable run and never accepts connector arguments. It calls `assert_refresh_not_cancelled(...)` at every connector page boundary and before tentative DatasetVersion/PipelineRun materialization. On `RefreshCancellationRequested`, it rolls back any uncommitted materialization and calls `finalize_refresh_cancellation(...)` under the same lease/fence before acknowledging; no cursor, DatasetVersion/PipelineRun lineage, or snapshot is advanced. It catches `SoftTimeLimitExceeded` and graceful shutdown separately, calls `mark_refresh_retryable(..., reason="WORKER_INTERRUPTED")`, and acknowledges only after that transition commits. Hard worker loss is handled by late acknowledgement/redelivery and lease expiry; no cursor is advanced by interruption. If the durable outcome transaction commits before a cancellation lock is acquired, the run is simply `succeeded`, and a later cancellation API request returns the plain `already_terminal` result from `request_refresh_cancellation` (Milestone 2/3 Scope Amendment).
- `dedupe_key` is applied before materialization; equal timestamps sort by primary key, late records are included within the overlap window, and out-of-order records do not move the cursor backward. Failed attempts keep the previous cursor, retry with bounded backoff, and move to `RefreshDeadLetter` after the configured maximum; replay creates a new idempotent run rather than mutating the old run.
- `DatasetService.create_version(..., refresh_run_id: str | None, source_cursor: SourceCursor | None, observed_at: datetime | None) -> DatasetVersion` records refresh provenance. `pipeline_run_task(pipeline_id: str, run_id: str, input_dataset_version_ids: Sequence[str] | None = None)` uses the explicitly pinned input versions, otherwise `Dataset.latest_version_id`/latest approved version, and never calls `preview(dataset_id, 1, ...)` for production refresh.
- `IncrementalOrchestrator.on_connection_sync` consumes the `RefreshRunResult` and passes its input version/run IDs to the pipeline and later snapshot materialization; it does not create a PipelineRun with only `pipeline_id` and `status`.

- [ ] **Step 1: Write failing polling, cursor, dedupe, retry, DLQ, and version-selection tests.**

    @pytest.mark.parametrize("case_id", ["normal", "empty", "late", "equal-watermark", "duplicate", "out-of-order"])
    def test_polling_cases_are_deterministic(case_id, polling_fixture):
        result = run_polling_fixture(case_id, polling_fixture)
        assert result.status == "succeeded"
        assert result.duplicate_count >= 0
        assert cursor_order(result.cursor_before, result.cursor_after) >= 0

    def test_failed_retry_keeps_cursor_until_pipeline_success(polling_fixture):
        result = run_polling_fixture("failed-retry", polling_fixture)
        assert result.cursor_after == result.cursor_before
        assert result.retry_count == 1

    def test_mid_refresh_termination_requeues_without_cursor_or_checkpoint_progress(polling_fixture):
        running = start_refresh_task_fixture(polling_fixture, "normal")
        before = read_fixture_cursor(polling_fixture.db)
        interrupted = interrupt_refresh_task(running.run_id, reason="WORKER_INTERRUPTED")
        assert interrupted.status == "queued"
        assert interrupted.dispatch_state == "pending"
        assert interrupted.cursor_before == interrupted.cursor_after
        assert read_fixture_cursor(polling_fixture.db) == before

    @pytest.mark.parametrize("case_id", ["cancel-before-pull", "cancel-inflight-page", "cancel-after-tentative-materialization"])
    def test_poll_cancellation_stops_at_safe_point_without_durable_progress(case_id, polling_fixture):
        result = run_polling_fixture(case_id, polling_fixture)
        assert result.status == "cancelled"
        assert result.cancel_requested_at is not None
        assert result.cursor_before == result.cursor_after
        assert result.input_dataset_version_ids == []
        assert result.pipeline_run_id is None
        assert read_fixture_cursor(polling_fixture.db) == result.cursor_before

    def test_poll_cancellation_after_success_is_already_terminal_and_preserves_lineage(polling_fixture):
        result = run_polling_fixture("normal", polling_fixture)
        assert result.status == "succeeded"
        cancelled = request_refresh_cancellation(polling_fixture.db, run_id=result.run_id, requested_by="operator-001", reason="too late", now=FIXED_NOW)
        assert cancelled.already_terminal is True
        assert cancelled.status == "succeeded"
        assert result.input_dataset_version_ids
        assert result.pipeline_run_id

    @pytest.mark.parametrize("case_id", ["cancel-after-failed-no-outcome", "cancel-after-dead-lettered-no-outcome"])
    def test_poll_terminal_failure_is_already_terminal_without_progress(case_id, polling_fixture):
        result = run_polling_fixture(case_id, polling_fixture)
        assert result.status in {"failed", "dead_lettered"}
        cancelled = request_refresh_cancellation(polling_fixture.db, run_id=result.run_id, requested_by="operator-001", reason="not applicable", now=FIXED_NOW)
        assert cancelled.already_terminal is True
        assert result.cursor_before == result.cursor_after
        assert result.input_dataset_version_ids == []
        assert result.pipeline_run_id is None

    def test_dlq_replay_does_not_mutate_failed_run(polling_fixture):
        failed = run_polling_fixture("dlq-replay", polling_fixture)
        replay = replay_refresh_fixture(failed.run_id)
        assert replay.run_id != failed.run_id
        assert get_refresh_run(failed.run_id).status == "dead_lettered"

    def test_structured_pipeline_uses_pinned_latest_input_not_version_one(pipeline_db):
        run = run_pipeline_fixture(pipeline_db, input_dataset_version_ids=["dataset-version-002"])
        assert run.input_dataset_version_ids == ["dataset-version-002"]
        assert loaded_pipeline_version(pipeline_db) == 2

- [ ] **Step 2: Run polling and connector tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_polling.py tests/v2/connection/test_sql_connector.py tests/v2/connection/test_rest.py tests/v2/connection/test_mongo.py -q)

    Expected: FAIL because connectors accept only a string `since`, no cursor is durable, SQL uses `watermark > since`, failures can fall back to full reads, structured pipeline code still requests version 1, and no run-ID-only task interruption/retry path exists.

- [ ] **Step 3: Implement shared polling and pinned input behavior.**

    Add cursor-aware connector responses and server-owned identifier validation, implement overlap reads with `(watermark, primary_key)` predicates, and apply canonical event dedupe before writing a DatasetVersion. Persist run statistics and source lag, use a lease around each poll, keep the cursor unchanged until the DatasetVersion and PipelineRun succeed, and route exhausted retries to the durable DLQ. Register `refresh.poll(run_id: str)` on `refresh.poll`; it reloads all source state from the run ID, checks `assert_refresh_not_cancelled` at connector page and tentative-materialization boundaries, and on cancellation calls `finalize_refresh_cancellation` before acknowledging. Soft timeout and graceful shutdown use `mark_refresh_retryable` instead, while the outcome transaction re-checks cancellation immediately before lineage and cursor advancement. If that transaction commits first, the run is simply `succeeded`, and a cancellation request that arrives afterward gets the plain `already_terminal` result; a failed/dead-lettered run behaves the same way and never claims rollback. Extend DatasetService/PipelineRun metadata and update `_load_source_rows` to accept explicit version IDs or the dataset's latest approved version. Preserve existing full/snapshot connector behavior and compatibility tests.

- [ ] **Step 4: Run unit and both-dialect integration tests.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_polling.py tests/v2/incremental/test_refresh_cancellation.py tests/v2/connection/test_sql_connector.py tests/v2/connection/test_rest.py tests/v2/connection/test_mongo.py -q)
    (cd "$REPO_ROOT" && docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait postgres mysql)
    (cd "$REPO_ROOT/backend" && RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime python -m pytest tests/v2/incremental/integration/test_refresh_polling_dialects.py tests/v2/incremental/integration/test_refresh_cancellation_dialects.py -q)

    Expected: PASS for normal/empty/late/equal-watermark/duplicate/out-of-order events, cursor non-advance on failure or worker interruption, cancel-before-pull/cancel-inflight-page/cancel-after-tentative-materialization safe-point cancellation, the plain `already_terminal` result for a cancellation request arriving after any terminal state, retry/DLQ/replay, observed lag, pinned latest input, run-ID-only task payloads, and the same durable refresh semantics against PostgreSQL and MySQL sources.

- [ ] **Step 5: Commit semi-real-time polling.**

    git add backend/app/services/v2/incremental/polling.py backend/app/services/connection/base.py backend/app/services/connection/sql_connector.py backend/app/services/connection/rest_connector.py backend/app/services/connection/mongo_connector.py backend/app/services/v2/dataset_service.py backend/app/tasks/v2/pipeline_run.py backend/app/services/v2/incremental/orchestrator.py backend/app/tasks/v2/refresh_tasks.py backend/tests/v2/incremental/test_refresh_polling.py backend/tests/v2/incremental/test_refresh_cancellation.py backend/tests/v2/incremental/integration/__init__.py backend/tests/v2/incremental/integration/test_refresh_polling_dialects.py backend/tests/v2/incremental/integration/test_refresh_cancellation_dialects.py backend/tests/v2/connection/test_sql_connector.py backend/tests/v2/connection/test_rest.py backend/tests/v2/connection/test_mongo.py
    git commit -m "feat: add durable semi-real-time refresh polling"

### Task 9: Add bounded event-driven refresh adapters (webhook + outbox) with inbox dedupe

- [ ] **Deliverable:** Managed webhook and outbox adapters normalize into the same ChangeEnvelope and durable inbox/DLQ path, with signature/replay/schema checks and at-least-once event-ID dedupe. (Scope per the Milestone 2/3 Scope Amendment: no CDC adapter, no partition/sequence ordering — deferred until a real CDC source exists to design against.)

**Files:**

- Create: backend/app/services/v2/incremental/event_adapters.py
- Create: backend/app/services/v2/incremental/event_ingest.py
- Create: backend/app/routers/v2/refresh_events.py
- Modify: backend/app/main.py
- Modify: backend/app/tasks/v2/refresh_tasks.py
- Create: backend/tests/v2/incremental/test_event_ingest.py
- Modify: backend/tests/v2/incremental/test_refresh_cancellation.py
- Create: backend/tests/v2/incremental/integration/test_event_ingest_integration.py
- Create: backend/tests/v2/incremental/integration/test_refresh_cancellation_dialects.py

**Interfaces:**

- `ManagedWebhookAdapter.verify_and_normalize(body: bytes, *, source_id: str, signature: str, timestamp: str, secret_ref: str, now: datetime) -> ChangeEnvelope` verifies an HMAC signature, rejects expired timestamps and duplicate event IDs, and records the source schema hash.
- `ManagedOutboxAdapter.normalize(record: Mapping[str, object], *, source_id: str, received_at: datetime) -> ChangeEnvelope` accepts only the versioned outbox envelope fields and rejects missing event ID, source/resource mismatch, or schema drift. It uses the source's configured `watermark_primary_key`/`opaque_source_cursor` contract for ordering, exactly like polling.
- `EventIngestService.accept(db: Session, envelope: ChangeEnvelope, *, lease_owner: str, now: datetime) -> IngestReceipt` persists the inbox idempotently keyed on `(source_id, resource, event_id)` before any dispatch, then claims the source `RefreshRun`/`RefreshSourceState` fencing token and frozen config version/contract. It checks `assert_refresh_not_cancelled` before tentative materialization, calls `record_refresh_outcome(..., config_version=run.config_version, cursor_contract=run.cursor_contract, ...)` exactly as Task 8's polling path does, and re-checks cancellation in the same fenced transaction immediately before lineage and cursor advancement. Cancellation before commit rolls back tentative materialization and `finalize_refresh_cancellation` marks the run terminal `cancelled`; if the outcome transaction commits first, the run is simply `succeeded` and a later cancellation request gets the plain `already_terminal` result (Task 6). It returns the inbox status (`received`, `duplicate`, `processed`, or `dead_lettered`) and the source fencing token/cursor.
- Celery task `refresh.event(run_id: str)` is the only worker entry for an accepted inbox event; it reloads the durable inbox/run and source contract from `run_id`, never accepts a raw event payload or credential, and uses the same lease/fencing path. `RefreshCancellationRequested` calls `finalize_refresh_cancellation` before acknowledgement; soft timeout/graceful shutdown calls `mark_refresh_retryable` before acknowledgement. The inbox status remains replayable and the source cursor is unchanged on either interruption path.
- `replay_dead_letter(db: Session, *, dead_letter_id: str, operator_id: str, now: datetime) -> RefreshRun` creates a new idempotent run from the stored envelope and retains the original dead-letter record and reason.
- `POST /api/v2/refresh/events/webhook/{source_id}` verifies the raw signed body before parsing; the outbox adapter is an internal ingestion contract in v1 and is not exposed as an arbitrary broker endpoint. The route returns only event/run IDs and status, never source secrets or protected payloads.
- Durable inbox state uses bounded retries, explicit `dead_lettered` status, and a replay audit record. The inbox write is durable before dispatch. Equal event IDs are `duplicate` and acknowledged without changing the cursor. A replay never regresses the source cursor, and every cursor update is fencing/CAS guarded as defined in Task 6.

- [ ] **Step 1: Write failing webhook, outbox, replay, and boundary tests.**

    @pytest.mark.parametrize("case_id", ["expired-webhook", "replay-attack", "schema-drift", "config-drift-late-finish", "duplicate", "out-of-order"])
    def test_event_ingest_rejects_or_deduplicates_invalid_cases(case_id):
        result = run_event_fixture(case_id)
        assert result.reason_code in {"ACCEPTED", "DUPLICATE_EVENT", "EXPIRED_SIGNATURE", "REPLAY_DETECTED", "SCHEMA_DRIFT", "CONFIGURATION_DRIFT", "OUT_OF_ORDER"}

    def test_webhook_signature_is_checked_before_payload_processing():
        with pytest.raises(EventIngressError) as exc:
            ingest_fixture_webhook(signature="wrong", body=fixture_body("normal"))
        assert exc.value.reason_code == "INVALID_SIGNATURE"

    def test_outbox_adapter_normalizes_without_broker_dependency():
        record = outbox_record_fixture("normal")
        envelope = ManagedOutboxAdapter.normalize(record, source_id="source-001", received_at=FIXED_NOW)
        assert envelope.event_id

    def test_dlq_replay_creates_new_run_and_retains_original(db):
        receipt = ingest_fixture_event(db, "dead-lettered")
        replay = replay_dead_letter(db, dead_letter_id=receipt.dead_letter_id, operator_id="operator-001", now=FIXED_NOW)
        assert replay.id != receipt.run_id
        assert original_dead_letter(db, receipt.dead_letter_id).replayed_at is not None

    @pytest.mark.parametrize("case_id", ["cancel-before-pull", "cancel-inflight-page", "cancel-after-tentative-materialization"])
    def test_event_cancellation_keeps_inbox_and_cursor_progress_unchanged(case_id, db):
        result = run_event_fixture(case_id, db)
        assert result.status == "cancelled"
        assert result.cursor_outcome == "unchanged"
        assert result.input_dataset_version_ids == []
        assert result.pipeline_run_id is None

    def test_event_cancellation_after_commit_is_already_terminal(db):
        result = run_event_fixture("normal", db)
        assert result.status == "succeeded"
        cancelled = request_refresh_cancellation(db, run_id=result.run_id, requested_by="operator-001", reason="too late", now=FIXED_NOW)
        assert cancelled.already_terminal is True
        assert cancelled.status == "succeeded"

    def test_event_worker_message_contains_only_durable_run_id():
        message = build_refresh_dispatch_message(run_id="run-event-001", task_name="refresh.event")
        assert message.to_dict() == {"run_id": "run-event-001", "task_name": "refresh.event", "queue": "refresh.event"}

- [ ] **Step 2: Run event tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_event_ingest.py tests/v2/incremental/integration/test_event_ingest_integration.py -q)

    Expected: FAIL because no signed webhook route, durable inbox, fenced cancellation path, or DLQ replay service exists.

- [ ] **Step 3: Implement only the managed event boundary.**

    Normalize both supported sources into `ChangeEnvelope`, verify webhook signature/timestamp before parsing, enforce source-owned schema, and persist the inbox before dispatch using the same idempotency-key/dedupe path as Task 6. Call `assert_refresh_not_cancelled` at inbox and tentative-materialization boundaries and immediately before `record_refresh_outcome`; advance the `RefreshSourceState` cursor only in the fenced transaction after durable DatasetVersion/PipelineRun outcome and a final cancellation check. Cancellation before that commit rolls back tentative materialization and finalizes `cancelled`; if the outcome transaction commits first, the run is simply `succeeded` and a later cancellation request gets the plain `already_terminal` result. Use the same dedupe, lease, DatasetVersion, PipelineRun, and cursor advancement services as Task 8. Do not add a Kafka client, arbitrary broker URL, generic stream topology, CDC adapter, or best-effort direct database listener.

- [ ] **Step 4: Run event, API, and migration checks.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_event_ingest.py tests/v2/incremental/integration/test_event_ingest_integration.py tests/v2/incremental/test_refresh_polling.py -q)
    (cd "$REPO_ROOT/backend" && python scripts/run_migrations.py upgrade head)
    (cd "$REPO_ROOT" && docker compose -f docker-compose.v2.yml config --quiet)

    Expected: PASS for valid webhook/outbox envelopes, expired signature, replay attack, duplicate/equal event, schema drift, source lease fencing, configuration-revision drift with no lineage/cursor progress, cancel-before/inflight/after-tentative-materialization safe-point cancellation, the plain `already_terminal` result after a committed outcome, interruption retry with unchanged cursor, run-ID-only event dispatch, DLQ/replay, and no arbitrary broker dependency.

- [ ] **Step 5: Commit bounded event ingestion.**

    git add backend/app/services/v2/incremental/event_adapters.py backend/app/services/v2/incremental/event_ingest.py backend/app/routers/v2/refresh_events.py backend/app/main.py backend/app/tasks/v2/refresh_tasks.py backend/tests/v2/incremental/test_event_ingest.py backend/tests/v2/incremental/test_refresh_cancellation.py backend/tests/v2/incremental/integration/test_event_ingest_integration.py backend/tests/v2/incremental/integration/test_refresh_cancellation_dialects.py
    git commit -m "feat: add bounded event-driven refresh adapters"

### Task 10: Expose refresh operations and failure/replay status

- [ ] **Deliverable:** Enterprise operators can inspect schedule, source cursor, lag, run, failure, and DLQ/replay status and can trigger bounded runs/backfills through authenticated API operations that reuse the refresh services. This task also closes the Milestone 2/3 Scope Amendment's mid-milestone real-execution checkpoint: a live Compose environment proves one real batch refresh and one real webhook refresh actually land a durable DatasetVersion.

**Files:**

- Create: backend/app/services/v2/incremental/operations.py
- Create: backend/app/routers/v2/refresh.py
- Modify: backend/app/main.py
- Modify: backend/app/routers/v2/connections.py
- Create: backend/tests/v2/incremental/test_refresh_api.py
- Modify: backend/tests/v2/incremental/test_refresh_cancellation.py
- Modify: backend/tests/v2/incremental/test_refresh_task_dispatch.py
- Create: scripts/verify_refresh_checkpoint.sh
- Create: backend/scripts/seed_refresh_checkpoint_fixture.py
- Create: test_data/runtime/fixtures/checkpoint_webhook_body.json
- Create: backend/tests/v2/incremental/test_refresh_checkpoint_gate.py
- Modify: .github/workflows/agent-mvp.yml

**Interfaces:**

- `RefreshStatus` contains source ID, resource, `RefreshPolicy`, current `config_version` and `cursor_contract`, cursor, cursor observed time, source fencing token (never writable by the caller), latest run ID/status, input DatasetVersion ID, PipelineRun ID, freshness lag seconds, duplicate/late/retry/DLQ counts, next schedule time, SLA status, and bounded backfill window.
- `backend/scripts/seed_refresh_checkpoint_fixture.py` is a one-shot CLI, run only inside the checkpoint's own freshly migrated database, never in production. It persists two synthetic sources against the fixture database that is already the backend's configured `DATABASE_URL` in this Compose stack: `source-checkpoint-batch` (a trivial single-row table seeded in the same database, `watermark_primary_key` contract, `RefreshPolicy.BATCH`) and `source-checkpoint-event` (`opaque_source_cursor` contract, `RefreshPolicy.EVENT_DRIVEN`, with a managed webhook secret it generates). It mints one short-lived operator-scoped delegated credential in-process using the app's own credential-signing configuration (no OAuth round trip — this bypasses only the *transport*, not the identity/scope checks: the minted token has the same claim shape, audience, and operator scope Task 13's verification path requires). It prints one line of canonical JSON to stdout: `{"operator_token": str, "webhook_secret": str, "webhook_signature": str}`, where `webhook_signature` is the HMAC signature of `test_data/runtime/fixtures/checkpoint_webhook_body.json`'s exact bytes computed with `webhook_secret`, matching `ManagedWebhookAdapter.verify_and_normalize`'s expected signature scheme (Task 9). It writes no output file and never logs the token or secret.
- `test_data/runtime/fixtures/checkpoint_webhook_body.json` is one fixed, deterministic `ChangeEnvelope`-shaped JSON body (fixed `event_id`, `primary_key`, `payload`, `occurred_at`) for `source-checkpoint-event`; it is a public test fixture with synthetic values only.
- `scripts/verify_refresh_checkpoint.sh` is a standalone script, runnable locally or in CI, modeled on `scripts/verify_m1_stabilization_gate.sh` (same `.env` backup/restore-via-trap safety, same unique `-p` Compose project, same `down -v --remove-orphans` cleanup on every exit path). It starts `docker compose -p <unique-project> -f docker-compose.v2.yml --profile refresh up --build -d --wait` — the explicit `--profile refresh` flag is required because Task 6A's default development profile omits the refresh worker services, so a plain `up` without it would leave `refresh.poll`/`refresh.event` unconsumed and this checkpoint would hang or falsely pass. It runs `seed_refresh_checkpoint_fixture.py` inside the running `backend` container via `docker compose exec -T backend`, captures its JSON stdout, and uses the operator token as a bearer `Authorization` header for every subsequent request. It triggers a real batch run on `source-checkpoint-batch` via `POST /api/v2/refresh/sources/source-checkpoint-batch/run`, delivers the fixed fixture body to `POST /api/v2/refresh/events/webhook/source-checkpoint-event` with the minted HMAC signature, and polls `GET /api/v2/refresh/sources/{source_id}/status` for each of the two sources independently (never conflating the two `latest_run` values) until both report `status == "succeeded"` or a 40-second bounded timeout is exceeded, in which case it fails loudly with the last observed status. It exits non-zero on any curl failure, non-`succeeded` terminal status, or timeout.
- `RefreshTriggerRequest` contains only `mode: RefreshPolicy` and optional `backfill_from`/`backfill_to` instants; the server loads source, resource, cursor, credentials, and connector configuration from the persisted connection contract.
- `CancelRefreshRequest` contains exactly `reason: str`; the server derives the operator identity from the verified credential and never accepts a lease owner, fencing token, cursor, source URL, credential, event payload, or broker address.
- `get_refresh_status(db: Session, *, source_id: str, resource: str | None = None) -> RefreshStatus` returns persisted state only and never reconstructs a cursor from request data.
- `trigger_refresh(db: Session, *, source_id: str, resource: str, mode: RefreshPolicy, backfill_from: datetime | None, backfill_to: datetime | None, operator_id: str, now: datetime) -> RefreshRun` validates the stored source contract and creates a durable idempotent run.
- `cancel_refresh_run(db: Session, *, run_id: str, operator_id: str, reason: str, now: datetime) -> RefreshRun` authorizes the operator and delegates to Task 6 `request_refresh_cancellation`; it returns the durable `cancel_requested`/`cancelled` state, or the plain `already_terminal=True` result when the run had already reached any terminal status. Neither outcome changes the committed result or progress.
- `replay_refresh_run(db: Session, *, run_id: str, dead_letter_id: str | None, operator_id: str, now: datetime) -> RefreshRun` delegates to `replay_dead_letter` or a failed-run retry policy and never edits the original run/cursor.
- Celery task `refresh.replay(run_id: str)` consumes only the newly created durable replay run ID on `refresh.replay`; it reloads the retained dead-letter/failed-run record and source contract inside the worker, never receives an operator cursor, event payload, broker address, or credential.
- `get_refresh_health(db: Session, *, now: datetime, observations: Mapping[str, QueueObservation]) -> RefreshOperability` delegates to `collect_refresh_operability` and returns only queue depth/age, run lag, retry/DLQ counts, broker/worker readiness, and observed time.
- `GET /api/v2/refresh/sources/{source_id}/status`, `POST /api/v2/refresh/sources/{source_id}/run`, `POST /api/v2/refresh/runs/{run_id}/cancel`, `POST /api/v2/refresh/runs/{run_id}/replay`, `PUT /api/v2/refresh/sources/{source_id}/schedule`, and `GET /api/v2/refresh/health` use existing editor/operator authorization and return typed `RefreshStatus`/`RefreshRun`/`RefreshOperability` values. The cancel route requires the editor/operator scope, persists the request before returning `202` with `cancel_requested_at`/actor/reason and terminal `cancelled` state to an authorized operator, and returns `200` with `{"status": "<terminal status>", "already_terminal": true}` (not a `409`, not a typed error code) when the run had already reached a terminal outcome. Mutation routes commit the durable run or schedule first and then enqueue a `RefreshDispatchMessage` containing only `run_id`, task name, and queue; a connector pull, event materialization, replay, transform, or cancellation finalization never runs in the FastAPI process. Backfill is limited to the persisted schedule window; arbitrary source URLs, cursors, credentials, broker addresses, and payloads are rejected.

- [ ] **Step 1: Write failing refresh operation API tests.**

    def test_refresh_status_exposes_cursor_lag_schedule_and_failure(client, operator_headers):
        response = client.get("/api/v2/refresh/sources/source-001/status", headers=operator_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["cursor"]["primary_key"] == "100"
        assert body["config_version"] == 7
        assert body["cursor_contract"] == "watermark_primary_key"
        assert body["lag_seconds"] == 120
        assert body["latest_run"]["status"] == "dead_lettered"
        assert body["latest_run"]["config_version"] == 7

    def test_refresh_trigger_and_replay_are_bounded_and_idempotent(client, operator_headers):
        triggered = client.post("/api/v2/refresh/sources/source-001/run", json={"mode": "micro_batch"}, headers=operator_headers)
        replayed = client.post("/api/v2/refresh/runs/run-dead-001/replay", json={}, headers=operator_headers)
        assert triggered.status_code == 202
        assert replayed.status_code == 202
        assert triggered.json()["cursor_before"] == replayed.json()["cursor_before"]

    def test_refresh_cancel_persists_durable_request_and_returns_terminal_safe_state(client, operator_headers):
        response = client.post("/api/v2/refresh/runs/run-running-001/cancel", json={"reason": "planned source maintenance"}, headers=operator_headers)
        assert response.status_code == 202
        assert response.json()["status"] in {"cancel_requested", "cancelled"}
        assert response.json()["cancel_requested_by"] == "operator-001"
        assert response.json()["cancel_reason"] == "planned source maintenance"
        status = client.get("/api/v2/refresh/sources/source-001/status", headers=operator_headers)
        assert status.json()["latest_run"]["cancel_requested_at"] is not None

    def test_refresh_cancel_requires_operator_authorization(client, viewer_headers):
        response = client.post("/api/v2/refresh/runs/run-running-001/cancel", json={"reason": "not allowed"}, headers=viewer_headers)
        assert response.status_code == 403

    @pytest.mark.parametrize("run_id", ["run-succeeded-001", "run-failed-no-outcome-001", "run-dead-lettered-no-outcome-001"])
    def test_refresh_cancel_after_terminal_state_is_already_terminal(client, operator_headers, run_id):
        response = client.post(f"/api/v2/refresh/runs/{run_id}/cancel", json={"reason": "too late"}, headers=operator_headers)
        assert response.status_code == 200
        assert response.json()["already_terminal"] is True
        status = client.get("/api/v2/refresh/sources/source-001/status", headers=operator_headers)
        latest = status.json()["latest_run"]
        assert latest["status"] in {"succeeded", "failed", "dead_lettered"}

    def test_refresh_trigger_only_persists_and_enqueues_durable_run_id(client, operator_headers, monkeypatch):
        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("connector pull must not run in FastAPI")
        monkeypatch.setattr("app.services.connection.base.ConnectorBase.pull_delta", fail_if_called)
        sent = capture_refresh_messages(monkeypatch)
        response = client.post("/api/v2/refresh/sources/source-001/run", json={"mode": "micro_batch"}, headers=operator_headers)
        assert response.status_code == 202
        assert sent == [{"run_id": response.json()["run_id"], "task_name": "refresh.poll", "queue": "refresh.poll"}]
        assert persisted_refresh_run(response.json()["run_id"]).status == "queued"

    def test_refresh_health_exposes_backpressure_and_readiness_without_payloads(client, operator_headers):
        response = client.get("/api/v2/refresh/health", headers=operator_headers)
        assert response.status_code == 200
        body = response.json()
        assert {item["queue"] for item in body["queues"]} == {"refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay"}
        assert "payload" not in response.text
        assert "credential" not in response.text

    def test_saturated_refresh_queue_does_not_block_status_or_agent_dispatch(client, operator_headers, fake_broker):
        fake_broker.fill("refresh.poll")
        status = client.get("/api/v2/refresh/sources/source-001/status", headers=operator_headers)
        assert status.status_code == 200
        agent_task = fake_broker.send("agent.turn_execute", ["turn-001"], "agent.interactive")
        assert agent_task.queue == "agent.interactive"

    def test_refresh_schedule_api_persists_timezone_calendar_and_sla(client, operator_headers):
        response = client.put("/api/v2/refresh/sources/source-001/schedule", json={"cron_expr": "0 2 * * *", "timezone": "Asia/Shanghai", "business_calendar": ["2026-10-01"], "sla_seconds": 86400, "backfill_window_seconds": 172800, "enabled": True}, headers=operator_headers)
        assert response.status_code == 200
        assert response.json()["timezone"] == "Asia/Shanghai"
        assert response.json()["sla_seconds"] == 86400

    def test_refresh_api_rejects_caller_cursor_url_and_broker(client, operator_headers):
        response = client.post("/api/v2/refresh/sources/source-001/run", json={"cursor": "spoof", "source_url": "https://example.invalid", "broker": "kafka://attacker"}, headers=operator_headers)
        assert response.status_code == 422

    Add `backend/tests/v2/incremental/test_refresh_checkpoint_gate.py`, mirroring `backend/tests/agent/test_stabilization_gate.py`'s pattern for the M1 gate:

    import pathlib
    import stat

    REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
    GATE_SCRIPT = REPO_ROOT / "scripts" / "verify_refresh_checkpoint.sh"
    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "agent-mvp.yml"

    def test_refresh_checkpoint_script_exists_is_executable_and_uses_the_refresh_profile():
        assert GATE_SCRIPT.exists()
        assert GATE_SCRIPT.stat().st_mode & stat.S_IXUSR
        source = GATE_SCRIPT.read_text()
        assert "--profile refresh" in source
        assert "seed_refresh_checkpoint_fixture.py" in source
        assert "down -v --remove-orphans" in source
        assert "trap cleanup EXIT" in source

    def test_refresh_checkpoint_polls_both_sources_independently():
        source = GATE_SCRIPT.read_text()
        assert "source-checkpoint-batch" in source
        assert "source-checkpoint-event" in source

    def test_refresh_checkpoint_is_wired_into_ci():
        import yaml
        data = yaml.safe_load(WORKFLOW.read_text())
        run_text = "\n".join(
            str(step.get("run", ""))
            for job in data["jobs"].values()
            for step in job.get("steps", [])
        )
        assert "scripts/verify_refresh_checkpoint.sh" in run_text

- [ ] **Step 2: Run the focused API tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_api.py -q)

    Expected: FAIL because no normalized refresh status/trigger/cancel/replay/health endpoints exist, the current schedule route stores only a cron string in connection JSON, and the API has no durable run-ID-only dispatch, fenced cancellation response, or connector-inline guard.

- [ ] **Step 3: Implement the thin refresh operations adapter.**

    Add typed request/response models, route all operations to `ScheduleService`, `PollingRefreshService`, `EventIngestService`, Task 6 cancellation services, and `collect_refresh_operability`, and preserve original run/cursor records on retries/replays. Register the router once, retain the existing connection route as a compatibility delegate, and return no raw event payload, credential, cursor override, or external broker configuration. Commit the durable run before calling `enqueue_refresh_run`; the only broker message is the Task 6A `RefreshDispatchMessage`, so a saturated or unavailable refresh queue returns a queued `202` and does not block Runtime/status requests or the `agent.interactive` queue. Cancellation is a durable database action, not a FastAPI worker shortcut: an authorized request commits `cancel_requested`/`cancelled` before returning, and a request against any already-terminal run returns `200` with the plain `already_terminal` result without mutation.

    Add `backend/scripts/seed_refresh_checkpoint_fixture.py` and `scripts/verify_refresh_checkpoint.sh` per the Interfaces above: the seed script persists the two synthetic sources and mints the operator credential/webhook signature in-process; the shell script starts Compose with `--profile refresh` explicitly (the plain default profile does not start refresh workers, per Task 6A), seeds through the running container, drives one real batch trigger and one real signed webhook delivery over the live API, and polls each source's status independently until both succeed or the bounded timeout fails the script. Add `test_data/runtime/fixtures/checkpoint_webhook_body.json` as the fixed fixture body. Wire a `refresh-checkpoint` step into `.github/workflows/agent-mvp.yml`'s `m1-stabilization-gate`-style job pattern (its own step, `if: failure()` log upload, `down -v --remove-orphans` cleanup) so this checkpoint runs in CI, not only when a developer remembers to run it locally; Task 28 reuses the same script rather than duplicating its logic.

- [ ] **Step 4: Run refresh API, end-to-end incremental tests, and the mid-milestone real-execution checkpoint.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_api.py tests/v2/incremental/test_refresh_schedule.py tests/v2/incremental/test_refresh_polling.py tests/v2/incremental/test_refresh_cancellation.py tests/v2/incremental/test_event_ingest.py -q)

    Expected: PASS for status, T+1 schedule, lag/cursor visibility, queue depth/oldest age/run lag/retry/DLQ/readiness visibility, bounded manual trigger/backfill, authenticated cancel-before/inflight/after-materialization state, the plain `already_terminal` result after any terminal state, failed/DLQ replay, authentication, run-ID-only post-commit enqueue, saturated-queue isolation, and rejection of caller-supplied cursor/source/broker values.

    Then run the Milestone 2/3 Scope Amendment's mid-milestone real-execution checkpoint — this is required evidence in addition to the unit tests above, not a substitute for them:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental/test_refresh_checkpoint_gate.py -q)
    (cd "$REPO_ROOT" && bash scripts/verify_refresh_checkpoint.sh)

    Expected: the gate test confirms the script exists, is executable, passes `--profile refresh` to `docker compose up`, polls both sources independently, and is wired into `.github/workflows/agent-mvp.yml`. Running the script itself starts Compose with the `refresh` worker profile explicitly, seeds `source-checkpoint-batch`/`source-checkpoint-event` and mints a real operator credential inside the running container, and both the live batch trigger and the live signed webhook event independently reach a durable `succeeded` `RefreshRun` with a real `DatasetVersion` through the actual Celery `refresh.poll`/`refresh.event` queues in a freshly built Compose environment — not a unit-test mock.

- [ ] **Step 5: Commit refresh operations.**

    git add backend/app/services/v2/incremental/operations.py backend/app/routers/v2/refresh.py backend/app/main.py backend/app/routers/v2/connections.py backend/tests/v2/incremental/test_refresh_api.py backend/tests/v2/incremental/test_refresh_cancellation.py backend/tests/v2/incremental/test_refresh_task_dispatch.py scripts/verify_refresh_checkpoint.sh backend/scripts/seed_refresh_checkpoint_fixture.py test_data/runtime/fixtures/checkpoint_webhook_body.json backend/tests/v2/incremental/test_refresh_checkpoint_gate.py .github/workflows/agent-mvp.yml
    git commit -m "feat: expose enterprise refresh operations"

### Task 10A: Python refresh capacity/extraction decision — deferred

- [ ] **Deliverable:** None in this plan. Per the Milestone 2/3 Scope Amendment, the Python-versus-alternate-runtime capacity and extraction decision is deferred until real Phase 2 production load exists against the seed customer's workload. A synthetic local capacity harness measured before real traffic exists would not produce a decision worth trusting, and the named Celery queues and `RefreshWorkerLimits` contract Task 6A already ships are sufficient to measure real load later without a rewrite.

**Revisit when:** Phase 2 (Tasks 6-20) has run in production against real refresh volume for the seed customer (or another enterprise customer) for long enough to have real throughput, latency, and queue-depth data. At that point, open a new brainstorming cycle for this decision — do not resume this task's original scope from memory, since the right measurement approach may look different once real traffic patterns are known. The reusable contract this plan already provides: `RefreshWorkerLimits` (Task 6A), the named `refresh.schedule`/`refresh.poll`/`refresh.event`/`refresh.replay` queues (Task 6A), and `collect_refresh_operability` (Task 6A) for queue depth/lag/retry/DLQ observation.

**No files are created or modified by this task.**

### Workstream 2B — Unified Semantic Runtime

### Task 11: Add the immutable SemanticSnapshot schema and lineage tables

- [ ] **Deliverable:** A database snapshot is a separate immutable contract from OntologyRelease and can represent the complete governed input set.

**Files:**

- Create: backend/app/models/semantic_snapshot.py
- Modify: backend/app/models/ontology_release.py
- Modify: backend/app/models/v2/pipeline.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0023_semantic_snapshot.py
- Create: backend/tests/runtime/__init__.py
- Create: backend/tests/runtime/test_snapshot_schema.py

**Interfaces:**

- SemanticSnapshot exposes id, ontology_release_id, quality_summary, evidence_summary, materialization_hash, status, created_by, and created_at.
- SemanticSnapshotInput exposes snapshot_id, dataset_version_id, and pipeline_run_id, with a unique constraint per snapshot/input pair. The association is the authoritative complete set rather than a truncated JSON list.
- OntologyRelease.status is constrained to draft, published, or revoked; existing release rows are backfilled to their current publication state without changing their schema hash or bytes.
- PipelineRun exposes governed completion and its dataset-version provenance through the `PipelineRunInput` association; a snapshot input can reference only a successful, completed run and its output dataset version.

- [ ] **Step 1: Write failing schema and immutability tests.**

    def test_snapshot_has_required_contract_and_complete_inputs(db):
        snapshot = SemanticSnapshot(
            id="snap-valid-001",
            ontology_release_id="release-valid-001",
            quality_summary={"row_count": 2},
            evidence_summary={"citations": ["ev-001"]},
            materialization_hash="a" * 64,
            status="materialized",
            created_by="user-001",
        )
        db.add(snapshot)
        db.commit()
        db.add(SemanticSnapshotInput(
            snapshot_id=snapshot.id,
            dataset_version_id="dataset-version-001",
            pipeline_run_id="pipeline-run-001",
        ))
        db.commit()
        assert snapshot.ontology_release_id == "release-valid-001"
        assert snapshot.materialization_hash == "a" * 64

    def test_snapshot_input_is_unique_per_dataset_and_run(db):
        assert duplicate_snapshot_input_raises_integrity_error(db)

- [ ] **Step 2: Run the focused schema tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_snapshot_schema.py -q)

    Expected: FAIL because the snapshot model, association table, and migration do not exist.

- [ ] **Step 3: Implement the model and migration.**

    Register the models in the central loader, add foreign keys and uniqueness constraints, enforce 64-character SHA-256 materialization hashes, add an append-only database trigger or equivalent service-level update guard, and preserve the current release manifest bytes. The migration down revision is `0022_refresh_contract`.

- [ ] **Step 4: Run migration and schema tests.**

    Run:

    (cd backend && python -m pytest tests/runtime/test_snapshot_schema.py -q)

    (cd backend && python scripts/run_migrations.py upgrade head)

    Expected: PASS, with one new Alembic head and no duplicate input rows.

- [ ] **Step 5: Commit the snapshot schema.**

    git add backend/app/models/semantic_snapshot.py backend/app/models/ontology_release.py backend/app/models/v2/pipeline.py backend/app/models/__init__.py backend/alembic/versions/0023_semantic_snapshot.py backend/tests/runtime

    git commit -m "feat: add immutable semantic snapshots"

### Task 12: Materialize snapshots and capture complete lineage

- [ ] **Deliverable:** Snapshot creation validates published semantic state and completed pipeline inputs, records quality/evidence summaries, and computes a reproducible materialization hash.

**Files:**

- Create: backend/app/schemas/runtime_snapshot.py
- Create: backend/app/services/runtime/lineage.py
- Create: backend/app/services/runtime/snapshots.py
- Create: backend/tests/runtime/test_snapshot_materialization.py
- Modify: backend/tests/runtime/conftest.py

**Interfaces:**

- collect_lineage(db, dataset_version_ids: Sequence[str]) -> LineageBundle returns sorted, de-duplicated dataset-version IDs, every originating pipeline-run ID, quality summary, and evidence summary.
- materialize_snapshot(db, *, ontology_release_id: str, dataset_version_ids: Sequence[str], created_by: str) -> SemanticSnapshot validates release publication and successful governed runs, persists all input associations in one transaction, and computes the canonical materialization hash.
- get_snapshot(db, snapshot_id: str) -> SnapshotView returns immutable pins and provenance; no update method exists.
- Canonical materialization input is a JSON object with ontology_release_id, sorted input pairs, quality summary, and evidence summary; it uses UTF-8, sorted keys, compact separators, and SHA-256.

- [ ] **Step 1: Write failing materialization tests for normal and edge cases.**

    def test_materialize_snapshot_binds_all_inputs_and_is_order_independent(db, valid_release, completed_runs):
        first = materialize_snapshot(
            db, ontology_release_id=valid_release.id,
            dataset_version_ids=["dv-002", "dv-001"], created_by="user-001",
        )
        second = materialize_snapshot(
            db, ontology_release_id=valid_release.id,
            dataset_version_ids=["dv-001", "dv-002"], created_by="user-001",
        )
        assert first.materialization_hash == second.materialization_hash
        assert set(first.pipeline_run_ids) == {"run-001", "run-002"}

    def test_materialize_snapshot_rejects_failed_run_or_unpublished_release(db, failed_run, draft_release):
        with pytest.raises(SnapshotValidationError) as exc:
            materialize_snapshot(db, ontology_release_id=draft_release.id,
                                 dataset_version_ids=["dv-failed"], created_by="user-001")
        assert exc.value.reason_code in {"RELEASE_NOT_PUBLISHED", "LINEAGE_NOT_GOVERNED"}

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_snapshot_materialization.py -q)

    Expected: FAIL because lineage collection, canonical hashing, and materialization are absent.

- [ ] **Step 3: Implement lineage and materialization.**

    Join every requested dataset version to every originating pipeline run, reject missing provenance, non-success status, duplicate or foreign-tenant inputs, and draft/revoked releases, then insert all rows atomically. Build quality and evidence summaries from stored pipeline statistics and source citations without embedding protected row values.

- [ ] **Step 4: Run function and database tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_snapshot_materialization.py tests/runtime/test_snapshot_schema.py -q)

    Expected: PASS for multi-input, reordered-input, incomplete-lineage, and release-state cases; an existing snapshot remains unchanged after a new materialization.

- [ ] **Step 5: Commit snapshot materialization.**

    git add backend/app/schemas/runtime_snapshot.py backend/app/services/runtime/lineage.py backend/app/services/runtime/snapshots.py backend/tests/runtime/test_snapshot_materialization.py backend/tests/runtime/conftest.py

    git commit -m "feat: materialize governed semantic snapshots"

### Task 13: Implement trusted delegated credentials and Runtime context

- [ ] **Deliverable:** All Runtime requests obtain a verified Agent/service principal and delegated user principal from a short-lived credential, with audience, scope, domain, expiry, and revocation checks.

**Files:**

- Create: backend/app/models/runtime_identity.py
- Modify: backend/app/models/oauth.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0024_runtime_identity.py
- Create: backend/app/services/runtime/credentials.py
- Create: backend/app/deps/runtime.py
- Create: backend/tests/runtime/test_runtime_credentials.py
- Modify: backend/tests/agent/test_oauth_security_properties.py

**Interfaces:**

- RuntimePrincipal is a frozen value with agent_id, user_id, security_domain_id, audience, scope: frozenset[str], and token_id.
- RuntimeContext is a frozen value with principal and correlation_id.
- issue_delegated_credential(db, *, client_id: str, user_id: str, audience: str, scope: set[str], ttl_seconds: int, now: datetime) -> str uses OAuth 2.0 token-exchange semantics and stores only a token hash plus revocation metadata.
- verify_delegated_credential(db, token: str, *, audience: str, required_scope: str, now: datetime) -> RuntimeContext verifies issuer/signature, audience, scope, expiry, token revocation, registered active Agent/service identity, active user, and same security domain; failures raise RuntimeAccessError with stable reason codes.
- OAuth client registration stores security domain, allowed audiences, and capability names. No caller-provided principal ID is consulted by the verifier.

- [ ] **Step 1: Write failing credential and spoofing tests.**

    def test_valid_delegation_contains_two_verified_principals(db):
        token = issue_delegated_credential(
            db, client_id="agent-service-001", user_id="user-001",
            audience="ontexus-runtime", scope={"ontology:read"}, ttl_seconds=300,
            now=FIXED_NOW,
        )
        context = verify_delegated_credential(
            db, token, audience="ontexus-runtime",
            required_scope="ontology:read", now=FIXED_NOW,
        )
        assert context.principal.agent_id == "agent-service-001"
        assert context.principal.user_id == "user-001"

    @pytest.mark.parametrize("case_id", [
        "missing", "bad-signature", "wrong-audience", "missing-scope",
        "expired", "revoked", "inactive-agent", "inactive-user", "cross-domain",
    ])
    def test_invalid_delegation_is_structured_denial(db, case_id):
        with pytest.raises(RuntimeAccessError) as exc:
            verify_fixture_credential(db, case_id)
        assert exc.value.reason_code in RUNTIME_DENIAL_CODES

    def test_body_identity_cannot_override_credential(db):
        context = verified_context(db)
        assert authorize_request(context, body_agent_id="agent-other", body_user_id="user-other").agent_id == context.principal.agent_id

- [ ] **Step 2: Run the focused security tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_credentials.py tests/agent/test_oauth_security_properties.py -q)

    Expected: FAIL because the current OAuthContext has no actor subject, audience/domain verification, token revocation lookup, or token-exchange credential.

- [ ] **Step 3: Implement the shared verification path.**

    Extend the registered OAuth client contract, add a hashed delegated-token record and migration `0024_runtime_identity.py` with `down_revision="0023_semantic_snapshot"`, sign short-lived credentials with the configured issuer key, bind actor and user to the same security domain, and expose one FastAPI dependency that returns RuntimeContext. Map all failures to stable codes such as MISSING_DELEGATION, INVALID_DELEGATION, AUDIENCE_DENIED, SCOPE_DENIED, EXPIRED_DELEGATION, REVOKED_DELEGATION, AGENT_INACTIVE, USER_INACTIVE, and CROSS_SECURITY_DOMAIN.

- [ ] **Step 4: Run credential, OAuth, and schema tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_credentials.py tests/agent/test_oauth_security_properties.py -q)

    Expected: PASS for valid dual-principal access and every missing, malformed, expired, revoked, inactive, scope, audience, and cross-domain case; request-body IDs have no authority.

- [ ] **Step 5: Commit trusted delegation.**

    git add backend/app/models/runtime_identity.py backend/app/models/oauth.py backend/app/models/__init__.py backend/alembic/versions/0024_runtime_identity.py backend/app/services/runtime/credentials.py backend/app/deps/runtime.py backend/tests/runtime/test_runtime_credentials.py backend/tests/agent/test_oauth_security_properties.py

    git commit -m "feat: add verified delegated runtime access"

### Task 14: Define transport-neutral runtime contracts and intersection policy

- [ ] **Deliverable:** Runtime code has one typed request/result/error vocabulary for snapshot access, evidence, rules, capabilities, entitlements, and policy decisions.

**Files:**

- Create: backend/app/schemas/runtime.py
- Create: backend/app/services/runtime/policy.py
- Create: backend/tests/runtime/test_runtime_contracts.py
- Create: backend/tests/runtime/test_runtime_policy.py

**Interfaces:**

- ReasonCode is an enum containing ALLOW, MISSING_DELEGATION, INVALID_DELEGATION, AUDIENCE_DENIED, SCOPE_DENIED, EXPIRED_DELEGATION, REVOKED_DELEGATION, AGENT_CAPABILITY_DENIED, USER_ENTITLEMENT_DENIED, CROSS_SECURITY_DOMAIN, SNAPSHOT_NOT_FOUND, SNAPSHOT_NOT_GOVERNED, SNAPSHOT_STALE, SNAPSHOT_FRESHNESS_HITL, POLICY_DENIED, ACTION_NOT_ELIGIBLE, PLAN_EXPIRED, PRECONDITION_CONFLICT, BINDING_DRIFT, UNSUPPORTED_ACTION, ROW_COUNT_MISMATCH, UNKNOWN_EXECUTION_OUTCOME, and INVALID_PLAN_HASH.
- EvidenceCitation contains source_id, source_type, locator, and content_hash; RuleOutcome contains rule_id, result, and reason_code.
- InvestigationRequest contains semantic_snapshot_id, query, ontology_id, entity_type, filters, and limit.
- InvestigationResult contains decision, reason_code, semantic_snapshot_id, ontology_release_id, evidence_citations, rule_outcome, result, and correlation_id. Denials set result to null; authorized no-match queries set result to an empty collection with decision ALLOW.
- PolicyDecision contains allowed, reason_code, agent_capability, user_entitlement, and policy_evidence.
- evaluate_access(context: RuntimeContext, *, required_capability: str, snapshot: SnapshotView, ontology_id: str, db: Session) -> PolicyDecision computes Agent capability ∩ user entitlement ∩ runtime policy and never treats a missing result as a denial.

- [ ] **Step 1: Write failing contract and policy tests.**

    def test_empty_authorized_result_is_allow():
        result = InvestigationResult(
            decision="ALLOW", reason_code="ALLOW",
            semantic_snapshot_id="snap-valid-001",
            ontology_release_id="release-valid-001",
            evidence_citations=[], rule_outcome=[],
            result=[], correlation_id="corr-001",
        )
        assert result.decision == "ALLOW"
        assert result.result == []

    @pytest.mark.parametrize("case_id", [
        "agent-only", "user-only", "policy-denied", "snapshot-stale",
    ])
    def test_intersection_policy_denies_with_stable_reason(case_id):
        decision = evaluate_fixture_policy(case_id)
        assert not decision.allowed
        assert decision.reason_code in {
            "AGENT_CAPABILITY_DENIED", "USER_ENTITLEMENT_DENIED",
            "POLICY_DENIED", "SNAPSHOT_STALE",
        }

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_contracts.py tests/runtime/test_runtime_policy.py -q)

    Expected: FAIL because the typed runtime contracts and shared intersection evaluator do not exist.

- [ ] **Step 3: Implement the contracts and policy evaluator.**

    Use Pydantic models for wire validation and immutable domain values for service calls. Validate snapshot and ontology identity before querying, preserve evidence and rule outcomes, and make every failure carry decision, reason code, correlation ID, and snapshot information without returning protected rows.

- [ ] **Step 4: Run contract and policy tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_contracts.py tests/runtime/test_runtime_policy.py -q)

    Expected: PASS for allow-with-data, allow-empty, Agent-only denial, user-only denial, policy denial, stale snapshot, and protected-content non-leakage.

- [ ] **Step 5: Commit the shared vocabulary.**

    git add backend/app/schemas/runtime.py backend/app/services/runtime/policy.py backend/tests/runtime/test_runtime_contracts.py backend/tests/runtime/test_runtime_policy.py

    git commit -m "feat: define shared runtime contracts and policy"

### Task 15: Implement snapshot-pinned investigation and immutable action plans

- [ ] **Deliverable:** One service performs investigation and proposal creation without production writes, pins snapshot and release provenance, and persists immutable action plans.

**Files:**

- Create: backend/app/models/runtime_plan.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0025_runtime_plans.py
- Create: backend/app/services/runtime/service.py
- Create: backend/tests/runtime/test_runtime_service.py

**Interfaces:**

- ActionPlanRequest contains semantic_snapshot_id, action_id, typed parameters, optional target_selector, and idempotency_key.
- ActionPlan contains id, semantic_snapshot_id, ontology_release_id, verified agent_id and user_id, input facts, evidence citations, rule outcomes, optional managed_action_binding_id and binding_version, frozen typed parameters, normalized target key tuple, before_image_hash, version_hash, predicted diff, impact scope, risk classification, policy decision, precondition hashes, expiry, idempotency key, and plan_hash.
- RuntimeService.investigate(request: InvestigationRequest, context: RuntimeContext, db: Session) -> InvestigationResult uses only the pinned snapshot and shared policy.
- RuntimeService.create_action_plan(request: ActionPlanRequest, context: RuntimeContext, db: Session) -> ActionPlan validates access, current action eligibility, snapshot and preconditions, commits one immutable non-writing proposal, and never calls a production connector.
- RuntimeService.get_action_plan(plan_id: str, context: RuntimeContext, db: Session) -> ActionPlan returns only plans visible to the verified user and Agent.

- [ ] **Step 1: Write failing service tests.**

    def test_investigate_returns_snapshot_release_evidence_and_rules(db, runtime_context):
        result = RuntimeService().investigate(
            InvestigationRequest(
                semantic_snapshot_id="snap-valid-001",
                query="supplier SUP001",
                ontology_id="ontology-supply-001",
                entity_type="Supplier",
                filters={},
                limit=20,
            ),
            runtime_context,
            db,
        )
        assert result.decision == "ALLOW"
        assert result.semantic_snapshot_id == "snap-valid-001"
        assert result.ontology_release_id == "release-valid-001"
        assert result.evidence_citations

    def test_create_action_plan_is_immutable_and_non_writing(db, runtime_context, production_spy):
        plan = RuntimeService().create_action_plan(valid_plan_request(), runtime_context, db)
        assert plan.plan_hash
        assert production_spy.calls == []
        with pytest.raises(ImmutablePlanError):
            update_action_plan(db, plan.id, {"parameters": {"status": "changed"}})

    def test_release_only_request_is_rejected(db, runtime_context):
        with pytest.raises(RuntimeAccessError) as exc:
            RuntimeService().investigate(request_without_snapshot(), runtime_context, db)
        assert exc.value.reason_code == "SNAPSHOT_NOT_GOVERNED"

- [ ] **Step 2: Run the focused service tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_service.py -q)

    Expected: FAIL because RuntimeService, action-plan persistence, snapshot pinning, and non-writing proposal semantics are absent.

- [ ] **Step 3: Implement the shared service and immutable plan store.**

    Resolve snapshot to release and lineage, call evaluate_access, query through the existing ontology query primitives with snapshot-scoped data, collect only hashable evidence locators, evaluate domain rules, and serialize protected results only on ALLOW. Store typed plan fields and a canonical plan hash while excluding generated storage IDs, timestamps, correlation IDs, HTTP/MCP envelopes, tracing IDs, and presentation-only fields. Set migration `0025_runtime_plans.py` to `down_revision="0024_runtime_identity"` so the refresh, snapshot, identity, and plan schema form one Alembic chain.

- [ ] **Step 4: Run service, snapshot, policy, and migration tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_service.py tests/runtime/test_snapshot_materialization.py tests/runtime/test_runtime_policy.py -q)

    Expected: PASS for data and empty investigations, structured denial, immutable read-only/writable proposals, release-only rejection, and zero production connector calls.

- [ ] **Step 5: Commit the shared Runtime service.**

    git add backend/app/models/runtime_plan.py backend/app/models/__init__.py backend/alembic/versions/0025_runtime_plans.py backend/app/services/runtime/service.py backend/tests/runtime/test_runtime_service.py

    git commit -m "feat: add snapshot-pinned runtime service"

### Task 16: Expose the shared Runtime through versioned REST endpoints

- [ ] **Deliverable:** External enterprise Agents can investigate, create/read action plans, and retrieve execution status through /api/v2/runtime with the same verified context and result contract.

**Files:**

- Create: backend/app/routers/v2/runtime.py
- Modify: backend/app/main.py
- Create: backend/tests/runtime/test_runtime_api.py

**Interfaces:**

- POST /api/v2/runtime/investigate accepts InvestigationRequest and requires the RuntimeContext dependency; it returns InvestigationResult.
- POST /api/v2/runtime/action-plans accepts ActionPlanRequest and returns ActionPlan without writing production data.
- GET /api/v2/runtime/action-plans/{plan_id} returns the immutable plan after verified principal checks.
- GET /api/v2/runtime/execution-status/{plan_id} returns a typed status record or a stable not-started status until Phase 3 execution exists.
- RuntimeAccessError and all typed service errors map to JSON with decision, reason_code, correlation_id, and snapshot information; request-body agent_id and user_id fields are ignored or rejected.

- [ ] **Step 1: Write failing API tests.**

    def test_investigate_api_returns_normalized_result(client, runtime_headers):
        response = client.post(
            "/api/v2/runtime/investigate",
            json={
                "semantic_snapshot_id": "snap-valid-001",
                "query": "supplier SUP001",
                "ontology_id": "ontology-supply-001",
                "entity_type": "Supplier",
                "filters": {},
                "limit": 20,
            },
            headers=runtime_headers,
        )
        assert response.status_code == 200
        assert response.json()["decision"] == "ALLOW"
        assert response.json()["semantic_snapshot_id"] == "snap-valid-001"

    def test_investigate_api_denial_does_not_return_protected_rows(client, missing_scope_headers):
        response = client.post("/api/v2/runtime/investigate", json=valid_investigation(), headers=missing_scope_headers)
        assert response.status_code == 403
        assert response.json()["decision"] == "DENY"
        assert response.json()["result"] is None

- [ ] **Step 2: Run the focused API tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_api.py -q)

    Expected: FAIL because /api/v2/runtime is not registered.

- [ ] **Step 3: Implement the thin REST adapter.**

    Use the shared OAuth bearer verification dependency, construct request models without trusting identity fields, call RuntimeService, and map result/error models without adding route-specific policy or query behavior. Register the router under /api/v2/runtime in app/main.py.

- [ ] **Step 4: Run API and service tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_api.py tests/runtime/test_runtime_service.py -q)

    Expected: PASS for valid delegated access, empty ALLOW, structured DENY, no protected-result leakage, immutable plan creation, plan retrieval, and release-only rejection.

- [ ] **Step 5: Commit the REST adapter.**

    git add backend/app/routers/v2/runtime.py backend/app/main.py backend/tests/runtime/test_runtime_api.py

    git commit -m "feat: expose the semantic runtime API"

### Task 17: Publish Python SDK v1 as a transport adapter

- [ ] **Deliverable:** An external Agent can call the Runtime through a typed Python SDK that injects delegated credentials and contains no alternate policy or write implementation.

**Files:**

- Create: sdk/pyproject.toml
- Create: sdk/ontexus_runtime/__init__.py
- Create: sdk/ontexus_runtime/client.py
- Create: sdk/ontexus_runtime/models.py
- Create: sdk/ontexus_runtime/errors.py
- Create: sdk/ontexus_runtime/transport.py
- Create: sdk/tests/test_runtime_client.py

**Interfaces:**

- CredentialProvider is a Protocol with get_delegation() -> str.
- HttpTransport is a Protocol with request(method: str, path: str, json: Mapping[str, object] | None, headers: Mapping[str, str]) -> Mapping[str, object].
- RuntimeClient(base_url: str, credential_provider: CredentialProvider, transport: HttpTransport | None = None) exposes investigate(request: InvestigationRequest) -> InvestigationResult, create_action_plan(request: ActionPlanRequest) -> ActionPlan, get_action_plan(plan_id: str) -> ActionPlan, and get_execution_status(plan_id: str) -> ExecutionStatus.
- RuntimeClient maps structured server failures to RuntimeDeniedError with decision, reason_code, correlation_id, and semantic_snapshot_id; it never evaluates policy or executes a database write locally.
- sdk/pyproject.toml declares the SDK's HTTP/model runtime dependencies so `python -m pip install -e sdk` is the single required package-and-runtime-dependency installation before SDK tests.

- [ ] **Step 1: Write failing SDK tests.**

    def test_sdk_injects_delegation_and_decodes_investigation():
        transport = RecordingTransport({"decision": "ALLOW", "reason_code": "ALLOW",
            "semantic_snapshot_id": "snap-valid-001",
            "ontology_release_id": "release-valid-001",
            "evidence_citations": [], "rule_outcome": [],
            "result": [], "correlation_id": "corr-001"})
        client = RuntimeClient("https://runtime.test", StaticCredential("token-001"), transport)
        result = client.investigate(valid_request())
        assert result.decision == "ALLOW"
        assert transport.last_headers["Authorization"] == "Bearer token-001"

    def test_sdk_preserves_structured_denial():
        client = RuntimeClient("https://runtime.test", StaticCredential("token-001"),
                               RecordingTransport({"decision": "DENY", "reason_code": "SCOPE_DENIED",
                                                   "semantic_snapshot_id": "snap-valid-001",
                                                   "correlation_id": "corr-002"}))
        with pytest.raises(RuntimeDeniedError) as exc:
            client.investigate(valid_request())
        assert exc.value.reason_code == "SCOPE_DENIED"

- [ ] **Step 2: Run the SDK tests to verify they fail.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT" && python -m pip install -e sdk)
    (cd "$REPO_ROOT/sdk" && python -m pytest tests/test_runtime_client.py -q)

    Expected: FAIL because the SDK package, typed models, and credential provider do not exist; the editable install or focused test is the intentional red step before implementation.

- [ ] **Step 3: Implement the minimal SDK.**

    Keep the SDK request/response models structurally identical to backend schemas, use one HTTP transport, add the bearer credential on every request, and preserve server decision/error fields. Do not import backend SQLAlchemy, policy, connector, or writer code into the SDK.

- [ ] **Step 4: Run SDK and API contract tests.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT" && python -m pip install -e sdk)
    (cd "$REPO_ROOT/sdk" && python -m pytest tests/test_runtime_client.py -q)

    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_runtime_api.py -q)

    Expected: PASS for all four methods, delegation injection, typed denial, snapshot citations, and absence of local write/policy behavior.

- [ ] **Step 5: Commit SDK v1.**

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    git -C "$REPO_ROOT" add sdk

    git -C "$REPO_ROOT" commit -m "feat: publish semantic runtime Python SDK"

### Task 18: Route MCP and the built-in Agent through RuntimeService

- [ ] **Deliverable:** MCP and the built-in Agent are reference adapters to the same investigation and plan methods, while compatibility tools do not bypass snapshot or delegated-access checks.

**Files:**

- Modify: backend/app/services/mcp_tools.py
- Modify: backend/app/routers/mcp.py
- Modify: backend/app/deps/oauth.py
- Create: backend/app/services/runtime/reference_agent.py
- Create: backend/tests/runtime/test_mcp_runtime_adapter.py
- Modify: backend/tests/agent/test_mcp_end_to_end.py
- Modify: backend/app/runtime/langgraph_adapter.py

**Interfaces:**

- MCP tool names are runtime_investigate, runtime_create_action_plan, runtime_get_action_plan, and runtime_get_execution_status; each maps to exactly one RuntimeService method.
- call_runtime_tool(db: Session, context: RuntimeContext, name: str, arguments: Mapping[str, object]) -> Mapping[str, object] rejects release-only requests and ignores caller identity IDs.
- ReferenceAgentRuntime.investigate(request: InvestigationRequest, context: RuntimeContext, db: Session) -> InvestigationResult and ReferenceAgentRuntime.create_action_plan(request: ActionPlanRequest, context: RuntimeContext, db: Session) -> ActionPlan call RuntimeService directly.
- Existing ontology_read_instances and ontology_propose_write remain compatibility adapters only; they resolve through the shared service and cannot produce an ungoverned write or treat OAuthContext IDs as authority.

- [ ] **Step 1: Write failing MCP/reference-agent tests.**

    def test_mcp_runtime_investigate_matches_service(db, runtime_context):
        result = call_runtime_tool(db, runtime_context, "runtime_investigate", valid_investigation_dict())
        assert result["decision"] == "ALLOW"
        assert result["semantic_snapshot_id"] == "snap-valid-001"

    def test_mcp_identity_arguments_cannot_impersonate_principal(db, runtime_context):
        result = call_runtime_tool(
            db, runtime_context, "runtime_investigate",
            {**valid_investigation_dict(), "agent_id": "agent-other", "user_id": "user-other"},
        )
        assert result["agent_id"] != "agent-other" or "agent_id" not in result

    def test_legacy_release_only_mcp_request_is_denied(db, runtime_context):
        with pytest.raises(McpToolError) as exc:
            call_runtime_tool(db, runtime_context, "runtime_investigate", {"release_id": "release-valid-001"})
        assert exc.value.code == "SNAPSHOT_NOT_GOVERNED"

- [ ] **Step 2: Run MCP and Agent tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_mcp_runtime_adapter.py tests/agent/test_mcp_end_to_end.py -q)

    Expected: FAIL because MCP currently dispatches directly to ontology_query and the separate write-request flow.

- [ ] **Step 3: Implement adapter-only routing.**

    Extend the MCP OAuth dependency to produce RuntimeContext from the verified delegated credential, add the runtime tools, serialize shared result models, and route the LangGraph/reference Agent through ReferenceAgentRuntime. Keep old tool names only as compatibility shims with the same service and denial semantics.

- [ ] **Step 4: Run adapter and security tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_mcp_runtime_adapter.py tests/agent/test_mcp_end_to_end.py tests/runtime/test_runtime_credentials.py -q)

    Expected: PASS for MCP OAuth delegation, valid investigation/plan calls, identity spoofing rejection, release-only rejection, compatibility reads, and zero direct production writes.

- [ ] **Step 5: Commit the adapter migration.**

    git add backend/app/services/mcp_tools.py backend/app/routers/mcp.py backend/app/deps/oauth.py backend/app/services/runtime/reference_agent.py backend/tests/runtime/test_mcp_runtime_adapter.py backend/tests/agent/test_mcp_end_to_end.py backend/app/runtime/langgraph_adapter.py

    git commit -m "feat: unify MCP and reference Agent runtime access"

### Task 19: Make normalized results and plan hashes transport-independent

- [ ] **Deliverable:** REST and SDK calls produce the same normalized semantic result and byte-identical canonical plan hash when their verified inputs and policy state are equivalent. MCP and the reference built-in Agent — compatibility/reference adapters, not the product's defining layer — produce the same normalized *decision* (decision, reason_code, snapshot pin, evidence) but are not held to byte-identical hash parity in v1 (Milestone 2/3 Scope Amendment, tiered parity).

**Files:**

- Create: backend/app/services/runtime/canonical.py
- Create: backend/tests/runtime/test_transport_parity.py
- Modify: backend/tests/runtime/test_runtime_api.py
- Modify: backend/tests/runtime/test_mcp_runtime_adapter.py
- Modify: sdk/tests/test_runtime_client.py

**Interfaces:**

- normalize_investigation(result: InvestigationResult) -> dict returns decision, reason_code, semantic_snapshot_id, ontology_release_id, evidence_citations, rule_outcome, and result in a stable key/order-independent representation.
- canonical_plan_fields(plan: ActionPlan) -> bytes serializes only semantic plan fields, including snapshot/release pins, verified principals, evidence/rules, binding identity/version, frozen typed parameters, normalized target, before-image/version hashes, risk/policy, preconditions, expiry, and idempotency key.
- compute_plan_hash(plan: ActionPlan) -> str returns the SHA-256 digest of canonical_plan_fields.
- HTTP/MCP envelopes, tracing/request IDs, generated storage IDs, created timestamps, and presentation-only fields are excluded from normalization and hashing.

- [ ] **Step 1: Write failing parity tests.**

    @pytest.mark.parametrize("transport", ["rest", "sdk", "mcp", "reference-agent"])
    def test_equivalent_investigation_has_the_same_normalized_decision(transport):
        result = invoke_fixture_transport(transport, "investigate", "parity-allow-001")
        normalized = normalize_investigation(result)
        expected = expected_normalized("parity-allow-001")
        assert normalized["decision"] == expected["decision"]
        assert normalized["reason_code"] == expected["reason_code"]
        assert normalized["semantic_snapshot_id"] == expected["semantic_snapshot_id"]
        assert normalized["evidence_citations"] == expected["evidence_citations"]

    @pytest.mark.parametrize("transport", ["rest", "sdk"])
    def test_rest_and_sdk_are_byte_identical(transport):
        result = invoke_fixture_transport(transport, "investigate", "parity-allow-001")
        assert normalize_investigation(result) == expected_normalized("parity-allow-001")

    def test_rest_and_sdk_plan_hash_matches_and_changes_on_semantic_drift():
        rest_plan = invoke_fixture_transport("rest", "create_action_plan", "parity-plan-001")
        sdk_plan = invoke_fixture_transport("sdk", "create_action_plan", "parity-plan-001")
        assert compute_plan_hash(rest_plan) == compute_plan_hash(sdk_plan)
        assert compute_plan_hash(change_frozen_target(rest_plan, "target-002")) != compute_plan_hash(rest_plan)

- [ ] **Step 2: Run the parity tests to verify they fail.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_transport_parity.py -q)

    Expected: FAIL because transports currently expose different envelopes, identifiers, release-only inputs, and hash implementations.

- [ ] **Step 3: Implement one canonicalizer and use it in every adapter.**

    Normalize missing optional collections to empty collections, sort evidence and rule records by stable IDs, serialize JSON with sorted keys, compact separators, UTF-8, and SHA-256, and remove only the explicitly non-semantic metadata. Do not normalize away decisions, reason codes, pins, evidence, rule outcomes, target hashes, or frozen parameters. Apply the canonicalizer identically to all four adapters so the normalized *decision* fields always agree; `compute_plan_hash` byte-identity is asserted only between REST and SDK, per the tiered parity in the Milestone 2/3 Scope Amendment — MCP and the reference Agent are not required to use hash-identical serialization internally, only to expose the same normalized decision content.

- [ ] **Step 4: Run all parity tests.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_transport_parity.py tests/runtime/test_runtime_api.py tests/runtime/test_mcp_runtime_adapter.py -q)
    (cd "$REPO_ROOT" && python -m pip install -e sdk)
    (cd "$REPO_ROOT/sdk" && python -m pytest tests/test_runtime_client.py -q)

    Expected: PASS for allow, deny, empty-result, and action-plan cases; all four transports agree on the normalized decision; REST/SDK plan hashes match exactly and change on every semantic field.

- [ ] **Step 5: Commit canonical parity.**

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    git -C "$REPO_ROOT" add backend/app/services/runtime/canonical.py backend/tests/runtime/test_transport_parity.py backend/tests/runtime/test_runtime_api.py backend/tests/runtime/test_mcp_runtime_adapter.py sdk/tests/test_runtime_client.py

    git -C "$REPO_ROOT" commit -m "feat: enforce transport-neutral runtime parity"

### Task 20: Add refresh freshness to snapshots and Runtime policy

- [ ] **Deliverable:** Every new SemanticSnapshot carries source freshness, lag, cursor, and refresh lineage; investigations and action plans make deterministic ALLOW/DENY/HITL decisions from that state, while historical snapshots, plans, and approvals remain unchanged when later refreshes arrive.

**Files:**

- Modify: backend/app/models/semantic_snapshot.py
- Modify: backend/app/schemas/runtime_snapshot.py
- Modify: backend/app/schemas/runtime.py
- Modify: backend/app/services/runtime/lineage.py
- Modify: backend/app/services/runtime/snapshots.py
- Modify: backend/app/services/runtime/policy.py
- Modify: backend/app/services/runtime/service.py
- Modify: backend/app/services/v2/incremental/operations.py
- Create: backend/alembic/versions/0026_snapshot_freshness.py
- Create: backend/tests/runtime/test_snapshot_freshness.py
- Modify: backend/tests/runtime/test_snapshot_materialization.py
- Modify: backend/tests/runtime/test_runtime_policy.py
- Modify: backend/tests/runtime/test_runtime_service.py

**Interfaces:**

- `FreshnessState` is `fresh`, `stale`, or `unknown`. `SemanticSnapshot` adds immutable `freshness_state`, `freshness_lag_seconds`, `source_cursor`, and `lineage_summary` fields alongside its existing release, dataset, pipeline, quality, and evidence pins.
- `FreshnessPolicy` contains `max_lag_seconds`, optional `hard_deny_after_seconds`, and `stale_action` (`deny` or `human_approved`). A policy may make stale evidence require HITL, but it may never make an unknown or ungoverned snapshot silently fresh.
- `FreshnessView` contains state, lag seconds, source cursor, source IDs, dataset-version IDs, pipeline-run IDs, last successful refresh run, and SLA status. `compute_snapshot_freshness(snapshot: SnapshotView, *, now: datetime, policy: FreshnessPolicy) -> FreshnessView` is pure and does not update the snapshot.
- `materialize_refresh_snapshot(db: Session, *, refresh_run_id: str, ontology_release_id: str, dataset_version_ids: Sequence[str], created_by: str) -> SemanticSnapshot` calls the existing governed materialization path, copies the durable cursor/lag/lineage from the successful RefreshRun, and always inserts a new snapshot.
- `evaluate_snapshot_freshness(freshness: FreshnessView, policy: FreshnessPolicy) -> FreshnessDecision` returns `ALLOW`, `HUMAN_APPROVED`, or `DENY` with `reason_code` and `lag_seconds`; `investigate` maps DENY to structured `SNAPSHOT_STALE`/`SNAPSHOT_NOT_GOVERNED`, while `create_action_plan` maps a soft stale result to a plan requiring exact-plan HITL and a hard stale/unknown result to DENY.
- `PolicyDecision` and `InvestigationResult` expose freshness state, lag, source cursor, and lineage citations. The stored plan/approval records capture the freshness view at creation/approval; later data does not recalculate or rewrite historical evidence.

- [ ] **Step 1: Write failing freshness and historical-immutability tests.**

    def test_fresh_snapshot_exposes_cursor_lag_and_lineage(db):
        snapshot = materialize_refresh_fixture(db, refresh_run_id="refresh-success-001")
        view = compute_snapshot_freshness(snapshot, now=FIXED_NOW, policy=FreshnessPolicy(max_lag_seconds=300, stale_action="deny"))
        assert view.state == "fresh"
        assert view.lag_seconds == 120
        assert view.cursor.primary_key == "100"
        assert view.pipeline_run_ids == ["pipeline-run-001"]

    @pytest.mark.parametrize("case_id", ["soft-stale", "hard-stale", "unknown-cursor"])
    def test_stale_policy_is_allow_hitl_or_deny_without_rewriting_snapshot(case_id, db, runtime_context):
        snapshot = materialize_refresh_fixture(db, refresh_run_id=case_id)
        before = snapshot.freshness_lag_seconds
        result = evaluate_fixture_runtime_freshness(case_id, db, runtime_context)
        assert result.reason_code in {"ALLOW", "SNAPSHOT_FRESHNESS_HITL", "SNAPSHOT_STALE"}
        assert snapshot.freshness_lag_seconds == before

    def test_new_refresh_creates_new_snapshot_and_preserves_old_plan_and_approval(db, runtime_context):
        old_snapshot, old_plan, old_approval = create_approved_fixture(db, runtime_context, "refresh-success-001")
        new_snapshot = materialize_refresh_fixture(db, refresh_run_id="refresh-success-002")
        assert new_snapshot.id != old_snapshot.id
        assert get_snapshot(db, old_snapshot.id).source_cursor == old_snapshot.source_cursor
        assert get_action_plan(db, old_plan.id).semantic_snapshot_id == old_snapshot.id
        assert get_approval(db, old_approval.id).plan_hash == old_plan.plan_hash

- [ ] **Step 2: Run freshness tests to verify they fail.**

    Run from the repository root:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_snapshot_freshness.py tests/runtime/test_snapshot_materialization.py tests/runtime/test_runtime_policy.py tests/runtime/test_runtime_service.py -q)

    Expected: FAIL because SemanticSnapshot has no freshness/cursor fields, refresh runs do not materialize new snapshots, and Runtime policy has no stale/lag decision path.

- [ ] **Step 3: Implement immutable freshness propagation and policy.**

    Add the migration after the current runtime-plan migration with `down_revision="0025_runtime_plans"`, calculate lag from the last successful governed RefreshRun/source observation, and persist cursor/lineage only at snapshot creation. Extend the common policy/service contracts so investigations cannot return protected rows from stale/unknown facts, soft-stale action plans carry an explicit HITL requirement, and hard-stale/unknown inputs are denied. Never update an existing snapshot, plan, approval, or evidence record when a later refresh succeeds.

- [ ] **Step 4: Run snapshot, policy, Runtime, and migration checks.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_snapshot_freshness.py tests/runtime/test_snapshot_materialization.py tests/runtime/test_runtime_policy.py tests/runtime/test_runtime_service.py -q)
    (cd "$REPO_ROOT/backend" && python scripts/run_migrations.py upgrade head)
    (cd "$REPO_ROOT" && python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime/generated --check)

    Expected: PASS for fresh/soft-stale/hard-stale/unknown decisions, freshness metadata in Runtime responses, new-snapshot creation, immutable historical plan/approval evidence, and a single Alembic head.

- [ ] **Step 5: Commit Runtime freshness.**

    git add backend/app/models/semantic_snapshot.py backend/app/schemas/runtime_snapshot.py backend/app/schemas/runtime.py backend/app/services/runtime/lineage.py backend/app/services/runtime/snapshots.py backend/app/services/runtime/policy.py backend/app/services/runtime/service.py backend/app/services/v2/incremental/operations.py backend/alembic/versions/0026_snapshot_freshness.py backend/tests/runtime/test_snapshot_freshness.py backend/tests/runtime/test_snapshot_materialization.py backend/tests/runtime/test_runtime_policy.py backend/tests/runtime/test_runtime_service.py
    git commit -m "feat: enforce refresh freshness in runtime decisions"

### Phase 2 release gate

Phase 2 is acceptable only when the following evidence is present in the generated manifest and CI artifacts:

- One source can run as `batch`, `micro_batch`, or bounded `event_driven`, and every run has a durable source/resource cursor, lease/fencing token, idempotency key, frozen `config_version`/cursor contract, at-least-once dedupe result, source provenance, input DatasetVersion, PipelineRun outcome, quality summary, and source lag. A configuration upgrade atomically increments the revision and invalidates the existing source/resource lease/fence. Outcome validation compares the run-frozen revision and cursor contract before lease/owner/fencing validation; a late old-revision worker produces typed `CONFIGURATION_DRIFT` with no DatasetVersion/PipelineRun lineage or cursor progress. Event-driven delivery uses only managed webhook and managed outbox adapters with inbox dedupe keyed by `(source_id, resource, event_id)` and states `received|duplicate|processed|dead_lettered`.
- T+1 schedules persist timezone, business calendar, SLA, retry, and bounded backfill policy; the isolated schedule test proves Celery beat dispatches one due run and does not dispatch it twice.
- The Python/Celery topology registers exactly the named refresh/artifact/agent/housekeeping queues and routes, uses `app.tasks.celery_app`, and has explicit queue-bound worker profiles in both Compose files. Refresh messages contain only durable run IDs, refresh delivery uses late acknowledgement/worker-loss redelivery, and worker concurrency/prefetch/time limits are finite. Graceful interruption leaves a retryable queued run with unchanged source cursor.
- A saturated refresh queue retains a durable `queued`/`backpressured` run, exposes queue depth/oldest age/run lag/retry/DLQ/readiness without payloads or secrets, and does not block a Runtime/API status request or an `agent.interactive` dispatch in the fixture topology. Compose role names without `-Q` queue bindings are not accepted as isolation evidence.
- Polling proves overlap handling for equal timestamps, late/out-of-order data, failure cursor non-advance, retry, DLQ, replay, and pinned latest/approved Pipeline input on both PostgreSQL and MySQL fixtures.
- Event-driven support proves signed managed webhook and managed outbox adapters with inbox-first persistence, event dedupe, source/resource lease/fencing, replay without cursor regression, source configuration revision drift rejection with no lineage/cursor progress, and source lineage; no arbitrary broker consumer or repository-owned CDC producer is required.
- Each successful refresh produces a new SemanticSnapshot with freshness/lag/cursor/lineage; stale/unknown evidence produces deterministic Runtime DENY or exact-plan HITL, and historical snapshots, plans, approvals, and evidence remain byte/state stable.
- Operator API and Playwright evidence can show config version/contract, schedule, cursor, lag/SLA, latest failure, DLQ/replay state, and the resulting snapshot lineage without exposing credentials, raw protected event payloads, or arbitrary source/broker configuration.
- No capacity/extraction-decision evidence is required by this gate; that decision is deferred until real Phase 2 production load exists, per the Milestone 2/3 Scope Amendment (originally Task 10A).

### Milestone 3 — Sandbox and governed PostgreSQL/MySQL writeback

### Task 21: Publish managed action bindings and freeze exact plan targets

- [ ] **Deliverable:** Every writable action has a versioned published binding, and a writable ActionPlan freezes typed parameters, one normalized target, before-image/version hashes, and the binding identity used to execute it.

**Files:**

- Create: backend/app/models/managed_action.py
- Modify: backend/app/models/runtime_plan.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0027_managed_action_bindings.py
- Create: backend/app/services/runtime/action_bindings.py
- Modify: backend/app/services/runtime/service.py
- Modify: backend/app/services/runtime/canonical.py
- Create: backend/tests/runtime/test_managed_action_bindings.py

**Interfaces:**

- ManagedActionBinding contains managed_action_binding_id, action_id, version, status, connection_id, connection_target_identity, dialect in {postgresql, mysql}, schema_name, table_name, primary_key_columns, writable_columns, version_column, parameter_schema, and secret_ref. secret_ref is a vault reference only and never a secret value.
- publish_binding(db, *, action_id: str, connection_id: str, connection_target_identity: str, dialect: str, schema_name: str, table_name: str, primary_key_columns: Sequence[str], writable_columns: Sequence[str], version_column: str, parameter_schema: Mapping[str, object], secret_ref: str) -> ManagedActionBinding validates the allowlist and creates a versioned published binding.
- resolve_published_binding(db, binding_id: str, version: int) -> ManagedActionBinding returns only the same published, non-revoked binding and fixed connection target.
- freeze_action_target(db, *, snapshot: SnapshotView, binding: ManagedActionBinding, parameters: Mapping[str, object], selector: Mapping[str, object]) -> FrozenTarget resolves against the snapshot and returns ordered primary-key tuple, typed parameters, before_image_hash, and version_hash.
- A writable plan must include binding ID/version, frozen parameters, normalized target tuple, before-image/version hashes, and plan_hash. Execution callers cannot supply SQL, identifiers, connection target, transaction options, or a replacement selector.

- [ ] **Step 1: Write failing binding and target-freeze tests.**

    def test_published_binding_freezes_server_owned_target_and_params(db):
        binding = publish_fixture_binding(db, dialect="postgresql")
        frozen = freeze_action_target(
            db, snapshot=valid_snapshot(), binding=binding,
            parameters={"status": "approved"}, selector={"target_id": "target-001"},
        )
        assert frozen.primary_key_tuple == (("target_id", "target-001"),)
        assert frozen.parameters == {"status": "approved"}
        assert frozen.before_image_hash
        assert frozen.version_hash

    def test_binding_draft_revoked_or_connection_drift_is_not_resolvable(db):
        for state in ("draft", "revoked", "connection-drift"):
            with pytest.raises(BindingError) as exc:
                resolve_fixture_binding(db, state)
            assert exc.value.reason_code == "BINDING_DRIFT"

    def test_runtime_cannot_override_frozen_target_or_parameters(db):
        plan = create_writable_fixture_plan(db)
        with pytest.raises(PlanValidationError) as exc:
            validate_execution_overrides(plan, {"target_id": "target-002"}, {"status": "rejected"})
        assert exc.value.reason_code == "INVALID_PLAN_HASH"

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_managed_action_bindings.py -q)

    Expected: FAIL because managed bindings, fixed target resolution, and binding-aware plan fields are absent.

- [ ] **Step 3: Implement binding publication and target freezing.**

    Validate all identifier names against the server-owned binding, resolve the selector against the pinned snapshot exactly once, normalize primary-key values in binding column order, hash the selected before image and version, and store the typed parameter schema. Set migration `0027_managed_action_bindings.py` to `down_revision="0026_snapshot_freshness"`. Extend canonical plan fields so binding identity/version, frozen parameters, target tuple, and both target hashes affect plan_hash.

- [ ] **Step 4: Run binding and parity tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_managed_action_bindings.py tests/runtime/test_transport_parity.py tests/runtime/test_runtime_service.py -q)

    Expected: PASS for published bindings, draft/revoked/connection drift, target and parameter override rejection, stable target hashes, and changed plan hashes.

- [ ] **Step 5: Commit managed bindings.**

    git add backend/app/models/managed_action.py backend/app/models/runtime_plan.py backend/app/models/__init__.py backend/alembic/versions/0027_managed_action_bindings.py backend/app/services/runtime/action_bindings.py backend/app/services/runtime/service.py backend/app/services/runtime/canonical.py backend/tests/runtime/test_managed_action_bindings.py

    git commit -m "feat: freeze managed action targets"

### Task 22: Implement snapshot-backed Sandbox simulation

- [ ] **Deliverable:** Sandbox v1 simulates a published managed action against a pinned SemanticSnapshot and persists an immutable diff without running Agent code, prompts, tools, or production connectors.

**Files:**

- Create: backend/app/models/sandbox.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0028_sandbox.py
- Create: backend/app/services/runtime/sandbox.py
- Create: backend/tests/runtime/test_sandbox.py

**Interfaces:**

- SandboxSimulation contains id, action_plan_id, semantic_snapshot_id, ontology_release_id, agent_id, user_id, managed_action_binding_id, binding_version, expected_rows, before_after_diff, impact_summary, rule_outcome, policy_result, expires_at, precondition_hashes, status, and created_at.
- SandboxResult contains simulation_id, action_plan_id, expected_rows, before_after_diff, impact_summary, rule_outcome, policy_result, precondition_hashes, and expires_at.
- simulate_action(db: Session, *, plan_id: str, context: RuntimeContext) -> SandboxResult reads only snapshot materialization and the published action binding; it never invokes a production connector.
- Sandbox rejects an unpinned snapshot, draft/revoked binding, unsupported action, arbitrary SQL, prompt/tool execution, and production connector access with structured reason codes.

- [ ] **Step 1: Write failing Sandbox tests.**

    def test_simulation_returns_immutable_diff_and_preconditions(db, runtime_context):
        result = simulate_action(db, plan_id="plan-auto-001", context=runtime_context)
        assert result.expected_rows == 1
        assert result.before_after_diff["status"] == {"before": "pending", "after": "approved"}
        assert result.precondition_hashes["version_hash"]

    def test_simulation_never_calls_production_connector(db, runtime_context, production_spy):
        simulate_action(db, plan_id="plan-auto-001", context=runtime_context)
        assert production_spy.calls == []

    @pytest.mark.parametrize("case_id", ["release-only", "draft-binding", "arbitrary-sql", "prompt-tool-call"])
    def test_sandbox_boundary_rejects_out_of_scope_cases(db, runtime_context, case_id):
        with pytest.raises(SandboxError) as exc:
            simulate_fixture(db, runtime_context, case_id)
        assert exc.value.reason_code in {"SNAPSHOT_NOT_GOVERNED", "BINDING_DRIFT", "UNSUPPORTED_ACTION"}

- [ ] **Step 2: Run Sandbox tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_sandbox.py -q)

    Expected: FAIL because the simulation record, snapshot-only evaluator, and diff contract do not exist.

- [ ] **Step 3: Implement the bounded simulation.**

    Load the immutable plan and snapshot, evaluate the published action's typed parameters against snapshot state, calculate expected rows and before/after values, capture rule/policy outcomes and precondition hashes, and persist an append-only result. Set migration `0028_sandbox.py` to `down_revision="0027_managed_action_bindings"`. Do not import or call database writer implementations from this module.

- [ ] **Step 4: Run Sandbox and action-plan tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_sandbox.py tests/runtime/test_managed_action_bindings.py tests/runtime/test_runtime_service.py -q)

    Expected: PASS for normal diff, no-match/zero-impact simulation, immutable result, stale snapshot, draft/revoked binding, and every arbitrary-code/prompt/tool/production-connector boundary.

- [ ] **Step 5: Commit Sandbox v1.**

    git add backend/app/models/sandbox.py backend/app/models/__init__.py backend/alembic/versions/0028_sandbox.py backend/app/services/runtime/sandbox.py backend/tests/runtime/test_sandbox.py

    git commit -m "feat: add snapshot-backed action simulation"

### Task 23: Evaluate risk and enforce exact-plan HITL

- [ ] **Deliverable:** Runtime policy routes low-risk deterministic reversible plans to automatic execution and all other permitted plans to exact-plan HITL, while stale or invalid plans are rejected.

**Files:**

- Create: backend/app/services/runtime/risk.py
- Modify: backend/app/services/actions/approval.py
- Modify: backend/app/routers/agent_approvals.py
- Create: backend/tests/runtime/test_risk_policy.py
- Modify: backend/tests/agent/test_approval_state.py

**Interfaces:**

- ExecutionClass has AUTOMATIC, HUMAN_APPROVED, and REJECTED.
- RiskDecision contains execution_class, allowed, reason_code, risk_factors, and required_plan_hash.
- evaluate_execution_policy(plan: ActionPlan, sandbox: SandboxResult, context: RuntimeContext, now: datetime) -> RiskDecision requires low risk, reversible, deterministic, policy-approved, valid dual-principal access, and unchanged preconditions for AUTOMATIC; high impact, irreversible, sensitive, ambiguous, or threshold-exceeding plans are HUMAN_APPROVED; missing access, stale state, failed Sandbox, unsupported binding, or failed preconditions are REJECTED.
- approve_exact_plan(db: Session, *, plan_id: str, presented_plan_hash: str, approver_context: RuntimeContext, now: datetime) -> ApprovalReceipt accepts one exact unexpired plan hash and never grants ongoing write authority.

- [ ] **Step 1: Write failing risk and approval tests.**

    def test_low_risk_reversible_deterministic_plan_is_automatic():
        decision = evaluate_execution_policy(auto_plan(), auto_sandbox(), valid_runtime_context(), FIXED_NOW)
        assert decision.execution_class == "AUTOMATIC"
        assert decision.allowed

    def test_high_risk_plan_requires_exact_hash_hitl():
        decision = evaluate_execution_policy(high_risk_plan(), high_risk_sandbox(), valid_runtime_context(), FIXED_NOW)
        assert decision.execution_class == "HUMAN_APPROVED"
        assert decision.required_plan_hash == high_risk_plan().plan_hash

    @pytest.mark.parametrize("case_id", ["expired", "stale-snapshot", "policy-drift", "sandbox-failed", "hash-mismatch"])
    def test_invalid_plan_is_rejected(case_id):
        decision = evaluate_fixture_risk(case_id)
        assert decision.execution_class == "REJECTED"

- [ ] **Step 2: Run risk tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_risk_policy.py tests/agent/test_approval_state.py -q)

    Expected: FAIL because risk classes do not share the immutable action-plan hash and the existing approval flow does not enforce exact-plan approval.

- [ ] **Step 3: Implement risk routing and exact approval.**

    Recheck snapshot, identity, policy, Sandbox status, expiry, and precondition hashes at decision time; store the required plan hash on the approval; reject a different hash, expired plan, changed identity, or changed policy. Reuse existing approval/audit conventions without introducing a second policy evaluator.

- [ ] **Step 4: Run risk, Sandbox, and policy tests.**

    Run: (cd backend && python -m pytest tests/runtime/test_risk_policy.py tests/runtime/test_sandbox.py tests/runtime/test_runtime_policy.py tests/agent/test_approval_state.py -q)

    Expected: PASS for automatic, HITL, structured rejection, exact hash mismatch, expiry, identity drift, policy drift, and stale precondition cases.

- [ ] **Step 5: Commit risk routing.**

    git add backend/app/services/runtime/risk.py backend/app/services/actions/approval.py backend/app/routers/agent_approvals.py backend/tests/runtime/test_risk_policy.py backend/tests/agent/test_approval_state.py

    git commit -m "feat: enforce risk-based automatic and HITL routing"

### Task 24: Add the server-side PostgreSQL managed row writer

- [ ] **Deliverable:** PostgreSQL execution performs only one allowlisted parameterized row update with server-owned identifiers, minimum-privilege credentials, transaction timeout, exact row count, and optimistic locking.

**Files:**

- Create: backend/app/services/runtime/writers/base.py
- Create: backend/app/services/runtime/writers/postgres.py
- Create: backend/tests/runtime/test_postgres_writer.py
- Create: backend/tests/runtime/integration/test_postgres_writer_integration.py

**Interfaces:**

- FrozenActionPlan is the writer input containing binding ID/version, connection target identity, schema/table identifiers from the binding, ordered primary-key tuple, frozen typed parameters, before-image hash, version hash, version column, and idempotency key.
- ManagedRowWriter is a Protocol with execute(plan: FrozenActionPlan, *, credential_ref: str) -> WriteReceipt.
- PostgresRowWriter.execute(plan: FrozenActionPlan, *, credential_ref: str) -> WriteReceipt uses only identifiers loaded from the published binding and binds every business value as a parameter.
- WriteReceipt contains dialect, target primary-key tuple, affected_rows, before_image_hash, after_image_hash, version_hash, idempotency_key, status, and correlation_id; it never contains credential_ref or a secret.

- [ ] **Step 1: Write failing PostgreSQL writer tests.**

    def test_postgres_writer_updates_exactly_one_frozen_row(postgres_url, auto_frozen_plan):
        receipt = PostgresRowWriter(postgres_url).execute(auto_frozen_plan, credential_ref="vault:runtime-db")
        assert receipt.affected_rows == 1
        assert read_target_status(postgres_url, "target-001") == "approved"

    @pytest.mark.parametrize("case_id", [
        "arbitrary-sql", "ddl", "multi-target", "delete", "wrong-parameter",
        "target-version-conflict", "row-count-zero", "row-count-two",
    ])
    def test_postgres_writer_rejects_unsafe_or_stale_plan(postgres_url, case_id):
        with pytest.raises(WriterError) as exc:
            execute_postgres_fixture(postgres_url, case_id)
        assert exc.value.reason_code in {"UNSUPPORTED_ACTION", "PRECONDITION_CONFLICT", "ROW_COUNT_MISMATCH"}

- [ ] **Step 2: Run unit and integration tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_postgres_writer.py -q)

    Run with PostgreSQL fixture: (cd backend && RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime python -m pytest tests/runtime/integration/test_postgres_writer_integration.py -q)

    Expected: FAIL because the managed writer and synthetic PostgreSQL fixture are not implemented.

- [ ] **Step 3: Implement the PostgreSQL writer.**

    Construct one parameterized UPDATE from the binding's fixed identifiers, add the normalized primary-key predicates and version precondition, set a dialect-specific statement timeout inside a transaction, require exactly one affected row, compute before/after hashes, and roll back on any failure. Resolve the credential reference server-side; never serialize the credential into a plan, receipt, or audit event. Reject SQL, identifier, connection, transaction-option, target-selector, delete, DDL, and multi-target inputs before opening a transaction.

- [ ] **Step 4: Run PostgreSQL unit and integration tests.**

    Run:

    (cd backend && python -m pytest tests/runtime/test_postgres_writer.py -q)

    docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait postgres

    (cd backend && RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime python -m pytest tests/runtime/integration/test_postgres_writer_integration.py -q)

    Expected: PASS for authorized one-row update, version conflict, exact row count, timeout rollback, idempotency input, unsafe SQL rejection, and secret non-disclosure.

- [ ] **Step 5: Commit PostgreSQL writer.**

    git add backend/app/services/runtime/writers/base.py backend/app/services/runtime/writers/postgres.py backend/tests/runtime/test_postgres_writer.py backend/tests/runtime/integration/test_postgres_writer_integration.py

    git commit -m "feat: add governed PostgreSQL row writer"

### Task 25: Add the server-side MySQL managed row writer

- [ ] **Deliverable:** MySQL has the same managed-binding safety contract and integration coverage as PostgreSQL, with dialect-specific transaction and timeout behavior.

**Files:**

- Create: backend/app/services/runtime/writers/mysql.py
- Create: backend/tests/runtime/test_mysql_writer.py
- Create: backend/tests/runtime/integration/test_mysql_writer_integration.py
- Modify: backend/app/services/runtime/writers/base.py

**Interfaces:**

- MySQLRowWriter implements ManagedRowWriter and the same execute(plan: FrozenActionPlan, *, credential_ref: str) -> WriteReceipt signature.
- MySQLRowWriter uses only published binding identifiers, bound values, one ordered primary-key tuple, and the published version precondition; it returns the same WriteReceipt shape as PostgreSQL.
- The dialect adapter chooses MySQL transaction and statement-timeout syntax internally; callers cannot pass SQL, identifiers, connection targets, transaction options, or a selector.

- [ ] **Step 1: Write failing MySQL tests.**

    def test_mysql_writer_updates_exactly_one_frozen_row(mysql_url, auto_frozen_plan):
        receipt = MySQLRowWriter(mysql_url).execute(auto_frozen_plan, credential_ref="vault:runtime-db")
        assert receipt.affected_rows == 1
        assert read_target_status(mysql_url, "target-001") == "approved"

    @pytest.mark.parametrize("case_id", [
        "binding-drift", "connection-drift", "wrong-parameter",
        "target-version-conflict", "row-count-zero", "row-count-two",
        "arbitrary-sql", "ddl", "multi-target", "delete",
    ])
    def test_mysql_writer_rejects_unsafe_or_stale_plan(mysql_url, case_id):
        with pytest.raises(WriterError) as exc:
            execute_mysql_fixture(mysql_url, case_id)
        assert exc.value.reason_code in {"BINDING_DRIFT", "PRECONDITION_CONFLICT", "ROW_COUNT_MISMATCH", "UNSUPPORTED_ACTION"}

- [ ] **Step 2: Run MySQL unit and integration tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_mysql_writer.py -q)

    Run with MySQL fixture: (cd backend && RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime python -m pytest tests/runtime/integration/test_mysql_writer_integration.py -q)

    Expected: FAIL because the MySQL adapter and fixture are not implemented.

- [ ] **Step 3: Implement the MySQL dialect adapter.**

    Reuse the base writer preflight and parameter contract, generate only the binding-owned update, use MySQL transaction/timeout semantics, enforce one affected row and optimistic locking, and return the common receipt. Keep the connection credential server-side and reject all unsupported operations before a transaction.

- [ ] **Step 4: Run both writer suites.**

    Run:

    docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait mysql

    (cd backend && RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime python -m pytest tests/runtime/test_mysql_writer.py tests/runtime/integration/test_mysql_writer_integration.py -q)

    Expected: PASS for the same normal, edge, negative, security, drift, row-count, lock, timeout, and secret-safety cases as PostgreSQL.

- [ ] **Step 5: Commit MySQL writer.**

    git add backend/app/services/runtime/writers/mysql.py backend/tests/runtime/test_mysql_writer.py backend/tests/runtime/integration/test_mysql_writer_integration.py backend/app/services/runtime/writers/base.py

    git commit -m "feat: add governed MySQL row writer"

### Task 26: Unify automatic/HITL execution, audit, reconciliation, and rollback

- [ ] **Deliverable:** One execution service revalidates every governance boundary immediately before commit, shares the writer between automatic and HITL paths, records receipts/audit/idempotency, creates reconciliation for unknown outcomes, and represents rollback as a new governed plan.

**Files:**

- Create: backend/app/models/runtime_execution.py
- Modify: backend/app/models/__init__.py
- Create: backend/alembic/versions/0029_runtime_execution.py
- Create: backend/app/services/runtime/execution.py
- Modify: backend/app/services/runtime/reconciliation.py
- Modify: backend/app/services/idempotency.py
- Modify: backend/app/models/governance_audit.py
- Create: backend/tests/runtime/test_execution_service.py
- Create: backend/tests/runtime/integration/test_execution_dialects.py

**Interfaces:**

- ExecutionReceipt contains execution_id, plan_id, plan_hash, status, execution_class, dialect, writer_receipt, audit_id, idempotency_key, and reconciliation_case_id.
- ReconciliationCase contains id, execution_id, plan_id, status, unknown_reason, observed_effect, next_action, and created_at; UNKNOWN never transitions to replay without a new governed decision.
- execute_plan(db: Session, *, plan_id: str, presented_plan_hash: str, context: RuntimeContext) -> ExecutionReceipt performs credential, plan hash, expiry, snapshot, policy, Sandbox, binding status/version/connection-target, target before-image/version, idempotency, and execution-fence checks before calling either writer.
- get_execution_status(db: Session, *, plan_id: str, context: RuntimeContext) -> ExecutionReceipt | None returns only authorized status.
- create_rollback_plan(db: Session, *, execution_id: str, context: RuntimeContext) -> ActionPlan uses the retained before image or compensating descriptor and calls normal plan creation, policy, Sandbox, and approval paths; it never writes directly.

- [ ] **Step 1: Write failing execution, audit, reconciliation, and rollback tests.**

    @pytest.mark.parametrize("dialect", ["postgresql", "mysql"])
    def test_automatic_execution_uses_shared_writer_and_audit(dialect, runtime_db):
        receipt = execute_fixture(runtime_db, dialect, "auto-approved")
        assert receipt.status == "SUCCEEDED"
        assert receipt.execution_class == "AUTOMATIC"
        assert receipt.audit_id
        assert receipt.reconciliation_case_id is None

    @pytest.mark.parametrize("case_id", [
        "plan-hash-mismatch", "binding-version-drift", "binding-revoked",
        "connection-target-drift", "snapshot-drift", "policy-drift",
        "before-image-drift", "version-conflict", "row-count-mismatch",
        "caller-parameter-override", "caller-selector-override", "expired-plan",
    ])
    def test_preflight_rejects_before_any_transaction(case_id, runtime_db, transaction_spy):
        with pytest.raises(ExecutionError) as exc:
            execute_fixture(runtime_db, "postgresql", case_id, transaction_spy=transaction_spy)
        assert exc.value.reason_code in {"INVALID_PLAN_HASH", "BINDING_DRIFT", "SNAPSHOT_STALE",
                                         "POLICY_DENIED", "PRECONDITION_CONFLICT", "PLAN_EXPIRED"}
        assert transaction_spy.started is False

    def test_unknown_outcome_creates_reconciliation_and_no_blind_replay(runtime_db):
        receipt = execute_fixture(runtime_db, "postgresql", "unknown-outcome")
        assert receipt.status == "UNKNOWN"
        assert receipt.reconciliation_case_id
        assert retry_unknown(receipt) is False

    def test_rollback_is_a_new_governed_plan(runtime_db, runtime_context):
        receipt = execute_fixture(runtime_db, "postgresql", "auto-approved")
        rollback = create_rollback_plan(runtime_db, execution_id=receipt.execution_id, context=runtime_context)
        assert rollback.id != receipt.plan_id
        assert rollback.semantic_snapshot_id == "snap-valid-001"
        assert rollback.plan_hash

- [ ] **Step 2: Run execution tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_execution_service.py -q)

    Expected: FAIL because execution receipts, preflight fencing, reconciliation, and rollback-plan creation are absent.

- [ ] **Step 3: Implement one governed execution path.**

    Resolve the plan and verify the presented hash, then recheck all credential, identity, policy, snapshot, Sandbox, binding, connection, target, and version conditions before opening a transaction. Set migration `0029_runtime_execution.py` to `down_revision="0028_sandbox"`. Route AUTOMATIC and approved HUMAN_APPROVED plans to the same writer interface. Persist an idempotency record and execution fence before the write, record exact outcomes and non-secret audit evidence, and convert ambiguous timeout/connection outcomes to UNKNOWN reconciliation without blind replay.

- [ ] **Step 4: Run service and both-dialect integration suites.**

    Run:

    (cd backend && python -m pytest tests/runtime/test_execution_service.py tests/runtime/test_risk_policy.py -q)

    docker compose -f test_data/runtime/db/docker-compose.yml up -d --wait

    (cd backend && RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime python -m pytest tests/runtime/integration/test_execution_dialects.py -q)

    Expected: PASS for automatic write, exact-plan HITL, stale/unauthorized/policy/binding/connection/target drift, precondition conflict, row-count mismatch, idempotent retry, unknown reconciliation, receipt/audit, and rollback-plan creation in PostgreSQL and MySQL.

- [ ] **Step 5: Commit governed execution.**

    git add backend/app/models/runtime_execution.py backend/app/models/__init__.py backend/alembic/versions/0029_runtime_execution.py backend/app/services/runtime/execution.py backend/app/services/runtime/reconciliation.py backend/app/services/idempotency.py backend/app/models/governance_audit.py backend/tests/runtime/test_execution_service.py backend/tests/runtime/integration/test_execution_dialects.py

    git commit -m "feat: govern runtime execution and reconciliation"

### Task 26A: Expose governed execution and reconciliation through Runtime REST

- [ ] **Deliverable:** The REST layer exposes every Phase 3 operator operation through RuntimeContext, shared authorization, and structured DENY errors; no route accepts caller-selected credentials, targets, SQL, or parameters.

**Files:**

- Modify: backend/app/routers/v2/runtime.py
- Modify: backend/app/main.py
- Create: backend/tests/runtime/test_runtime_execution_api.py
- Modify: backend/tests/runtime/test_runtime_api.py

**Interfaces:**

- GET /api/v2/runtime/action-plans/{plan_id}/sandbox requires a verified RuntimeContext and returns SandboxResult after shared authorization and snapshot/binding checks.
- POST /api/v2/runtime/action-plans/{plan_id}/approve accepts only {"plan_hash": string}, requires an approver RuntimeContext, and calls approve_exact_plan.
- POST /api/v2/runtime/action-plans/{plan_id}/execute accepts only {"plan_hash": string}, requires a RuntimeContext, and calls execute_plan; it never accepts parameters, selector, SQL, identifiers, connection target, or transaction options.
- GET /api/v2/runtime/reconciliations/{reconciliation_id} requires a verified RuntimeContext and returns ReconciliationCase with no secret or protected row content.
- POST /api/v2/runtime/executions/{execution_id}/rollback-plans requires a verified RuntimeContext and calls create_rollback_plan, returning a new immutable ActionPlan.
- Define `RuntimeContext` as the dependency result `get_runtime_context(request: Request, credential: VerifiedCredential = Depends(require_verified_credential)) -> RuntimeContext`, containing verified `agent_id`, `user_id`, tenant/security domain, scopes, credential ID, and correlation ID; no identity field is read from the request body.
- Define the shared denial response as `StructuredDeny(decision="DENY", reason_code: str, correlation_id: str, semantic_snapshot_id: str | None, result: None)` and have one exception mapper return it for HTTP 403/409 without exposing credentials, SQL, or protected row data.
- Every endpoint uses the shared RuntimeContext dependency, evaluates Agent capability ∩ user entitlement ∩ runtime policy, and maps access, snapshot, policy, binding, hash, expiry, and precondition failures to decision DENY with reason_code, correlation_id, and semantic_snapshot_id. Body agent_id and user_id are ignored or rejected.
- app/main.py registers the existing v2 runtime router once; no endpoint creates a second policy or writer path.

- [ ] **Step 1: Write failing API tests for every Phase 3 endpoint and denial shape.**

    def test_sandbox_query_requires_verified_context_and_returns_diff(client, runtime_headers):
        response = client.get(
            "/api/v2/runtime/action-plans/plan-auto-001/sandbox",
            headers=runtime_headers,
        )
        assert response.status_code == 200
        assert response.json()["action_plan_id"] == "plan-auto-001"
        assert response.json()["semantic_snapshot_id"] == "snap-valid-001"

    def test_approve_execute_and_rollback_accept_only_exact_hash_or_execution_id(client, runtime_headers):
        approve = client.post(
            "/api/v2/runtime/action-plans/plan-hitl-001/approve",
            json={"plan_hash": "plan-hash-hitl-001"},
            headers=runtime_headers,
        )
        execute = client.post(
            "/api/v2/runtime/action-plans/plan-hitl-001/execute",
            json={"plan_hash": "plan-hash-hitl-001"},
            headers=runtime_headers,
        )
        rollback = client.post(
            "/api/v2/runtime/executions/execution-001/rollback-plans",
            json={},
            headers=runtime_headers,
        )
        assert approve.status_code == 200
        assert execute.status_code == 200
        assert rollback.status_code == 201

    def test_reconciliation_query_and_denial_are_structured(client, runtime_headers, missing_scope_headers):
        allowed = client.get(
            "/api/v2/runtime/reconciliations/recon-001",
            headers=runtime_headers,
        )
        denied = client.post(
            "/api/v2/runtime/action-plans/plan-hitl-001/execute",
            json={"plan_hash": "wrong"},
            headers=missing_scope_headers,
        )
        assert allowed.status_code == 200
        assert allowed.json()["status"] == "UNKNOWN"
        assert denied.status_code == 403
        assert denied.json()["decision"] == "DENY"
        assert denied.json()["reason_code"] in {"SCOPE_DENIED", "INVALID_PLAN_HASH"}
        assert denied.json().get("result") is None

- [ ] **Step 2: Run the focused API tests to verify they fail.**

    Run: (cd backend && python -m pytest tests/runtime/test_runtime_execution_api.py tests/runtime/test_runtime_api.py -q)

    Expected: FAIL because the runtime router has no Sandbox, approval, execution, reconciliation, or rollback-plan routes.

- [ ] **Step 3: Implement the thin Phase 3 REST extension.**

    Add the five routes to backend/app/routers/v2/runtime.py, inject `get_runtime_context(request, credential=Depends(require_verified_credential))` through the same verified-credential dependency used by investigation and action-plan creation, call SandboxService, approve_exact_plan, execute_plan, get_reconciliation, and create_rollback_plan respectively, and map every typed exception through one exception handler to the `StructuredDeny` schema. Ensure app/main.py has exactly one registration of the v2 runtime router; if Task 16 already registered it, add an assertion/test rather than a duplicate.

- [ ] **Step 4: Run API, authorization, and execution tests.**

    Run:

    (cd backend && python -m pytest tests/runtime/test_runtime_execution_api.py tests/runtime/test_runtime_api.py tests/runtime/test_runtime_credentials.py tests/runtime/test_execution_service.py -q)

    Expected: PASS for all five endpoints, valid dual-principal context, missing/expired/revoked/cross-domain denial, Agent or user capability denial, exact hash mismatch, stale snapshot, binding drift, no secret leakage, and no caller-supplied target or parameter override.

- [ ] **Step 5: Commit the governed REST extension.**

    git add backend/app/routers/v2/runtime.py backend/app/main.py backend/tests/runtime/test_runtime_execution_api.py backend/tests/runtime/test_runtime_api.py

    git commit -m "feat: expose governed execution REST endpoints"

### Task 27: Add refresh and Runtime operator surfaces with Playwright governance flows

- [ ] **Deliverable:** The built-in UI exposes refresh schedule/cursor/lag/run/DLQ-replay status plus investigation lineage/evidence, Sandbox diff, risk decision, exact-plan approval, receipt, reconciliation, and rollback-plan state as operator surfaces, without becoming a general Agent Builder.

**Files:**

- Create: frontend/src/api/runtime.ts
- Create: frontend/src/api/refresh.ts
- Create: frontend/src/types/runtime.ts
- Create: frontend/src/types/refresh.ts
- Create: frontend/src/pages/runtime/RuntimeInvestigationPage.tsx
- Create: frontend/src/pages/runtime/ActionPlanPage.tsx
- Create: frontend/src/pages/runtime/SandboxPage.tsx
- Create: frontend/src/pages/runtime/ApprovalQueuePage.tsx
- Create: frontend/src/pages/runtime/ReconciliationPage.tsx
- Create: frontend/src/pages/runtime/RefreshOperationsPage.tsx
- Modify: frontend/src/App.tsx
- Create: frontend/src/api/runtime.test.ts
- Create: frontend/src/api/refresh.test.ts
- Create: frontend/src/pages/runtime/RuntimeInvestigationPage.test.tsx
- Create: frontend/src/pages/runtime/ActionPlanPage.test.tsx
- Create: frontend/src/pages/runtime/RefreshOperationsPage.test.tsx
- Create: frontend/src/test/e2e/runtime-governance.spec.ts
- Create: frontend/src/test/e2e/helpers/runtime.ts

**Interfaces:**

- runtimeApi.investigate(request: InvestigationRequest) -> InvestigationResult maps to POST /api/v2/runtime/investigate.
- runtimeApi.getActionPlan(planId: string) -> ActionPlan maps to GET /api/v2/runtime/action-plans/{planId}.
- runtimeApi.getSandboxSimulation(planId: string) -> SandboxResult maps to GET /api/v2/runtime/action-plans/{planId}/sandbox.
- runtimeApi.approveActionPlan(planId: string, planHash: string) -> ApprovalReceipt maps to POST /api/v2/runtime/action-plans/{planId}/approve with body {"plan_hash": planHash}.
- runtimeApi.executeActionPlan(planId: string, planHash: string) -> ExecutionReceipt maps to POST /api/v2/runtime/action-plans/{planId}/execute with body {"plan_hash": planHash}; it sends no parameters or selector.
- runtimeApi.getExecutionStatus(planId: string) -> ExecutionReceipt maps to GET /api/v2/runtime/execution-status/{planId}.
- runtimeApi.getReconciliation(reconciliationId: string) -> ReconciliationCase maps to GET /api/v2/runtime/reconciliations/{reconciliationId}.
- runtimeApi.createRollbackPlan(executionId: string) -> ActionPlan maps to POST /api/v2/runtime/executions/{executionId}/rollback-plans with an empty body.
- refreshApi.getStatus(sourceId: string) -> RefreshStatus maps to GET /api/v2/refresh/sources/{sourceId}/status.
- refreshApi.setSchedule(sourceId: string, request: ScheduleRequest) -> RefreshSchedule maps to PUT /api/v2/refresh/sources/{sourceId}/schedule.
- refreshApi.trigger(sourceId: string, request: RefreshTriggerRequest) -> RefreshRun maps to POST /api/v2/refresh/sources/{sourceId}/run; it sends only the persisted policy/mode and bounded backfill window.
- refreshApi.cancel(runId: string, reason: string) -> RefreshRun maps to POST /api/v2/refresh/runs/{runId}/cancel with body {"reason": reason}; the operator identity comes from the authenticated session and the client sends no lease/fence/cursor/source/payload.
- refreshApi.replay(runId: string, deadLetterId?: string) -> RefreshRun maps to POST /api/v2/refresh/runs/{runId}/replay; it sends no cursor, source URL, credential, broker, or event payload.
- Every method uses the existing authenticated API client and maps only to its listed endpoint; client code does not evaluate authorization, risk, hashes, or writes.
- The UI displays refresh policy, source config version/cursor contract, schedule timezone/business calendar, next due time, cursor/watermark, source lag/SLA, latest run status, retry count, DLQ/replay state, cancellation requested actor/time/reason, and terminal `cancelled` state, snapshot ID, release ID, evidence citations, rule outcome, decision/reason code, before/after diff, impact, risk class, exact plan hash, receipt, and reconciliation state. It shows a cancel control only for an authorized active run, disables it after any durable terminal state, and for a cancellation request that arrives after the run is already terminal shows a plain "run already finished; cancellation had no effect" message — never a distinct error state and never rollback language. It distinguishes DENY from ALLOW with no matching data, and renders `CONFIGURATION_DRIFT` as a failed run requiring a new claim, never as a successful refresh.
- Protected routes are added under /runtime/refresh/:sourceId, /runtime/investigate, /runtime/action-plans/:planId, /runtime/sandbox/:planId, /runtime/approvals, and /runtime/reconciliation/:planId.
- Playwright uses test_data/runtime/playwright_seed.json and existing authenticated helpers; it never connects to a production system.

- [ ] **Step 1: Write failing component and browser tests.**

    it('maps every governed runtime operation to its REST endpoint', async () => {
      const get = vi.spyOn(apiClientV2, 'get').mockResolvedValue({ data: {} })
      const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({ data: {} })
      await runtimeApi.getSandboxSimulation('plan-auto-001')
      await runtimeApi.approveActionPlan('plan-hitl-001', 'plan-hash-hitl-001')
      await runtimeApi.executeActionPlan('plan-hitl-001', 'plan-hash-hitl-001')
      await runtimeApi.getReconciliation('recon-001')
      await runtimeApi.createRollbackPlan('execution-001')
      expect(get).toHaveBeenCalledWith('/runtime/action-plans/plan-auto-001/sandbox')
      expect(get).toHaveBeenCalledWith('/runtime/reconciliations/recon-001')
      expect(post).toHaveBeenCalledWith('/runtime/action-plans/plan-hitl-001/approve', { plan_hash: 'plan-hash-hitl-001' })
      expect(post).toHaveBeenCalledWith('/runtime/action-plans/plan-hitl-001/execute', { plan_hash: 'plan-hash-hitl-001' })
      expect(post).toHaveBeenCalledWith('/runtime/executions/execution-001/rollback-plans', {})
    })

    it('maps refresh status, schedule, trigger, cancellation, and replay without accepting cursor or broker data', async () => {
      const get = vi.spyOn(apiClientV2, 'get').mockResolvedValue({ data: {} })
      const put = vi.spyOn(apiClientV2, 'put').mockResolvedValue({ data: {} })
      const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({ data: {} })
      await refreshApi.getStatus('source-001')
      await refreshApi.setSchedule('source-001', { cron_expr: '0 2 * * *', timezone: 'Asia/Shanghai', enabled: true })
      await refreshApi.trigger('source-001', { mode: 'micro_batch' })
      await refreshApi.cancel('run-running-001', 'planned source maintenance')
      await refreshApi.replay('run-dead-001', 'dlq-001')
      expect(get).toHaveBeenCalledWith('/refresh/sources/source-001/status')
      expect(put).toHaveBeenCalledWith('/refresh/sources/source-001/schedule', { cron_expr: '0 2 * * *', timezone: 'Asia/Shanghai', enabled: true })
      expect(post).toHaveBeenCalledWith('/refresh/sources/source-001/run', { mode: 'micro_batch' })
      expect(post).toHaveBeenCalledWith('/refresh/runs/run-running-001/cancel', { reason: 'planned source maintenance' })
      expect(post).toHaveBeenCalledWith('/refresh/runs/run-dead-001/replay', { dead_letter_id: 'dlq-001' })
    })

    it('shows snapshot evidence and distinguishes denied from empty allowed results', async () => {
      render(RuntimeInvestigationPage)
      expect(await screen.findByTestId('semantic-snapshot-id')).toHaveTextContent('snap-valid-001')
      expect(screen.getByTestId('investigation-decision')).toHaveTextContent('ALLOW')
    })

    test('operator approves the exact plan hash and sees the receipt', async ({ page }) => {
      await seedRuntimePage(page, 'hitl-approved')
      await page.goto('/runtime/action-plans/plan-hitl-001')
      await expect(page.getByTestId('plan-hash')).toHaveText('plan-hash-hitl-001')
      await page.getByTestId('approve-exact-plan').click()
      await expect(page.getByTestId('execution-status')).toHaveText('SUCCEEDED')
    })

    test('operator queries reconciliation and creates a governed rollback plan', async ({ page }) => {
      await seedRuntimePage(page, 'unknown-outcome')
      await page.goto('/runtime/reconciliation/recon-001')
      await expect(page.getByTestId('reconciliation-status')).toHaveText('UNKNOWN')
      await page.getByTestId('create-rollback-plan').click()
      await expect(page.getByTestId('rollback-plan-hash')).toBeVisible()
      await expect(page.getByTestId('rollback-execution-state')).toHaveText('PROPOSAL_ONLY')
    })

    test('operator sees T+1 schedule, cursor/lag, and failed replay status', async ({ page }) => {
      await seedRuntimePage(page, 'refresh-dead-lettered')
      await page.goto('/runtime/refresh/source-001')
      await expect(page.getByTestId('refresh-policy')).toHaveText('micro_batch')
      await expect(page.getByTestId('refresh-cursor')).toHaveText('100')
      await expect(page.getByTestId('refresh-lag-seconds')).toHaveText('120')
      await expect(page.getByTestId('refresh-latest-status')).toHaveText('DEAD_LETTERED')
      await page.getByTestId('replay-refresh-run').click()
      await expect(page.getByTestId('refresh-replay-status')).toHaveText('QUEUED')
    })

    test('operator sees configuration drift as failed with unchanged progress', async ({ page }) => {
      await seedRuntimePage(page, 'config-drift-late-finish')
      await page.goto('/runtime/refresh/source-001')
      await expect(page.getByTestId('refresh-config-version')).toHaveText('8')
      await expect(page.getByTestId('refresh-latest-status')).toHaveText('FAILED')
      await expect(page.getByTestId('refresh-latest-reason')).toHaveText('CONFIGURATION_DRIFT')
      await expect(page.getByTestId('refresh-cursor')).toHaveText('100')
    })

    test('operator cancels an in-flight refresh and sees unchanged progress', async ({ page }) => {
      await seedRuntimePage(page, 'cancel-inflight-page')
      await page.goto('/runtime/refresh/source-001')
      await expect(page.getByTestId('refresh-latest-status')).toHaveText('CANCEL_REQUESTED')
      await page.getByTestId('cancel-refresh-run').click()
      await expect(page.getByTestId('refresh-latest-status')).toHaveText('CANCELLED')
      await expect(page.getByTestId('refresh-cancel-reason')).toHaveText('planned source maintenance')
      await expect(page.getByTestId('refresh-cursor')).toHaveText('100')
    })

    test('operator sees a cancellation request after the run already finished as already-terminal', async ({ page }) => {
        await seedRuntimePage(page, 'refresh-succeeded')
        await page.goto('/runtime/refresh/source-001')
        await expect(page.getByTestId('refresh-latest-status')).toHaveText('SUCCEEDED')
        await page.getByTestId('cancel-refresh-run').click()
        await expect(page.getByTestId('refresh-cancel-message')).toHaveText('run already finished; cancellation had no effect')
        await expect(page.getByTestId('refresh-latest-status')).toHaveText('SUCCEEDED')
        await expect(page.getByTestId('refresh-snapshot-id')).toHaveText('snap-refresh-committed-001')
    })

- [ ] **Step 2: Run frontend tests to verify they fail.**

    Run: (cd frontend && npm run test:unit -- src/api/runtime.test.ts src/api/refresh.test.ts src/pages/runtime/RuntimeInvestigationPage.test.tsx src/pages/runtime/ActionPlanPage.test.tsx src/pages/runtime/RefreshOperationsPage.test.tsx)

    Run: (cd frontend && npx playwright test src/test/e2e/runtime-governance.spec.ts)

    Expected: FAIL because the runtime/refresh API methods, pages, routes, and browser fixtures do not exist.

- [ ] **Step 3: Implement the operator surface.**

    Implement every runtimeApi and refreshApi method with the exact path/body mapping above using the existing API client, auth store, Layout, protected routes, i18n, and Playwright helpers. Add route handlers and UI controls for schedule update, refresh trigger, cancel request with reason, cancellation requested/cancelled/already-terminal display, lag/cursor/status display, bounded DLQ replay, Sandbox query, exact-hash approval, exact-hash execution, reconciliation query, and rollback-plan creation. Render all evidence and governance states from server responses, keep identity and policy decisions server-owned, and provide only operator review/approval/reconciliation controls; the UI never constructs a cursor, SQL, broker, credential, or write target.

- [ ] **Step 4: Run component, build, and Playwright tests.**

    Run:

    (cd frontend && npm run test:unit -- src/api/runtime.test.ts src/api/refresh.test.ts src/pages/runtime/RuntimeInvestigationPage.test.tsx src/pages/runtime/ActionPlanPage.test.tsx src/pages/runtime/RefreshOperationsPage.test.tsx)

    (cd frontend && npm run build)

    (cd frontend && npx playwright test src/test/e2e/runtime-governance.spec.ts)

    Expected: PASS for every refresh/runtime endpoint mapping, authenticated cancellation mapping, T+1 schedule display, source config version/contract, cursor/lag display, cancel-before/inflight/after-commit operator states with unchanged progress or the plain already-terminal result, configuration-drift failed status with unchanged cursor, failed/DLQ replay state, normal investigation, allowed empty result, denied result, Sandbox diff, automatic state, exact-plan HITL, stale rejection, receipt, reconciliation query, and rollback-plan display.

- [ ] **Step 5: Commit operator surfaces.**

    git add frontend/src/api/runtime.ts frontend/src/api/refresh.ts frontend/src/types/runtime.ts frontend/src/types/refresh.ts frontend/src/pages/runtime frontend/src/App.tsx frontend/src/test/e2e/runtime-governance.spec.ts frontend/src/test/e2e/helpers/runtime.ts

    git commit -m "feat: add refresh and runtime governance operator surfaces"

### Task 28: Run the integrated refresh, Runtime, and governed-execution release gates

- [ ] **Deliverable:** M1, Phase 2 refresh/lineage, and Phase 3 acceptance evidence is executable in CI and locally, with batch/micro-batch/event-driven refresh, both database dialects, all four Runtime transports, operator E2E coverage, and source-only webhook/outbox evidence using durable cursors, inbox dedupe, lease/fencing, and replay safety.

**Files:**

- Create: backend/tests/runtime/test_acceptance_matrix.py
- Modify: backend/tests/agent/test_celery_topology.py
- Modify: backend/tests/v2/incremental/test_refresh_task_dispatch.py
- Modify: backend/tests/v2/incremental/test_refresh_operability.py
- Modify: .github/workflows/agent-mvp.yml
- Create: backend/tests/runtime/run_registered_cases.py
- Create: backend/tests/runtime/test_registered_case_execution.py
- Modify: test_data/runtime/README.md
- Modify: test_data/runtime/db/docker-compose.yml
- Modify: README.md
- Modify: README_zh.md

**Interfaces:**

- `test_acceptance_matrix.py` loads every case from `test_data/runtime/manifest.json` and its registered `test_targets` from `test_data/runtime/registry.py`; it never calls an untyped `run_case` function. The single runner owned by this task, `backend/tests/runtime/run_registered_cases.py`, exposes `run_registered_case(case: Mapping[str, object]) -> CaseExecutionResult`, `run_all_registered_cases(manifest_path: Path, *, report_path: Path) -> tuple[CaseExecutionResult, ...]`, `load_case_execution_report(path: Path) -> CaseExecutionReport`, and `assert_deterministic_report(report: CaseExecutionReport) -> None`. Its CLI is `python -m tests.runtime.run_registered_cases --manifest <manifest> --report <report>`; it selects only cases whose `execution_mode == "deterministic"`, executes each single target exactly once, writes the sanitized report, and fails on a missing/duplicate target, non-zero result, or skip. It makes zero model calls. Collect/list checks are supplemental and never acceptance evidence; no other task creates a registry runner.
- `assert_case_registry(case: Mapping[str, object]) -> None` requires case_id, expected, layers, execution_mode in `{deterministic, real_model_browser}`, exactly one-element plural `test_targets`, and coverage, checks the manifest/registry bidirectional mapping and target descriptor syntax, and requires deterministic cases to use one pytest target while the three `real_model_browser` normal journey cases use one exact Playwright target. It also requires both postgresql and mysql for database layers, all four rest/sdk/mcp/reference-agent transports for parity layers, `refresh_mode`/`source_contract`/`cursor_outcome` for refresh layers, and `cancel_outcome` in `{requested, cancelled, already_terminal}` with unchanged cursor/lineage metadata for cancellation cases. Refresh contracts are limited to `watermark_primary_key` and `opaque_source_cursor`; the `config-drift-late-finish` case carries `error_code == CONFIGURATION_DRIFT` plus unchanged cursor and lineage outcomes. Journey cases additionally carry `journey_id`, `fixture_manifest_sha256`, `model_id == deepseek-v4-flash-vision-exp`, `risk_class`, and `skip_allowed == false`; every E2E layer requires a Playwright target descriptor. Task 28 executes every deterministic registered target and asserts a passed, non-skipped result; it never executes the three real-model browser cases. Final Task 33 repeats the deterministic command before preparing those three separate browser journeys.
- The CI workflow has dedicated steps for fixture generation/check, refresh contract/schedule/polling/event/API tests, backend Runtime unit tests, PostgreSQL/MySQL refresh and writeback integration, SDK tests, frontend test:ci, refresh/runtime Playwright governance E2E, and Compose validation; backend Python 3.11/3.12 coverage remains.
- The CI workflow also runs the named queue/route registration, run-ID-only dispatch, graceful interruption, operability/readiness, queue-saturation-isolation, and durable cancellation tests. It validates both application Compose files' queue-bound worker profiles and `app.tasks.celery_app` entry point without requiring an external CDC producer/broker. It calls the same `scripts/verify_refresh_checkpoint.sh` Task 10 defines and runs locally — the CI job does not duplicate its `--profile refresh` startup, seeding, or polling logic.
- The CI SDK job installs the package and its declared runtime dependencies with `(cd "$REPO_ROOT" && python -m pip install -e sdk)` before running `(cd "$REPO_ROOT/sdk" && python -m pytest tests -q)`; the local release gate uses the same root-anchored install and test commands.
- Release evidence records the exact fixture manifest hash, Alembic head, service health, normalized parity results, database receipts, cancellation state-transition and unchanged-cursor assertions, the plain `already_terminal` result for a cancellation request after any terminal state, reconciliation IDs, the allowlist/forbidden-value artifact scan result, and the registry-driven case execution result. (Capacity evidence is not part of this gate — Task 10A is deferred per the Milestone 2/3 Scope Amendment.)

- [ ] **Step 1: Write the failing acceptance matrix.**

    @pytest.mark.parametrize("case", load_cases("test_data/runtime/manifest.json"))
    def test_design_case_has_exactly_one_executable_registered_target(case):
        assert_case_registry(case)
        targets = targets_for(case["case_id"])
        assert len(targets) == 1
        assert [target.to_dict() for target in targets] == case["test_targets"]
        if case["execution_mode"] == "deterministic":
            assert target_is_executable(targets[0])
        else:
            assert case["execution_mode"] == "real_model_browser"
            assert targets[0].kind == "playwright"

    def test_case_layers_require_their_test_dimensions():
        for case in load_cases("test_data/runtime/manifest.json"):
            assert case["layers"]
            if "database" in case["layers"]:
                assert set(case["dialects"]) == {"mysql", "postgresql"}
            if "parity" in case["layers"]:
                assert set(case["transports"]) == {"mcp", "reference-agent", "rest", "sdk"}
            if "refresh" in case["layers"]:
                assert case["refresh_mode"] in {"batch", "micro_batch", "event_driven"}
                assert case["source_contract"] in {"watermark_primary_key", "opaque_source_cursor"}
                assert case["cursor_outcome"] in {"advanced", "unchanged", "dead_lettered"}
                if case["case_id"].startswith("cancel-"):
                    assert case["cancel_outcome"] in {"requested", "cancelled", "already_terminal"}
                    assert case["cursor_outcome"] == "unchanged" or case["cancel_outcome"] == "already_terminal"
                    if case["cancel_outcome"] == "already_terminal":
                        assert case["already_terminal"] is True
            if "playwright" in case["layers"]:
                assert case["test_targets"][0]["kind"] == "playwright"

    def test_runtime_corpus_has_no_pii_or_real_secret():
        test_no_pii_or_real_secret()

    def test_both_dialects_are_required():
        assert {"postgresql", "mysql"} <= set(load_manifest()["dialects"])

    def test_all_supported_refresh_modes_and_cursor_contracts_are_represented():
        refresh_cases = [case for case in load_cases("test_data/runtime/manifest.json") if "refresh" in case["layers"]]
        assert {case["refresh_mode"] for case in refresh_cases} == {"batch", "micro_batch", "event_driven"}
        assert {case["source_contract"] for case in refresh_cases} >= {"watermark_primary_key", "opaque_source_cursor"}

    def test_configuration_drift_fixture_requires_fail_closed_progress_evidence():
        case = next(case for case in load_cases("test_data/runtime/manifest.json") if case["case_id"] == "config-drift-late-finish")
        assert case["error_code"] == "CONFIGURATION_DRIFT"
        assert case["cursor_outcome"] == "unchanged"
        assert case["lineage_outcome"] == "unchanged"

    def test_registry_runner_executes_every_deterministic_case_once(tmp_path):
        results = run_all_registered_cases(
            Path("test_data/runtime/manifest.json"),
            report_path=tmp_path / "deterministic-cases.json",
        )
        expected_ids = {
            case["case_id"] for case in load_cases("test_data/runtime/manifest.json")
            if case["execution_mode"] == "deterministic"
        }
        assert {result.case_id for result in results} == expected_ids
        assert all(result.status == "passed" and not result.skipped for result in results)
        assert_deterministic_report(load_case_execution_report(tmp_path / "deterministic-cases.json"))

    def test_python_execution_topology_is_part_of_phase_two_gate():
        assert set(ALL_QUEUE_NAMES) == {
            "refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay",
            "artifact.extraction", "agent.interactive", "housekeeping",
        }
        assert TASK_ROUTES["refresh.poll"]["queue"] == "refresh.poll"
        assert TASK_ROUTES["refresh.event"]["queue"] == "refresh.event"

- [ ] **Step 2: Run the matrix before final wiring.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_acceptance_matrix.py -q)

    Expected: FAIL for any design case without exactly one executable registry target, a required execution mode, dialect/transport/refresh/Playwright marker, an expected assertion, a generated fixture, the configuration-drift or cancellation cursor/lineage invariant, or the named Python/Celery topology evidence. The runner must execute every deterministic case; the three `real_model_browser` normal journey cases are excluded here and run only by the ordered Task 33 browser gate. Listability alone is insufficient.

- [ ] **Step 3: Wire final checks and document reproducible commands.**

    Add fixture generation verification, test_no_pii_or_real_secret(), the
    registry-driven execution of every deterministic case, refresh
    contract/schedule/polling/event/API/cancellation tests, queue/route/
    operability/interruption tests, Runtime unit/integration/parity tests, SDK
    tests, frontend test:ci, refresh/runtime Playwright, and fresh-database
    Compose checks to the existing workflow. Keep frontend outside the Python
    matrix, retain backend 3.11/3.12, and document the exact local commands,
    synthetic database URLs, queue-bound worker profiles, and the managed
    webhook/outbox prerequisites. The final business-journey job executes its
    commands strictly in this order before any artifact upload:

    ```bash
    python -m tests.runtime.run_registered_cases --manifest test_data/runtime/manifest.json --report artifacts/runtime/deterministic-cases.json
    python -m evals.business_journeys.run --phase prepare --journey all --model-id deepseek-v4-flash-vision-exp --api-base "$BUSINESS_JOURNEY_API_BASE" --output artifacts/evals/business_journeys --run-id "$RUN_ID"
    npx playwright test --config frontend/playwright.config.ts frontend/src/test/e2e/business-journeys.spec.ts
    python -m evals.business_journeys.run --phase verify --journey all --api-base "$BUSINESS_JOURNEY_API_BASE" --output artifacts/evals/business_journeys --run-id "$RUN_ID"
    # scan the allowlisted report and sanitized browser artifacts, then upload them
    ```

    The first command must pass with zero model calls before preparation is
    allowed to run. Preparation must pass all three journeys before
    Playwright starts. The verification command runs only after all three
    exact browser titles finish; it reads persisted trace/audit/receipt/budget
    evidence and makes zero model calls. Only after verification passes may
    the scanner/report/upload phase run. The SDK CI job must install `-e sdk`
    (including the runtime dependencies declared by sdk/pyproject.toml) before
    collecting/running its tests. Its commands are:

    REPO_ROOT="${GITHUB_WORKSPACE:-$(git rev-parse --show-toplevel)}"
    (cd "$REPO_ROOT" && python -m pip install -e sdk)
    (cd "$REPO_ROOT/sdk" && python -m pytest tests -q)

    The acceptance matrix must fail closed if a manifest case is removed, lacks an assertion, execution_mode, or plural `test_targets` list with exactly one entry, loses a required dialect/transport/refresh mode, omits the configuration-drift case or its `CONFIGURATION_DRIFT`/unchanged cursor/lineage metadata, omits cancellation cases or their `cancel_outcome`/unchanged cursor metadata, fails to represent the ordinary `already_terminal` result, points a deterministic case to a target that cannot be executed or is skipped, or claims an unsupported event source. The registry runner must report one passed result for every `execution_mode == "deterministic"` case, with `missing == []`, `duplicates == []`, and `skipped == 0`, before any real-model request; the three `real_model_browser` normal journey cases are not deterministic registry cases or deterministic runner targets and are run exactly once by the three browser journeys in Task 33. Collect/list output alone is not evidence.

- [ ] **Step 4: Run the complete release gate.**

    Run:

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT" && python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime/generated --check)

    (cd "$REPO_ROOT" && python -m pip install -e sdk)

    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime -q)

    (cd "$REPO_ROOT/backend" && python -m pytest tests/v2/incremental -q)

    (cd "$REPO_ROOT/backend" && python -m pytest tests/agent/test_celery_topology.py tests/v2/incremental/test_refresh_task_dispatch.py tests/v2/incremental/test_refresh_operability.py -q)

    (cd "$REPO_ROOT/backend" && python -m pytest tests/runtime/test_acceptance_matrix.py -q)

    (cd "$REPO_ROOT" && python -m pytest test_data/runtime/test_fixture_manifest.py -q)

    DIALECT_PROJECT="ontexus-dialect-${GITHUB_RUN_ID:-local}-$$"
    (cd "$REPO_ROOT" && docker compose -p "$DIALECT_PROJECT" -f test_data/runtime/db/docker-compose.yml up -d --wait postgres mysql)

    (cd "$REPO_ROOT/backend" && RUNTIME_POSTGRES_URL=postgresql://runtime:runtime@localhost:55432/runtime RUNTIME_MYSQL_URL=mysql+pymysql://runtime:runtime@localhost:53306/runtime python -m pytest tests/v2/incremental/integration tests/runtime/integration -q)
    (cd "$REPO_ROOT" && docker compose -p "$DIALECT_PROJECT" -f test_data/runtime/db/docker-compose.yml down -v --remove-orphans)

    (cd "$REPO_ROOT/sdk" && python -m pytest tests -q)

    (cd "$REPO_ROOT/frontend" && npm run test:ci)

    (cd "$REPO_ROOT/frontend" && npx playwright test src/test/e2e/runtime-governance.spec.ts)

    (cd "$REPO_ROOT" && docker compose -f docker-compose.v2.yml config --quiet)

    (cd "$REPO_ROOT" && docker compose -f docker-compose.agent.yml config --quiet)

    set -e
    SMOKE_PROJECT=ontexus-final-smoke
    cleanup() {
      docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" down -v --remove-orphans
    }
    trap cleanup EXIT
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" down -v --remove-orphans
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" up --build -d --wait
    MIGRATION_ID="$(docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" ps -q migration)"
    test -n "$MIGRATION_ID"
    test "$(docker inspect -f '{{.State.Status}}' "$MIGRATION_ID")" = "exited"
    test "$(docker inspect -f '{{.State.ExitCode}}' "$MIGRATION_ID")" = "0"
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" ps --status running backend frontend
    curl -fsS http://127.0.0.1:8000/health | python -c 'import json, sys; assert json.load(sys.stdin)["status"] == "ok"'
    curl -fsS http://127.0.0.1:5173/ >/dev/null
    docker compose -p "$SMOKE_PROJECT" -f "$REPO_ROOT/docker-compose.v2.yml" down -v --remove-orphans

    Expected: all M1, Phase 2 refresh/lineage, and Phase 3 checks pass; every case points to exactly one executed, non-skipped target; batch, micro-batch, and bounded managed webhook/outbox refresh cases show cursor/idempotency/lineage/freshness evidence; the Python/Celery topology shows explicit named queue routes, run-ID-only messages, bounded worker settings, graceful retry, queue saturation isolation, and sanitized readiness metrics; cancellation cases prove fenced cancel-before/inflight/after-tentative-materialization safe points leave cursor/lineage unchanged, and a cancellation request arriving after any terminal state returns the plain `already_terminal` result without progress mutation; configuration upgrade after claim makes outcome validation compare the frozen revision/contract before lease/owner/fencing validation, produces typed `CONFIGURATION_DRIFT` with no DatasetVersion/PipelineRun lineage association or cursor progress, and invalidates the old lease/fence; fresh v2 Compose reaches healthy backend/frontend after migration completion; no production connector is used by Sandbox; both dialects reject binding/connection drift before a transaction; managed webhook/outbox prerequisites are reported rather than assumed; and the final isolated volumes are removed.

- [ ] **Step 5: Commit the release gate.**

    REPO_ROOT="$(git rev-parse --show-toplevel)"
    git -C "$REPO_ROOT" add backend/tests/runtime/test_acceptance_matrix.py backend/tests/v2/incremental/test_refresh_cancellation.py backend/tests/v2/incremental/integration/test_refresh_cancellation_dialects.py .github/workflows/agent-mvp.yml test_data/runtime/README.md test_data/runtime/db/docker-compose.yml README.md README_zh.md

    git -C "$REPO_ROOT" commit -m "ci: add semantic runtime acceptance gates"

## Workstream 4 — Three real-model business journey acceptance

Tasks 29–33 are the final cross-milestone acceptance work. They depend on
Task 1's generic fixture conventions, M1 Tasks 2–5, the Phase 2 Runtime
Tasks 6–20, and the Phase 3 governed execution Tasks 21–27. They do not
replace the unit, API, SDK, MCP, refresh, dialect, or Runtime tests; they bind
those components into the three business paths a seed customer will use.

### Task 29: Version the three multimodal journey corpora

- [ ] **Deliverable:** Three low-cost, hash-addressed journey manifests exist
  and each maps normal, edge, security/governance, runtime-resilience, and
  writeback cases to exact backend and Playwright targets. Existing domain
  assets remain read-only references.

**Files:**

- Create: `test_data/runtime/supply_chain/manifest.json`
- Create: `test_data/runtime/supply_chain/inputs.json`
- Create: `test_data/runtime/supply_chain/semantic_minima.json`
- Create: `test_data/runtime/supply_chain/dialogues.json`
- Create: `test_data/runtime/supply_chain/governance.json`
- Create: `test_data/runtime/supply_chain/case_matrix.json`
- Create: `test_data/runtime/supply_chain/reproducibility.json`
- Create: `test_data/runtime/finance/manifest.json`
- Create: `test_data/runtime/finance/inputs.json`
- Create: `test_data/runtime/finance/semantic_minima.json`
- Create: `test_data/runtime/finance/dialogues.json`
- Create: `test_data/runtime/finance/governance.json`
- Create: `test_data/runtime/finance/case_matrix.json`
- Create: `test_data/runtime/finance/reproducibility.json`
- Create: `test_data/runtime/credit/manifest.json`
- Create: `test_data/runtime/credit/inputs.json`
- Create: `test_data/runtime/credit/semantic_minima.json`
- Create: `test_data/runtime/credit/dialogues.json`
- Create: `test_data/runtime/credit/governance.json`
- Create: `test_data/runtime/credit/case_matrix.json`
- Create: `test_data/runtime/credit/reproducibility.json`
- Create: `test_data/runtime/journey_registry.py`
- Create: `test_data/runtime/test_journey_manifest.py`
- Create: `test_data/runtime/test_registered_case_execution.py`
- Modify: `test_data/runtime/generate_runtime_fixtures.py`
- Modify: `test_data/runtime/manifest.json`
- Modify: `test_data/runtime/registry.py`
- Modify: `test_data/runtime/test_fixture_manifest.py`
- Modify: `test_data/runtime/README.md`

**Interfaces:**

- `JourneyManifest` is a typed record with `journey_id` in
  `{"supply_chain", "finance", "credit"}`, `fixture_version`, `seed`,
  `model_id`, `inputs`, `source_refs`, `input_hashes`, `semantic_minima`,
  `dialogue_scenarios`, `governance_outcomes`, `case_matrix`,
  `reproducibility`, `logical_model_calls == 3`,
  `max_tool_rounds_per_turn == 1`, and `max_http_attempts == 6`.
- `load_journey_manifest(journey_id: str, root: Path) -> JourneyManifest`
  loads one directory and rejects a missing field, an unknown journey, a
  source reference outside the repository, a non-SHA-256 input hash, or a
  model ID other than `deepseek-v4-flash-vision-exp`.
- `validate_journey_manifest(manifest: JourneyManifest) -> None` enforces one
  tabular input, one policy/report document, one fixed rendered visual-page
  input, one governed dialogue scenario containing both the normal result and
  high-risk proposal, and governance outcomes `automatic`, `approved`,
  `rejected`, and `expired`.
- `journey_registry.py` exposes
  `journey_cases(journey_id: str) -> list[Mapping[str, object]]` and
  `assert_journey_case(case: Mapping[str, object]) -> None`. Every case has
  `case_id`, `coverage`, `expected`, `layers` containing
  `business_journey`, `journey_id`, `fixture_manifest_sha256`, `risk_class`,
  `fixture_hashes`, an explicit `execution_mode`, and a plural `test_targets`
  list with exactly one entry whose kind is `pytest` or `playwright`. Browser
  cases use the Playwright target; deterministic non-browser cases use one
  pytest node. `normal-pipeline-release` is the only `real_model_browser`
  case for each journey and points to that journey's exact Task 32 title; all
  other journey cases are `deterministic` and point to one pytest node. No
  target is marked optional or merely listable. The registry delegates
  deterministic target execution to the sole runner defined by Task 28,
  `backend/tests/runtime/run_registered_cases.py`; Task 29 creates no second
  runner and does not execute a real-model browser case.

- [ ] **Step 1: Write failing fixture-contract tests.**

    ```python
    def test_three_journey_manifests_have_multimodal_inputs_and_governance_states():
        for journey_id in ("supply_chain", "finance", "credit"):
            manifest = load_journey_manifest(journey_id, Path("test_data/runtime"))
            assert manifest.model_id == "deepseek-v4-flash-vision-exp"
            assert {part["kind"] for part in manifest.inputs} >= {"tabular", "document", "image"}
            assert {item["id"] for item in manifest.governance_outcomes} == {
                "automatic", "approved", "rejected", "expired"
            }

    def test_journey_manifests_reference_existing_assets_and_are_hash_reproducible():
        for journey_id in ("supply_chain", "finance", "credit"):
            manifest = load_journey_manifest(journey_id, Path("test_data/runtime"))
            validate_journey_manifest(manifest)
            assert manifest.reproducibility["seed"] == 20260826
            assert manifest.reproducibility["manifest_sha256"] == hash_manifest(manifest)
            for source in manifest.source_refs:
                assert Path(source["path"]).is_file()

    def test_journey_case_matrix_uses_one_typed_target_and_explicit_mode():
        for journey_id in ("supply_chain", "finance", "credit"):
            for case in journey_cases(journey_id):
                assert len(case["test_targets"]) == 1
                assert case["test_targets"][0]["kind"] in {"pytest", "playwright"}
                assert case["execution_mode"] in {"deterministic", "real_model_browser"}
                assert case["skip_allowed"] is False
                if case["execution_mode"] == "deterministic":
                    assert case["test_targets"][0]["kind"] == "pytest"
                else:
                    assert case["case_id"] == "normal-pipeline-release"
                    assert case["test_targets"][0]["kind"] == "playwright"
    ```

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run: `python -m pytest test_data/runtime/test_journey_manifest.py -q`

    Expected: FAIL because the three journey directories, typed manifest
    loader, semantic minima, and registry do not exist.

- [ ] **Step 3: Generate the fixed journey inputs and contracts.**

    Add the following exact semantic minima and action names to the generated
    JSON. The values are deliberately small so one PR cannot trigger an
    uncontrolled corpus or model-call expansion:

    ```python
    JOURNEY_MINIMA = {
        "supply_chain": {
            "entities": ["Supplier", "PurchaseOrder", "InventoryItem", "Warehouse"],
            "relations": ["SUPPLIES", "PLACED_WITH", "CONTAINS", "BELOW_SAFETY_STOCK"],
            "rules": ["inventory_below_safety_stock"],
            "actions": ["risk_label", "purchase_order_price_update"],
            "keywords": ["supplier", "inventory", "safety stock", "purchase order"],
            "low_risk_action": "risk_label",
            "high_risk_action": "purchase_order_price_update",
        },
        "finance": {
            "entities": ["Account", "Invoice", "Expense", "CostCenter", "AccountingPeriod"],
            "relations": ["POSTED_TO", "BELONGS_TO", "DUPLICATES", "EXCEEDS_BUDGET"],
            "rules": ["duplicate_invoice", "expense_over_budget"],
            "actions": ["risk_label", "journal_entry"],
            "keywords": ["invoice", "expense", "accounting period", "cash flow"],
            "low_risk_action": "risk_label",
            "high_risk_action": "journal_entry",
        },
        "credit": {
            "entities": ["Borrower", "LoanApplication", "Repayment", "CreditLine", "RiskAssessment"],
            "relations": ["APPLIES_FOR", "HAS_REPAYMENT", "ASSESSED_AS", "USES_CREDIT_LINE"],
            "rules": ["credit_score_limit"],
            "actions": ["risk_label", "credit_limit_update"],
            "keywords": ["borrower", "application", "repayment", "credit limit"],
            "low_risk_action": "risk_label",
            "high_risk_action": "credit_limit_update",
        },
    }
    ```

    `inputs.json` references the existing supply-chain, finance, and credit
    CSV/XLSX/policy/PDF files listed in the design spec and adds synthetic
    rows with stable IDs. Render exactly page 1 of the selected PDF at the
    pinned resolution used by the repository's document tooling and record
    the generated PNG hash; do not commit a copied source file. `dialogues.json`
    contains one governed question that asks for the normal business result
    and a high-risk proposal in the same Agent turn. The turn state is one
    initial completion -> one MCP/query tool call/result -> one final
    completion. Each journey has `logical_model_calls: 3`,
    `max_tool_rounds_per_turn: 1`, and `max_http_attempts: 6`; automatic,
    approved, rejected, expired, and reconciliation branches reuse the final
    persisted plan without another model call.

    `case_matrix.json` must contain these case IDs for each journey:
    `normal-pipeline-release`, `edge-empty-result`, `edge-duplicate-or-missing`,
    `security-no-grant`, `security-stale-release`, `security-prompt-injection`,
    `resilience-timeout-retry`, `resilience-429-retry`,
    `resilience-provider-error`, `resilience-mcp-timeout`,
    `resilience-sse-reconnect`, `writeback-automatic`,
    `writeback-hitl-approved`, `writeback-hitl-rejected`, and
    `writeback-hitl-expired`. Failure-injection cases use deterministic
    transport doubles in backend tests; they do not call the real model.
    Set `execution_mode: "real_model_browser"` and the exact Task 32
    Playwright target only for `normal-pipeline-release`; set
    `execution_mode: "deterministic"` and one exact pytest target for every
    other case. The three real-model normal cases are not selected by the
    Task 28 registry runner and are not counted as deterministic targets or
    additional model calls; Task 32 runs them exactly once.

    `governance.json` records `execution_class`, `expected_status`,
    `target_before_hash`, `target_after_hash`, `must_write`, and
    `required_plan_hash` for each outcome. Automatic outcomes have
    `execution_class: "AUTOMATIC"` and `must_write: true` only to the
    disposable Sandbox/fixture target. Approved outcomes have
    `execution_class: "HUMAN_APPROVED"` and `must_write: true`; rejected and
    expired outcomes have `must_write: false` and identical before/after
    target hashes.

- [ ] **Step 4: Generate and verify the corpus.**

    Run:

    ```bash
    REPO_ROOT="$(git rev-parse --show-toplevel)"
    (cd "$REPO_ROOT" && python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime/generated)
    (cd "$REPO_ROOT" && python test_data/runtime/generate_runtime_fixtures.py --seed 20260826 --output test_data/runtime/generated --check)
    (cd "$REPO_ROOT" && python -m pytest test_data/runtime/test_fixture_manifest.py test_data/runtime/test_journey_manifest.py -q)
    ```

    Expected: all three manifests are idempotent, source and rendered-image
    hashes resolve, every required matrix case has exactly one executable
    registry target, the registry runner executes every deterministic case
    without a skip, all actions have exact risk/governance outcomes, and no
    generated byte contains a secret, PII, credential, production URI, or raw
    source row.

- [ ] **Step 5: Commit only the journey corpus and registry.**

    ```bash
    git add test_data/runtime/supply_chain test_data/runtime/finance test_data/runtime/credit \
      test_data/runtime/journey_registry.py test_data/runtime/generate_runtime_fixtures.py \
      test_data/runtime/manifest.json test_data/runtime/registry.py \
      test_data/runtime/test_fixture_manifest.py test_data/runtime/test_journey_manifest.py \
      test_data/runtime/README.md
    git commit -m "test: add business journey fixture contracts"
    ```

### Task 30: Add the exact DeepSeek vision client and semantic/artifact validators

- [ ] **Deliverable:** Real-model calls are exact-model, bounded-retry,
  multimodal, semantically validated, and safely observable. Mock transport
  tests cover failure behavior; they do not replace the real gate.

**Files:**

- Create: `backend/evals/business_journeys/__init__.py`
- Create: `backend/evals/business_journeys/contracts.py`
- Create: `backend/evals/business_journeys/deepseek_client.py`
- Create: `backend/evals/business_journeys/semantic_validators.py`
- Create: `backend/evals/business_journeys/artifacts.py`
- Create: `backend/evals/business_journeys/test_deepseek_client.py`
- Create: `backend/evals/business_journeys/test_semantic_validators.py`
- Create: `backend/evals/business_journeys/test_artifacts.py`

**Interfaces:**

- `MODEL_ID: Final[str] = "deepseek-v4-flash-vision-exp"` is the only
  accepted model ID.
- `OFFICIAL_ORIGIN: Final[str] = "https://api.deepseek.com"` is the only
  model origin. `DeepSeekVisionClient` has no production endpoint parameter
  or environment override: `DeepSeekVisionClient(api_key: str, *,
  timeout_seconds: float = 45.0, transport: httpx.BaseTransport | None =
  None)`. The client parses and validates this constant as an HTTPS origin
  with hostname `api.deepseek.com`, no userinfo, non-default port, query, or
  fragment, constructs only `/models` and `/chat/completions`, and sets
  `follow_redirects=False`; an unexpected redirect location is a failure.
  `transport` is an injected `httpx` test transport only and is never
  accepted from the real gate or from CI configuration, so the official key
  can only be sent to the official origin.
- `InputPart(kind: Literal["text", "image"], media_type: str,
  content: bytes | str, sha256: str)` is the model input type. Image content
  is sent through the official DeepSeek multimodal request shape; source
  filenames and fixture hashes, not raw secrets, identify it in evidence.
- `DeepSeekVisionClient.verify_model() -> ModelProbe` calls `GET /models`,
  requires HTTP success and an exact `MODEL_ID` entry, and never maps an
  unsupported model name to another ID.
- `DeepSeekVisionClient.complete(parts: Sequence[InputPart], *, response_schema: Mapping[str, object], correlation_id: str) -> ModelResponse` sends `model=MODEL_ID`, records requested and observed model IDs, and retries exactly once only for a timeout or HTTP 429 after an exponential backoff. The second retryable failure and every other error raise a typed exception.
- `validate_semantic_minimum(response: Mapping[str, object], minimum: JourneySemanticMinimum) -> SemanticValidation` checks required entities, relations, rules, actions, source citations, keywords, and structured numeric predicates. It accepts wording variation but rejects a generic/non-empty answer, missing evidence, or irrelevant semantics.
- `ArtifactAllowlist` is the only upload schema and contains exactly
  `schema_version`, `run_id`, `journey_id`, `fixture_manifest_sha256`,
  `model_requested`, `model_observed`, `logical_model_calls`,
  `http_attempts`, `retry_count`, `call_timestamps`, semantic-validation
  outcomes, state-transition names, citation/tool/audit/backend trace IDs,
  and sanitized browser artifact references. It has no raw response/prompt,
  input content, source rows, headers, credential, or arbitrary URL fields.
- `build_fixture_forbidden_values(manifest: JourneyManifest) -> frozenset[str]`
  derives exact values from every fixture cell, source/document projection,
  prompt-injection sentinel, synthetic secret/header/JWT test value, PII
  sentinel, and production URL. `sanitize_browser_artifacts(...)` redacts or
  omits raw Playwright traces, screenshots, and logs before they are placed
  in the allowlist. `scan_artifact(path: Path, *, forbidden_values:
  frozenset[str]) -> None` recursively scans serialized bytes and structured
  fields for model-response echo, prompt-injection text, raw fixture cells,
  secret/header/JWT forms, PII patterns, and production URLs; it fails closed
  on any match. Only a passing sanitized artifact is uploadable.

- [ ] **Step 1: Write failing client, semantic, and redaction tests.**

    ```python
    def test_model_preflight_requires_the_exact_vision_model(mock_http):
        mock_http.get("/models", json={"data": [{"id": "deepseek-v4-flash"}]})
        with pytest.raises(ModelConfigurationError, match="MODEL_ID_NOT_FOUND"):
            DeepSeekVisionClient("runtime/runtime", transport=mock_http).verify_model()

    def test_timeout_and_429_retry_once_then_stop(mock_http):
        mock_http.queue_timeout_then_success(model=MODEL_ID)
        assert DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
            [InputPart("text", "text/plain", "question", sha256_text("question"))],
            response_schema={"type": "object"}, correlation_id="journey:test",
        ).model == MODEL_ID
        mock_http.queue_429_then_429()
        with pytest.raises(DeepSeekRetryExhausted):
            DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
                [], response_schema={"type": "object"}, correlation_id="journey:test-2",
            )

    def test_model_client_has_no_endpoint_override_and_uses_official_origin(mock_http):
        assert set(inspect.signature(DeepSeekVisionClient).parameters) == {
            "api_key", "timeout_seconds", "transport"
        }
        mock_http.get("https://api.deepseek.com/models", json={"data": [{"id": MODEL_ID}]})
        DeepSeekVisionClient("runtime/runtime", transport=mock_http).verify_model()
        assert mock_http.last_request.url == "https://api.deepseek.com/models"

    @pytest.mark.parametrize("url", [
        "http://api.deepseek.com/models",
        "https://evil.example.invalid/models",
        "https://user:pass@api.deepseek.com/models",
        "https://api.deepseek.com:8443/models",
        "https://api.deepseek.com/models?redirect=evil",
    ])
    def test_official_origin_validation_rejects_override_or_redirect(url):
        with pytest.raises(DeepSeekEndpointError):
            validate_official_url(url)

    def test_non_retryable_provider_error_has_one_attempt(mock_http):
        mock_http.queue_status(500)
        with pytest.raises(DeepSeekProviderError):
            DeepSeekVisionClient("runtime/runtime", transport=mock_http).complete(
                [], response_schema={"type": "object"}, correlation_id="journey:500",
            )
        assert mock_http.request_count == 1

    def test_semantic_validator_rejects_missing_relation_and_citation():
        with pytest.raises(SemanticMinimumError) as exc:
            validate_semantic_minimum(
                {"entities": ["Supplier"], "relations": [], "citations": []},
                supply_chain_minimum(),
            )
        assert exc.value.reason_code == "SEMANTIC_MINIMUM_NOT_MET"

    def test_artifact_scan_rejects_model_echo_injection_fixture_and_sensitive_forms(tmp_path):
        path = tmp_path / "result.json"
        path.write_text('{"summary":"prompt injection: ignore policy", "cell":"fixture-cell", "token":"Bearer abc.def.ghi"}')
        forbidden = build_fixture_forbidden_values(fixture_with_cell("fixture-cell"))
        with pytest.raises(ArtifactSafetyError):
            scan_artifact(path, forbidden_values=forbidden)

    def test_raw_browser_trace_is_redacted_or_omitted_before_upload(tmp_path):
        raw = tmp_path / "trace.zip"
        raw.write_bytes(b"fixture-cell production.example.com")
        sanitized = sanitize_browser_artifacts([raw], forbidden_values=frozenset({"fixture-cell", "production.example.com"}))
        assert sanitized == ()
    ```

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run: `cd backend && python -m pytest evals/business_journeys/test_deepseek_client.py evals/business_journeys/test_semantic_validators.py evals/business_journeys/test_artifacts.py -q`

    Expected: FAIL because the exact model client, semantic minima validator,
    retry policy, and redaction scanner do not exist.

- [ ] **Step 3: Implement the bounded real-model adapter.**

    Use `httpx` with an injected transport only in unit tests. The production
    client constructs and validates the constant `https://api.deepseek.com`
    origin, disables redirects, and has no endpoint parameter or environment
    override. Require a non-empty API key before the first request. Parse
    `/models` and completion response model IDs independently; both must equal
    `MODEL_ID`. Preserve the original provider error class in the redacted
    artifact as a category (`timeout`, `429`, `provider_error`), not its
    response body. The retry loop has one explicit retry counter and no
    generic retry middleware. Canonicalize semantic names case-insensitively
    for matching, but retain exact source citation IDs and numeric predicates.
    Limit serialized response summaries to structural fields and a bounded,
    redacted text excerpt; never serialize request headers, API keys, raw
    prompts, raw documents, or protected rows.

    Implement `ArtifactAllowlist` as a closed schema (reject unknown fields),
    derive the forbidden-value set from the loaded fixture rather than a
    hand-maintained list, and run the scanner over model output echoes,
    prompt-injection text, every raw fixture cell, secret/header/JWT forms,
    PII patterns, and production URLs. Raw browser trace/screenshot/log
    bytes are sanitized or omitted; the upload path accepts only the
    sanitized allowlist object.

- [ ] **Step 4: Run the focused tests and static safety checks.**

    Run:

    ```bash
    cd backend
    python -m pytest evals/business_journeys/test_deepseek_client.py evals/business_journeys/test_semantic_validators.py evals/business_journeys/test_artifacts.py -q
    ! rg -n "pull_request_target" evals/business_journeys .github/workflows/agent-mvp.yml
    ```

    Expected: tests pass, the client signature has no endpoint override, every
    model request URL is the canonical official origin with redirects disabled,
    the artifact scanner rejects all forbidden-value categories, and no
    `pull_request_target` appears in the new client or workflow. The CI
    contract test in Task 33 checks the business-journey job's
    `continue-on-error` field after that job is wired; the existing
    document-only diagnostic step is not evaluated by this task.

- [ ] **Step 5: Commit the model client and validators.**

    ```bash
    git add backend/evals/business_journeys
    git commit -m "test: enforce exact DeepSeek vision evaluation contract"
    ```

### Task 31: Prepare the journey baseline for the browser acceptance

- [ ] **Deliverable:** A strict `prepare_journey` runner executes only the
  pre-browser baseline for each journey: Pipeline -> Curated review -> real
  vision ontology completion -> publish release -> governed MCP descriptors
  and ontology grant -> immutable model configuration. It returns binding
  options for the browser and MUST NOT create an Agent, persist an Agent
  binding, run an Agent turn, execute Sandbox/writeback, or create HITL
  outcomes.

**Files:**

- Create: `backend/evals/business_journeys/api_client.py`
- Create: `backend/evals/business_journeys/orchestrator.py`
- Create: `backend/evals/business_journeys/run.py`
- Create: `backend/evals/business_journeys/test_orchestrator.py`
- Create: `backend/tests/acceptance/test_business_journey_api.py`
- Modify: `backend/app/services/llm_service.py`
- Modify: `backend/app/tasks/extraction.py`
- Modify: `backend/app/services/model_config_selector.py`
- Modify: `backend/app/services/model_callers/extraction.py`
- Modify: `backend/app/routers/ontologies.py`
- Modify: `backend/app/routers/ontology_lifecycle.py`
- Modify: `backend/app/routers/ontology_data_grants.py`
- Modify: `backend/app/routers/v2/pipelines.py`
- Modify: `backend/app/routers/v2/curated.py`
- Modify: `backend/app/routers/mcp.py`
- Modify: `backend/app/routers/v2/runtime.py`
- Only include a router in the task commit when a named failing acceptance
  test identifies its missing release, grant, binding, or evidence field;
  otherwise leave that file untouched.

**Interfaces:**

- `JourneyApiClient` exposes typed methods
  `start_pipeline(manifest: JourneyManifest) -> PipelineEvidence`,
  `approve_curated(run_id: str) -> CuratedEvidence`,
  `create_or_complete_ontology(manifest: JourneyManifest, model_version_id: str) -> OntologyReleaseEvidence`,
  `publish_mcp_descriptors(release_id: str) -> tuple[McpDescriptor, ...]`,
  `grant_ontology_data(ontology_id: str, user_id: str, release_id: str) -> GrantEvidence`,
  `prepare_browser_agent_binding(manifest: JourneyManifest, release_id: str, descriptors: Sequence[McpDescriptor], model_version_id: str) -> AgentBindingOptions`,
  and `prepare_journey(journey_id: str) -> JourneyPreparation`. The
  `prepare_browser_agent_binding` method only validates and returns the
  published release, granted descriptor IDs, and model configuration; it
  cannot create an Agent or invoke Runtime turns.
- `AgentBindingOptions` contains only the published `ontology_release_id`,
  granted `mcp_descriptor_ids`, and immutable `model_config_version_id` that
  the browser wizard must select; it does not create or name an Agent.
- `prepare_journey(journey_id: str, *, api_base: str, api_key: str, output_dir: Path, run_id: str, model_id: str = MODEL_ID) -> JourneyPreparation` first calls `DeepSeekVisionClient.verify_model()`, seeds only the isolated baseline, and fails on any missing state. `api_base` is the application-under-test URL only; it never configures the DeepSeek client. It performs exactly one ontology completion and no Agent turn. `prepare_all_journeys(...) -> list[JourneyPreparation]` returns exactly three entries in the fixed order `supply_chain`, `finance`, `credit` and fails if any entry is absent or non-passing.
- `verify_journey(journey_id: str, *, api_base: str, output_dir: Path, run_id: str) -> JourneyVerification` is a separate post-browser operation owned by this task. It runs only after the corresponding Task 32 browser test has finished, reads persisted Agent binding, turn/tool trace, citation, audit, Sandbox receipt, HITL receipt/status, target before/after hashes, and model budget records, and performs no model call, mutation, Agent creation, turn, tool invocation, approval, or writeback. `verify_all_journeys(...) -> list[JourneyVerification]` reads the three journeys in the fixed order `supply_chain`, `finance`, `credit` and fails if any persisted record is missing or violates the exact one-tool/three-logical-call budget.
- `backend/evals/business_journeys/run.py` exposes `--phase prepare|verify`, `--journey {supply_chain,finance,credit,all}`, `--model-id` (required and exact only for `--phase prepare`, defaulting to `MODEL_ID` but rejecting every other value), `--api-base` for the local application only, `--output`, and `--run-id`; `--phase prepare --journey all` runs the three baseline preparations in fixed order and exits non-zero unless all three are passed. `--phase verify --journey all` runs only the read-only verifiers after the browser phase and exits non-zero unless all three persisted journey records pass. There is no model endpoint option or model-base environment input.
- `JourneyPreparationBudget` is `{logical_model_calls: 1,
  max_http_attempts: 2}`. `prepare_journey` consumes exactly this ontology
  completion budget and no Agent-turn budget. Task 32 consumes the remaining
  two logical completions for the sole browser-triggered Agent turn; Task 33
  verifies the combined journey budget of three logical calls and at most six
  HTTP attempts without invoking the model again.
- `JourneyPreparation` contains `journey_id`, `fixture_manifest_sha256`,
  `model_requested`, `model_observed`, `pipeline_run_id`, `dataset_version_id`,
  `ontology_id`, `ontology_release_id`, `semantic_snapshot_id`, `mcp_descriptor_ids`,
  `grant_id`, `agent_binding_options`, `model_config_version_id`,
  `logical_model_calls`, `http_attempts`, and `status`. It contains no raw
  input, credential, prompt, Agent ID, turn, tool, Sandbox, or HITL outcome;
  the browser report records the Agent ID/version after the UI creates and
  binds it.
- `JourneyVerification` contains `journey_id`, `run_id`, the persisted Agent,
  binding, turn, tool, citation, audit, Sandbox, approval, receipt, target
  hash, and budget identifiers/statuses, plus `model_requested`,
  `model_observed`, `logical_model_calls`, `http_attempts`,
  `tool_rounds`, and `status`. It is an evidence record only; the verifier
  never returns a new model response or an action result produced by a new
  call.

- [ ] **Step 1: Write failing API-chain and governance tests.**

    ```python
    def test_prepare_journey_requires_every_release_and_browser_binding_option(fake_api):
        fake_api.drop("mcp_descriptor")
        with pytest.raises(JourneyAcceptanceError, match="MCP_DESCRIPTOR_MISSING"):
            prepare_journey("supply_chain", api_base=fake_api.url,
                        api_key="runtime/runtime", output_dir=Path("artifacts"),
                        run_id="journey-test")

    def test_orchestrator_rejects_model_version_drift(fake_api):
        fake_api.model_response_id = "deepseek-v4-flash"
        with pytest.raises(JourneyAcceptanceError, match="MODEL_ID_MISMATCH"):
            prepare_journey("finance", api_base=fake_api.url,
                        api_key="runtime/runtime", output_dir=Path("artifacts"),
                        run_id="journey-model-drift")

    def test_prepare_journey_does_not_create_agent_or_run_agent_turn(fake_api):
        preparation = prepare_journey("supply_chain", api_base=fake_api.url,
                                      api_key="runtime/runtime", output_dir=Path("artifacts"),
                                      run_id="journey-prepare-only")
        assert preparation.agent_binding_options
        assert fake_api.agent_create_calls == 0
        assert fake_api.agent_turn_calls == 0
        assert preparation.logical_model_calls == 1

    def test_prepare_journey_returns_only_published_binding_options(fake_api):
        preparation = prepare_journey("finance", api_base=fake_api.url,
                                      api_key="runtime/runtime", output_dir=Path("artifacts"),
                                      run_id="journey-prepare-options")
        assert preparation.agent_binding_options.ontology_release_id == preparation.ontology_release_id
        assert set(preparation.agent_binding_options.mcp_descriptor_ids) == set(preparation.mcp_descriptor_ids)
        assert preparation.agent_binding_options.model_config_version_id

    def test_verify_journey_reads_only_persisted_browser_evidence(fake_api):
        verification = verify_journey(
            "credit", api_base=fake_api.url, output_dir=Path("artifacts"),
            run_id="journey-verify-only",
        )
        assert verification.status == "passed"
        assert verification.logical_model_calls == 3
        assert verification.tool_rounds == 1
        assert fake_api.model_calls == 0
        assert fake_api.mutations == []
    ```

- [ ] **Step 2: Run the focused tests to verify they fail.**

    Run: `cd backend && python -m pytest evals/business_journeys/test_orchestrator.py tests/acceptance/test_business_journey_api.py -q`

    Expected: FAIL because the preparation runner does not yet enforce the
    Pipeline/Curated/release/MCP/grant/model-configuration baseline or return
    complete browser-binding options, and the read-only post-browser verifier
    does not yet validate persisted trace/audit/receipt/budget evidence.

- [ ] **Step 3: Implement the real application chain.**

    Seed the DeepSeek model configuration/version through the existing model
    configuration service using `DEEPSEEK_API_KEY`; never write the key to a
    fixture or artifact. Submit the versioned multimodal inputs to the real
    Pipeline, wait for a durable successful run, approve the Curated result,
    and materialize the DatasetVersion/SemanticSnapshot lineage. Call the
    existing ontology extraction path with the exact model version, validate
    the response against that journey's semantic minima, then require an
    explicit publish/release result. Build MCP descriptors and the ontology
    data grant from that release and return only `AgentBindingOptions` (the
    published release ID, granted descriptor IDs, and immutable model config
    version) for the browser. This API runner must not create an Agent or
    persist an Agent binding; those actions are reserved for the real UI
    flow in Task 32.

    Stop after returning the published release, active ontology grant,
    selected MCP descriptor IDs, immutable model configuration version, and
    the one ontology-completion budget record. Do not create an Agent or Agent
    binding, create a turn, invoke a tool, evaluate Sandbox/risk, create an
    approval, or write any target. Those actions belong exclusively to the
    browser phase in Task 32. The separate `verify_journey` operation is
    invoked only after that browser phase and only reads persisted evidence;
    it never calls the model or changes state.

- [ ] **Step 4: Run backend function and API integration checks.**

    Run:

    ```bash
    cd backend
    python -m pytest evals/business_journeys/test_orchestrator.py tests/acceptance/test_business_journey_api.py -q
    ```

    Expected: all baseline preparation failure cases and isolated application
    integration cases pass; every `JourneyPreparation` has one release,
    snapshot, grant, browser-binding options, exact model ID, exactly one
    logical ontology call, and no Agent/turn/tool/Sandbox/HITL state. The
    `verify_journey` checks pass only against persisted post-browser records,
    make zero model calls and zero mutations, and validate the exact
    three-call/one-tool budget. No production connector is contacted.

- [ ] **Step 5: Commit the API-chain runner.**

    ```bash
    git add backend/evals/business_journeys backend/tests/acceptance/test_business_journey_api.py backend/app/services/llm_service.py backend/app/tasks/extraction.py backend/app/services/model_config_selector.py backend/app/services/model_callers/extraction.py backend/app/routers/ontologies.py backend/app/routers/ontology_lifecycle.py backend/app/routers/ontology_data_grants.py backend/app/routers/v2/pipelines.py backend/app/routers/v2/curated.py backend/app/routers/mcp.py backend/app/routers/v2/runtime.py
    git commit -m "test: exercise governed business journeys through Runtime"
    ```

### Task 32: Replace the API/screenshot demo with strict Playwright journeys

- [ ] **Deliverable:** Three browser tests perform login, Agent binding
  verification, dialogue, trace/citation inspection, Sandbox automatic action,
  and HITL approve/reject/expire. They fail on seed or API errors and contain
  no `test.skip`, `test.fixme`, soft-failing seed, or API-only substitute.

**Files:**

- Create: `frontend/src/test/e2e/fixtures/businessJourneys.ts`
- Create: `frontend/src/test/e2e/business-journeys.spec.ts`
- Modify: `frontend/src/pages/ontologies/detail/OntologyDetailPage.tsx`
- Modify: `frontend/src/pages/ontologies/detail/OntologyAccessPanel.tsx`
- Modify: `frontend/src/pages/agents/new/AgentCreateWizard.tsx`
- Modify: `frontend/src/pages/agents/detail/AgentDetailPage.tsx`
- Modify: `frontend/src/pages/agents/detail/AgentInfoTab.tsx`
- Modify: `frontend/src/pages/agents/detail/ToolConfigTab.tsx`
- Modify: `frontend/src/pages/agents/application/AgentApplicationTab.tsx`
- Modify: `frontend/src/pages/agents/application/ConversationPanel.tsx`
- Modify: `frontend/src/pages/agents/application/SessionSidebar.tsx`
- Modify: `frontend/src/pages/agents/application/ActionApprovalCard.tsx`
- Modify: `frontend/src/pages/agents/application/ExecutionTracePanel.tsx`
- Modify: `frontend/src/pages/agents/application/OntologyAccessPanel.tsx`
- Modify: `frontend/src/test/e2e/playwright.config.ts`
- Modify: `frontend/playwright.config.ts`
- Modify: `frontend/scripts/pipeline_full_flow.mjs` only to mark it as a
  manual demo and remove it from all CI acceptance commands

**Interfaces:**

- `loadBusinessJourneyRun(path: string) -> BusinessJourneyRun` reads the
  redacted runner output, requires exactly three passed journey records and
  all baseline IDs/hashes (Pipeline, Curated result, published ontology
  release, MCP descriptors/grant, and model configuration), and throws on
  missing/invalid data. It rejects an API-seeded `agent_id` or
  `agent_version_id`: the browser must create those values. It never returns
  `undefined` to let a test skip.
- The fixture exposes `journeyData(journeyId: JourneyId)`,
  `assertNoSkippedTests(testInfo: TestInfo)`, and stable selectors
  `[data-testid="journey-model-id"]`, `journey-ontology-release`,
  `journey-mcp-tool`, `agent-model-version`, `agent-ontology-release-picker`,
  `agent-mcp-tool-picker`, `agent-create-submit`, `journey-agent-binding`,
  `journey-citation`,
  `journey-tool-trace`, `journey-audit-trace`, `journey-sandbox-status`,
  `journey-approval-status`, `journey-automatic-receipt`,
  `journey-hitl-receipt`, and `journey-target-hash`.
- The three exact Playwright titles are
  `supply chain journey completes the governed browser loop`,
  `finance journey completes the governed browser loop`, and
  `credit journey completes the governed browser loop`. The registry records
  these titles verbatim and Task 33 runs them by exact spec path.

- [ ] **Step 1: Write failing strict browser tests.**

    ```typescript
    const journeyTitles = {
      supply_chain: 'supply chain journey completes the governed browser loop',
      finance: 'finance journey completes the governed browser loop',
      credit: 'credit journey completes the governed browser loop',
    } as const
    for (const journeyId of ['supply_chain', 'finance', 'credit'] as const) {
      test(journeyTitles[journeyId], async ({ page }) => {
        const journey = journeyData(journeyId)
        await loginAsAdmin(page)
        await page.goto('/agents/new')
        await page.getByTestId('agent-name').fill(`${journeyId} acceptance agent`)
        await page.getByTestId('agent-model-version').selectOption(journey.model_config_version_id)
        await page.getByTestId('agent-ontology-release-picker').click()
        await page.getByRole('option', { name: journey.ontology_release_id, exact: true }).click()
        await page.getByTestId('agent-mcp-tool-picker').click()
        for (const descriptorId of journey.mcp_descriptor_ids) {
          await page.getByRole('option', { name: descriptorId, exact: true }).click()
        }
        await page.getByTestId('agent-create-submit').click()
        await expect(page.getByTestId('journey-agent-binding')).toContainText(journey.model_config_version_id)
        await expect(page.getByTestId('journey-ontology-release')).toContainText(journey.ontology_release_id)
        for (const descriptorId of journey.mcp_descriptor_ids) {
          await expect(page.getByTestId('journey-mcp-tool')).toContainText(descriptorId)
        }
        await page.getByRole('button', { name: /Agent Application|智能体应用/ }).click()
        await page.getByTestId('session-new').click()
        await page.getByTestId('conversation-input').fill(journey.dialogues.governed_turn.question)
        await page.getByTestId('conversation-send').click()
        await expect(page.getByTestId('journey-citation')).toContainText(journey.semantic_minima.citation_id)
        await expect(page.getByTestId('journey-tool-trace')).toContainText(journey.mcp_descriptor_ids[0])
        await expect(page.getByTestId('journey-audit-trace')).toContainText(journey.audit_event_ids[0])
        await expect(page.getByTestId('journey-sandbox-status')).toHaveText('AUTOMATIC')
        await expect(page.getByTestId('journey-automatic-receipt')).toBeVisible()
        await expect(page.getByTestId('journey-approval-status')).toHaveText('pending')
        await page.getByTestId('approval-branch-approved').click()
        await page.getByTestId('approve-action').click()
        await expect(page.getByTestId('journey-hitl-receipt')).toBeVisible()
        await page.getByTestId('approval-branch-rejected').click()
        await page.getByTestId('reject-action').click()
        await expect(page.getByTestId('journey-target-hash')).toHaveAttribute('data-before', journey.governance.rejected.target_before_hash)
        await expect(page.getByTestId('journey-target-hash')).toHaveAttribute('data-after', journey.governance.rejected.target_after_hash)
        await page.getByTestId('approval-branch-expired').click()
        await expect(page.getByTestId('journey-approval-status')).toHaveText('expired')
        await expect(page.getByTestId('journey-target-hash')).toHaveAttribute('data-before', journey.governance.expired.target_before_hash)
        await expect(page.getByTestId('journey-target-hash')).toHaveAttribute('data-after', journey.governance.expired.target_after_hash)
      })
    }
    ```

    Add separate deterministic browser steps using the same pre-seeded high-
    risk plan for `reject` and `expired`: assert the status and unchanged
    before/after hash. These actions may be separate `test.step` branches in
    the same journey test, but every branch must be executed, not conditionally
    skipped. Make the browser fixture fail in `beforeAll` if the runner file,
    API health, or any journey ID is missing.

- [ ] **Step 2: Run the new spec before wiring the strict fixture to verify it fails.**

    Run: `cd frontend && npx playwright test --config playwright.config.ts src/test/e2e/business-journeys.spec.ts --list`

    Expected: FAIL or list no matching tests because the strict fixture,
    selectors, and browser journey spec do not yet exist. This is a collection
    failure, not a permitted skip.

- [ ] **Step 3: Implement browser selectors and real UI assertions.**

    Add the stable `data-testid` attributes to the existing ontology detail,
    AgentCreateWizard, Agent binding/tool configuration, conversation, trace,
    ontology-access, Sandbox, and approval components. The browser must start
    at `AgentCreateWizard`, choose the already-published release from the
    server-provided ontology picker, choose the granted MCP descriptors from
    the server-provided tool picker, choose the exact model configuration,
    submit the form, and assert that the resulting Agent binding is persisted.
    Keep all authority and risk decisions server-owned; the UI only renders
    server responses and submits the exact approval hash.
    The fixture reads the runner output path from
    `BUSINESS_JOURNEY_RUN_MANIFEST`, uses the existing UI login helper, and
    performs ontology/release/tool/model selection, Agent creation, binding,
    dialogue, automatic execution, and all three HITL outcomes through visible
    browser controls. API calls in the fixture are limited to health and
    read-only evidence validation; the API seed may prepare only Pipeline,
    Curated, published ontology, descriptor/grant, and model-configuration
    baseline state, never an Agent or its binding. Set the
    dedicated spec to `trace: 'on'` and capture screenshots on every outcome.
    Do not call a backend API in place of a browser interaction; API calls in
    the fixture are limited to health and read-only evidence validation.
    Remove `pipeline_full_flow.mjs` from CI/docs acceptance commands and label
    it as a manual demonstration only.

- [ ] **Step 4: Run frontend tests and strict browser collection.**

    Run:

    ```bash
    cd frontend
    npm run test:ci
    npx playwright test --config playwright.config.ts src/test/e2e/business-journeys.spec.ts --list
    ```

    Expected: frontend unit/type/build checks pass; the list contains exactly
    the three named journey titles; no `test.skip` or `test.fixme` occurs in
    the new spec/fixture; the strict fixture throws on missing seed data.

- [ ] **Step 5: Commit the strict browser acceptance.**

    ```bash
    git add frontend/src/test/e2e/business-journeys.spec.ts frontend/src/test/e2e/fixtures/businessJourneys.ts frontend/src/test/e2e/playwright.config.ts frontend/playwright.config.ts frontend/src/pages/agents/new/AgentCreateWizard.tsx frontend/src/pages/agents/detail/AgentDetailPage.tsx frontend/src/pages/agents/detail/AgentInfoTab.tsx frontend/src/pages/agents/detail/ToolConfigTab.tsx frontend/src/pages/agents/application/AgentApplicationTab.tsx frontend/src/pages/agents/application/ConversationPanel.tsx frontend/src/pages/agents/application/SessionSidebar.tsx frontend/src/pages/agents/application/ActionApprovalCard.tsx frontend/src/pages/agents/application/ExecutionTracePanel.tsx frontend/src/pages/agents/application/OntologyAccessPanel.tsx frontend/scripts/pipeline_full_flow.mjs
    git commit -m "test: add strict browser business journeys"
    ```

### Task 33: Make the real three-journey gate blocking on every trusted PR

- [ ] **Deliverable:** The exact DeepSeek vision journey and strict browser
  suite run as a blocking CI gate on every trusted same-repository PR. The
  old document-only live eval is no longer the acceptance gate.

**Files:**

- Create: `scripts/run_business_journey_gate.sh`
- Create: `backend/tests/agent/test_business_journey_ci_contract.py`
- Modify: `.github/workflows/agent-mvp.yml`
- Modify: `backend/evals/document_extraction/run.py` only if it is retained as
  an explicitly diagnostic, non-acceptance command
- Modify: `frontend/src/test/e2e/playwright.config.ts`
- Modify: `test_data/runtime/README.md`
- Modify: `README.md`
- Modify: `README_zh.md`

**Interfaces:**

- `scripts/run_business_journey_gate.sh` takes `DEEPSEEK_API_KEY` and an
  optional `BUSINESS_JOURNEY_API_BASE` for the application under test only.
  The real-model client has no endpoint input and always validates and calls
  the canonical `https://api.deepseek.com` origin with redirects disabled; a
  non-HTTPS, non-official-host, userinfo, alternate-port, query/fragment, or
  non-official redirect fails before the key is sent. The script
  generates/checks fixtures, starts or uses an isolated API/worker/frontend
  stack, and executes these phases in this exact fail-fast order: (1)
  `python -m tests.runtime.run_registered_cases --manifest
  test_data/runtime/manifest.json --report artifacts/runtime/deterministic-cases.json`
  runs every `execution_mode == "deterministic"` case exactly once with zero
  model calls and validates its sanitized report; (2) `python -m
  evals.business_journeys.run --phase prepare --journey all --model-id
  deepseek-v4-flash-vision-exp --api-base "$BUSINESS_JOURNEY_API_BASE"
  --output artifacts/evals/business_journeys --run-id "$RUN_ID"` prepares
  all three baselines; (3) `npx playwright test --config
  frontend/playwright.config.ts frontend/src/test/e2e/business-journeys.spec.ts`
  runs the three exact named browser journeys and is the only owner of Agent
  creation/binding, the single Agent turn/tool result, Sandbox action, and
  HITL branches; (4) `python -m evals.business_journeys.run --phase verify
  --journey all --api-base "$BUSINESS_JOURNEY_API_BASE" --output
  artifacts/evals/business_journeys --run-id "$RUN_ID"` performs only the
  persisted read-only `verify_journey` checks and no model call; and (5) the
  artifact scanner, report validation, and upload runs. The three
  `real_model_browser` normal cases are excluded from phase (1), are not
  deterministic registry targets or additional model budget, and run once in
  phase (3). The script always tears down only its unique disposable Compose
  project with `down -v --remove-orphans`.
- The script fails before any model call when the key is empty, the trusted
  branch guard fails, `/models` lacks the exact model, or the stack is not
  healthy. It never invokes `agentMvp.ts` because that fixture intentionally
  soft-fails; the business runner's fixture seed is strict.
- The script must not reorder these phases, run the browser before the
  deterministic report is valid, call `verify_journey` before all three
  browser tests finish, or bypass the scanner/report/upload phase.
  `prepare_journey` owns only Pipeline, Curated approval, the one ontology
  completion, release, descriptors/grant, and model configuration, and
  returns `AgentBindingOptions`; it never creates an Agent or runs a turn.
  `verify_journey` reads only persisted trace, audit, receipt, and budget
  records after the browser phase and never invokes the model.
- `test_business_journey_ci_contract.py` verifies the workflow's job has no
  `continue-on-error`, contains `DEEPSEEK_API_KEY`, exact
  `deepseek-v4-flash-vision-exp`, the trusted same-repository guard, only the
  `pull_request` event, no `pull_request_target`, no model endpoint/base
  override, the one-retry policy, artifact upload with `if: always()`, and
  the exact Playwright spec. It also rejects any new business-journey spec
  containing `test.skip` or `test.fixme`.

- [ ] **Step 1: Write failing CI and script contract tests.**

    ```python
    def test_business_journey_job_is_blocking_and_exact_model():
        workflow = yaml.safe_load(Path(".github/workflows/agent-mvp.yml").read_text())
        job = workflow["jobs"]["business-journey-real-gate"]
        text = yaml.safe_dump(job)
        assert "continue-on-error" not in text
        assert "DEEPSEEK_API_KEY" in text
        assert "deepseek-v4-flash-vision-exp" in text
        workflow_text = Path(".github/workflows/agent-mvp.yml").read_text()
        assert "pull_request_target" not in workflow_text
        events = workflow.get(True, workflow.get("on", {}))
        assert set(events) == {"pull_request"}
        assert "github.event.pull_request.head.repo.full_name" in workflow_text
        assert "github.repository" in workflow_text
        assert "DEEPSEEK_" + "API_BASE" not in workflow_text
        assert "base_" + "url" not in workflow_text

    def test_real_gate_has_no_model_endpoint_override():
        script = Path("scripts/run_business_journey_gate.sh").read_text()
        assert "https://api.deepseek.com" in script
        assert "DEEPSEEK_" + "API_BASE" not in script
        assert "base_" + "url" not in script
        assert "BUSINESS_JOURNEY_API_BASE" in script

    def test_gate_script_fails_without_key_or_with_non_exact_model(monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            run_gate_preflight()

    def test_new_business_journey_browser_spec_has_no_skip_or_fixme():
        text = Path("frontend/src/test/e2e/business-journeys.spec.ts").read_text()
        assert "test.skip" not in text
        assert "test.fixme" not in text
    ```

- [ ] **Step 2: Run the CI contract tests to verify they fail.**

    Run: `cd backend && python -m pytest tests/agent/test_business_journey_ci_contract.py -q`

    Expected: FAIL because the blocking job, strict gate script, exact model
    preflight, and artifact contract do not exist.

- [ ] **Step 3: Wire the blocking trusted-branch workflow.**

    Add a `business-journey-real-gate` job to `.github/workflows/agent-mvp.yml`
    under `pull_request` only. In the first shell step, compare the exact
    pull request head repository (`github.event.pull_request.head.repo.full_name`)
    to the base repository (`github.repository`); exit with
    `TRUSTED_BRANCH_REQUIRED` when they differ. The job must reject any
    invocation whose event is not `pull_request`; do not add a push trigger.
    In the next step, assert `DEEPSEEK_API_KEY` is non-empty and exit with
    `DEEPSEEK_API_KEY_REQUIRED` otherwise. Do not use `pull_request_target`.
    Install the pinned Python/frontend dependencies, run the fixture manifest
    checks, launch the unique isolated test stack, and invoke the strict API
    runner plus the three exact Playwright tests. Keep the existing fake
    `agent_ontology` eval as a fast diagnostic layer. Remove the current
    document-extraction DeepSeek step's role as a gate: either remove that
    `continue-on-error` step or rename it as a clearly diagnostic job that
    cannot satisfy this acceptance contract; it must not be the only live
    model check.

    Upload only `artifacts/evals/business_journeys/**` and redacted
    backend/MCP evidence with `if: always()`; both are produced by the
    scanned, schema-validated `ArtifactAllowlist` path from Task 32 and
    contain the sanitized Playwright trace references and sanitized synthetic
    screenshots already embedded in that object. Never upload
    `frontend/test-results/**` or any other raw Playwright trace/screenshot/log
    output directory as a CI artifact — per the Global Constraints, raw browser
    evidence with source content must never enter artifacts, and the upload
    path accepts only the sanitized allowlist object, not the raw test-results
    tree. Before upload, run the artifact scanner and fail on any forbidden
    content. Verify the JSON reporter has no skipped tests and
    exactly these statuses: `supply_chain=passed`, `finance=passed`, and
    `credit=passed`. The model budget must be checked as exactly `3` logical
    model calls and at most `6` HTTP attempts per journey, hence exactly `9`
    logical calls and at most `18` HTTP attempts for all three. A timeout/429
    retry increases `http_attempts` only and never permits a fourth logical
    call.

- [ ] **Step 4: Run the full real gate locally and inspect artifacts.**

    Run:

    ```bash
    REPO_ROOT="$(git rev-parse --show-toplevel)"
    test -n "${DEEPSEEK_API_KEY:-}"
    (cd "$REPO_ROOT" && bash scripts/run_business_journey_gate.sh)
    (cd "$REPO_ROOT/backend" && python -m pytest tests/agent/test_business_journey_ci_contract.py -q)
    (cd "$REPO_ROOT/frontend" && npx playwright test --config playwright.config.ts src/test/e2e/business-journeys.spec.ts --list)
    ```

    Expected: the gate performs the exact `/models` preflight, makes no
    fallback call, completes all three real-model API chains, runs all three
    browser journeys without a skip, emits only redacted artifacts, and
    cleans its unique Compose project. Missing key, fork head, model mismatch,
    second retry failure, semantic predicate failure, any skipped test, or
    any artifact scanner finding exits non-zero.

- [ ] **Step 5: Commit the blocking gate.**

    ```bash
    git add scripts/run_business_journey_gate.sh backend/tests/agent/test_business_journey_ci_contract.py .github/workflows/agent-mvp.yml backend/evals/document_extraction/run.py frontend/src/test/e2e/playwright.config.ts test_data/runtime/README.md README.md README_zh.md
    git commit -m "ci: block trusted PRs on real business journeys"
    ```

## Spec Coverage and Non-goals Check

The implementation sequence covers the design in this order:

- Product boundary, source refresh policy, authoritative source state, durable cursor/lease/fencing/configuration-revision/idempotency/inbox state, best-effort cancellation and safe-point rule, Python/Celery execution topology, queue isolation, backpressure/operability, T+1 schedules, bounded polling/event ingestion, retry/DLQ/replay, and refresh status/cancellation operations: Tasks 1 and 6–10. (Ordered CDC/sequence-partition delivery and the Python-versus-alternate-runtime capacity/extraction decision, originally Task 10A, are deferred per the Milestone 2/3 Scope Amendment and are non-goals of this plan.)
- Pipeline and semantic foundation, separate immutable release/snapshot contracts, quality/evidence, full dataset-version and pipeline-run lineage: Tasks 1, 11, and 12.
- Trusted registered Agent/service identity, credential-derived user delegation, audience, scope, TTL, revocation, and same-security-domain checks: Task 13.
- Effective capability ∩ entitlement ∩ runtime policy, structured ALLOW/DENY, stable reason codes, evidence, rules, empty-result distinction, and snapshot-pinned investigation/action plans: Tasks 14 and 15.
- Versioned REST/API, Python SDK v1, MCP OAuth adapters, built-in reference Agent, compatibility paths, and tiered transport normalization/hash parity (REST/SDK byte-identical, MCP/reference-Agent normalized-decision-identical): Tasks 16–19.
- Refresh freshness/lag/cursor/lineage propagation, stale/unknown ALLOW/DENY/HITL routing, and historical evidence immutability: Task 20.
- Python remains the Phase 2 control and governed execution plane, with FastAPI request-only persistence/authentication and Celery worker execution; queue-bound Compose reference roles and graceful interruption: Tasks 2, 6A, 8, 9, and 28.
- Snapshot-backed Sandbox boundary and immutable simulation output: Task 22.
- Managed binding, frozen target/parameters, before-image/version hashes, canonical plan_hash, secret exclusion, and drift rejection: Tasks 21 and 26.
- Automatic, human-approved, and rejected policy classes; exact-plan HITL; expiry and revalidation: Tasks 23 and 26.
- PostgreSQL/MySQL parameterized single-target updates, minimum privilege, dialect transactions/timeouts, exact row counts, optimistic locking, idempotency, fencing, audit, reconciliation, and governed rollback plan: Tasks 24–26.
- Governed Sandbox/approval/execute/reconciliation/rollback REST endpoints with RuntimeContext and structured DENY responses: Task 26A.
- Operator refresh/runtime lineage, diff/approval/receipt/reconciliation surfaces, durable cancellation visibility, and full normal/edge/security/Playwright evidence: Tasks 1, 10, 26A, 27, and 28.
- Three complete supply-chain, finance, and credit journeys, including fixed multimodal inputs, Curated review, real exact-model ontology publication, MCP descriptors/grants, Agent release/tool/model binding, browser dialogue, semantic/citation/tool/audit assertions, automatic Sandbox execution, and HITL approve/reject/expire/writeback outcomes: Tasks 29–33.
- Real-model operational contract, exact `/models` preflight, one timeout/429 retry, no fallback, trusted-branch secret guard, redacted success/failure artifacts, fixed model/tool budgets, and zero-skip browser enforcement: Tasks 30 and 33.

The following remain explicit non-goals: generic Agent orchestration, chat UX replacement, separate MCP policy, release-only evidence, arbitrary code/prompt/tool sandboxing, Agent-supplied SQL or secrets, arbitrary SQL/DDL, multi-target transactions, destructive deletes, arbitrary Kafka/broker sources or a general stream-processing platform, a repository-owned CDC producer/broker, broad connector expansion, Python-to-Go/Java/Rust rewrites, Kubernetes/autoscaling implementation, and unrelated refactoring. Production CDC is gated on enterprise source availability, network, credentials, retention, schema compatibility, and operations ownership.

## Evidence-Based Plan Self-Review

Before committing or handing this plan to a coding agent, verify from the current checkout that:

- `backend/app/tasks/celery_app.py` has no `task_routes`/`task_queues` contract and includes `app.tasks.extraction`, so Task 2/6A explicitly repair the stale app entry point and add named routes rather than assuming queue isolation already exists.
- `docker-compose.v2.yml` has one `celery_worker`/beat path and invokes `app.tasks.extraction`; Task 2/6A explicitly replace that path with `app.tasks.celery_app` and declare queue-bound profiles.
- `docker-compose.agent.yml` has dispatcher/artifact/beat/watchdog/sweeper role names but no refresh `-Q` route; Task 6A explicitly binds queues and adds refresh/interactive roles.
- `test_data/runtime/db/docker-compose.yml` is a database-only fixture (PostgreSQL/MySQL) used by the dialect integration tests in Tasks 8, 24, and 25; it does not provide Redis or live refresh workers, and this plan does not add them — the live-queue-consumption capacity harness (originally Task 10A) is deferred per the Milestone 2/3 Scope Amendment, not implemented here. Task 33 adds a separate unique-stack real-model/browser journey gate and does not reuse the soft-failing `agentMvp.ts` seeder.
- `backend/app/routers/v2/connections.py` currently dispatches from the request path and `backend/app/tasks/v2/connection_sync.py` is a stub; Task 7/10 define the durable-run-first, run-ID-only dispatch and connector-free FastAPI test.
- `backend/app/tasks/v2/pipeline_run.py` currently accepts a pipeline/run pair and can load a fixed version; Task 8 retains the explicit pinned-input contract and places materialization in workers.
- Task 6 now owns the durable `cancel_requested`/`cancelled` state and `request_refresh_cancellation`; Tasks 8/9 check cancellation at connector/event/materialization boundaries, while Task 10/27 expose the amendment's best-effort `already_terminal` result. Task 33 independently owns the strict real-model journey gate and its artifact/skip checks.

The following consistency checks are mandatory and have no placeholder outcome:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
(cd "$REPO_ROOT" && rg -n "app\.tasks\.extraction|task_routes|task_queues|celery_worker|dispatcher|artifact_worker" backend/app/tasks/celery_app.py docker-compose.v2.yml docker-compose.agent.yml)
PLACEHOLDER_A='TO''DO'
PLACEHOLDER_B='T''BD'
PLACEHOLDER_C='add'" appropriate"
PLACEHOLDER_D='write tests for the '"above"
(cd "$REPO_ROOT" && rg -n "$PLACEHOLDER_A|$PLACEHOLDER_B|$PLACEHOLDER_C|$PLACEHOLDER_D" docs/superpowers/plans/2026-08-26-agent-semantic-infrastructure-implementation.md)
(cd "$REPO_ROOT" && python - <<'PY'
from pathlib import Path
import re

plan = Path("docs/superpowers/plans/2026-08-26-agent-semantic-infrastructure-implementation.md").read_text()
assert "TO" + "DO" not in plan and "T" + "BD" not in plan
assert plan.index("### Task 6: Define durable source refresh contracts") < plan.index("### Task 6A: Align the Python/Celery execution topology") < plan.index("### Task 7: Persist T+1 schedules") < plan.index("### Task 10A: Python refresh capacity/extraction decision") < plan.index("### Task 11: Add the immutable SemanticSnapshot schema")
for name in ("refresh.schedule", "refresh.poll", "refresh.event", "refresh.replay", "artifact.extraction", "agent.interactive", "housekeeping"):
    assert plan.count(name) >= 3, name
assert "RefreshDispatchMessage" in plan and "enqueue_refresh_run" in plan
assert "refresh_schedule_worker" in plan and "refresh_poll_worker" in plan and "refresh_event_worker" in plan and "refresh_replay_worker" in plan
assert "agent.interactive_probe" in plan
assert "request_refresh_cancellation" in plan and "finalize_refresh_cancellation" in plan
assert "cancel_requested_at" in plan and "already_terminal" in plan
assert "test_refresh_cancellation_dialects.py" in plan
# Milestone 2/3 Scope Amendment: these identifiers named a mechanism this plan
# cut/deferred (capacity harness, sequence-partition ordering, the precise
# cancellation-marker error types). They may still appear once, in the
# amendment's own explanation of what was removed and in Task 6's scope note
# — never as an active field/interface a later task relies on. Checked as a
# bounded count, not a hard absence, against everything before this
# self-review section (whose own source necessarily names them too).
plan_before_self_review = plan[: plan.index("## Evidence-Based Plan Self-Review")]
for removed, max_mentions in (
    ("CapacityMeasurement", 0), ("run_refresh_capacity", 0),
    ("CapacityQueueConsumption", 0), ("InteractiveProbeConsumption", 0), ("QueueIsolationEvidence", 0),
    ("RefreshPartitionState", 3), ("sequence_partition", 2),
    ("outcome_committed_at", 6), ("CANCELLATION_TOO_LATE", 1), ("CANCELLATION_NOT_APPLICABLE", 1),
):
    count = plan_before_self_review.count(removed)
    assert count <= max_mentions, f"{removed} appears {count} times; expected at most {max_mentions} explanatory mention(s) after the Milestone 2/3 Scope Amendment cut it"
assert plan.index("RefreshDispatchMessage` is an immutable") < plan.index("enqueue_refresh_run")
assert plan.index("request_refresh_cancellation(db: Session") < plan.index("cancel_refresh_run(db: Session")
assert plan.index("finalize_refresh_cancellation(db: Session") < plan.index("Celery task `refresh.poll")
assert plan.index("assert_refresh_not_cancelled(db: Session") < plan.index("Celery task `refresh.poll")
assert plan.index("CancelRefreshRequest` contains exactly") < plan.index("GET /api/v2/refresh/sources/{source_id}/status`, `POST")
assert plan.index("## Milestone 2/3 Scope Amendment") < plan.index("### Task 6: Define durable source refresh contracts")
assert plan.index("### Task 28: Run the integrated refresh") < plan.index("## Workstream 4") < plan.index("### Task 29: Version the three multimodal journey corpora")
print("plan coverage/placeholder/type-order self-review passed")
PY
)
```

The self-review must report the current evidence, task ordering (including the Milestone 2/3 Scope Amendment preceding Task 6 and Workstream 4 following Task 28), absence of placeholders, exact queue names, run-ID dispatch interface, cancellation functions and API, confirmed absence of the deferred/cut capacity and sequence-partition/precise-cancellation-marker identifiers, and definition-before-use ordering before the plan is considered ready. No code, Compose file, design document, or unrelated user change is modified by this plan update.
