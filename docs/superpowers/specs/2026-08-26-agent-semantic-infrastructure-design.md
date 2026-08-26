# Ontexus: Agent Semantic Infrastructure Design

## Status

Approved product-positioning design. This document defines product boundaries
and stabilization priorities; it does not authorize an implementation plan by
itself.

## Product definition

**Ontexus is enterprise infrastructure for Agent-assisted business decisions
and governed execution.** It turns enterprise data into versioned, governed
domain semantics, then exposes a common runtime that enterprise-owned
Copilots and Agents can use to investigate, decide, and act safely.

The primary user is the data or ontology engineer. Business experts
collaborate to validate the domain model and approve material actions, while
the enterprise remains free to supply its own Copilot or Agent experience.

## Product boundary

Ontexus is not a general-purpose Agent Builder, chat product, or replacement
for an enterprise's existing Copilot UX. Agent configuration and the built-in
Agent UI remain useful as a reference implementation, integration verifier,
and operator surface, but they are not the product's defining layer.

The core promise is:

> Any enterprise Agent can access business facts and perform business actions
> only through the current semantic, identity, and governance boundaries.

## Architecture

```text
Enterprise data
  -> Data & Semantic Foundation
  -> Decision Runtime
  -> Governed Execution
  -> Enterprise Copilot / Agent
```

### Data & Semantic Foundation

Owns the reliable business context:

- Data connections, pipeline execution, transformations, data quality, and
  version lineage.
- Ontologies containing entities, relations, rules, actions, evidence, and
  publication state.
- Immutable releases that identify the semantic and data contract used by a
  decision or action.

### Decision Runtime

Owns governed decision support:

- Semantic investigation, retrieval, explanation, and evidence retrieval.
- Domain-rule evaluation and action eligibility.
- A single contract exposed by API/SDK and MCP. Transports must not have
  divergent policy or business behavior.
- Delegated Semantic Access: every request carries both an Agent/service
  identity and a delegated end-user identity.

The effective permission is the intersection of:

```text
Agent capability ∩ user entitlement ∩ runtime policy
```

Agent capability controls what integrations, ontology capabilities, tools,
and actions an Agent may invoke. User entitlement controls the data scope and
business authorization of the represented person. Runtime policy evaluates
the active ontology release, rule constraints, data state, and risk controls.

### Governed Execution

Owns the transition from a proposed operation to a production change:

- A unified, versioned Sandbox evaluates semantic changes and Agent behavior
  against isolated or snapshot-backed data.
- Sandbox output is an immutable action plan: input snapshot, ontology and
  policy versions, dual identities, evidence, predicted diff, impact scope,
  expiry, and idempotency key.
- A production writer revalidates identities, policy, version and data
  preconditions before execution, then records the outcome and reconciliation
  evidence.

## Risk-based execution policy

Every production action is classified by risk and determinism.

| Class | Conditions | Outcome |
| --- | --- | --- |
| Automatic | Low risk, reversible, deterministic, policy-approved, and within delegated access | Execute without per-action HITL; retain full audit and reconciliation. |
| Human-approved | High impact, irreversible, sensitive, ambiguous, or above policy threshold | Require HITL approval of the exact immutable action plan. |
| Rejected | Missing authorization, stale input/version, failed precondition, or failed Sandbox validation | Do not execute. Return a structured explanation. |

HITL approval grants execution of one exact plan; it does not grant an Agent
ongoing broad write authority. Plans expire and must be rebuilt if their
semantic, data, policy, identity, or relevant production preconditions change.

## Canonical execution flow

```text
Investigate -> Sandbox -> immutable action plan -> policy decision
  -> automatic execution OR HITL approval -> production writeback
  -> audit + reconciliation
```

Production writeback may target approved enterprise systems or production
databases only through managed connectors/actions. Direct, ungoverned Agent
writes are out of scope.

## Stabilization priorities

The current release should stabilize contracts before expanding Agent-facing
features:

1. Make the documented Docker quick-start, migration contract, frontend build,
   and schema tests green and mutually consistent.
2. Treat published ontology releases, evidence, pipeline lineage, and data
   quality as first-class Runtime inputs.
3. Keep API/SDK and MCP as thin transports over one Semantic Runtime.
4. Make Sandbox/action-plan/policy/approval/writeback/audit a single
   traceable workflow.
5. Retain the built-in Agent application as a reference client and operations
   surface; avoid positioning it as a separate Agent platform.

## Non-goals for this stabilization cycle

- Competing with generic multi-Agent orchestration or chat application
  platforms.
- Adding a separate business-policy implementation for MCP versus API/SDK.
- Allowing production writes without delegated identity, policy evaluation,
  idempotency, and audit evidence.
- Broad refactoring that does not improve release correctness, the shared
  Runtime contract, or governed execution.
