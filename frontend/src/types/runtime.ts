/**
 * Wire types for the governed Runtime REST surface (Task 16/21/22/23/26/26A,
 * `backend/app/routers/v2/runtime.py`). These mirror the router's own
 * `_serialize_*` functions and `app/schemas/runtime.py` field-for-field —
 * this file carries no logic, only shape.
 */

export interface EvidenceCitation {
  source_id: string
  source_type: string
  locator: string
  content_hash: string
  refresh_run_id?: string | null
}

export interface RuleOutcome {
  rule_id: string
  result: string
  reason_code: string
}

export interface InvestigationRequest {
  semantic_snapshot_id: string
  query?: string | null
  ontology_id: string
  entity_type?: string | null
  filters?: Record<string, unknown>
  limit?: number
}

/** A denial (`decision === 'DENY'`) never carries `result`; an ALLOW always
 * carries a collection (possibly empty) — never confuse the two. */
export interface InvestigationResult {
  decision: 'ALLOW' | 'DENY'
  reason_code: string
  semantic_snapshot_id: string
  ontology_release_id: string
  evidence_citations: EvidenceCitation[]
  rule_outcome: RuleOutcome[]
  result: unknown[] | null
  correlation_id: string
  freshness_state?: string | null
  freshness_lag_seconds?: number | null
  source_cursor?: Record<string, unknown> | null
}

export interface ActionPlan {
  id: string
  semantic_snapshot_id: string
  ontology_release_id: string
  agent_id: string
  user_id: string
  action_id: string
  input_facts: Record<string, unknown>
  evidence_citations: EvidenceCitation[]
  rule_outcomes: RuleOutcome[]
  managed_action_binding_id: string | null
  binding_version: string | null
  parameters: Record<string, unknown>
  target_key: unknown[]
  before_image_hash: string
  version_hash: string
  predicted_diff: Record<string, unknown>
  impact_scope: Record<string, unknown>
  risk_classification: string
  policy_decision: Record<string, unknown>
  precondition_hashes: string[]
  expiry: string
  idempotency_key: string
  plan_hash: string
}

export interface SandboxResult {
  simulation_id: string
  action_plan_id: string
  semantic_snapshot_id: string
  expected_rows: number
  before_after_diff: Record<string, unknown>
  impact_summary: Record<string, unknown>
  rule_outcome: Record<string, unknown>[]
  policy_result: Record<string, unknown>
  precondition_hashes: Record<string, unknown>
  expires_at: string
}

export interface ApprovalReceipt {
  plan_id: string
  plan_hash: string
  approver_agent_id: string
  approver_user_id: string
  approved_at: string
  execution_class: string
  expiry: string
  correlation_id: string
}

export interface ExecutionReceipt {
  execution_id: string
  plan_id: string
  plan_hash: string
  status: string
  execution_class: string
  dialect: string
  writer_receipt: Record<string, unknown> | null
  audit_id: string
  idempotency_key: string
  reconciliation_case_id: string | null
}

/** `GET /execution-status/{plan_id}` returns this stable "not started" shape
 * instead of an `ExecutionReceipt` when no execution has ever been attempted. */
export interface ExecutionNotStarted {
  plan_id: string
  status: 'not_started'
  correlation_id: string
}

export interface ReconciliationCase {
  id: string
  execution_id: string
  plan_id: string
  status: 'UNKNOWN' | 'SUCCEEDED' | 'FAILED'
  unknown_reason: string | null
  observed_effect: Record<string, unknown>
  next_action: string | null
  created_at: string
}

/** Every structured Runtime denial (credential- or service-layer) is this
 * one shape (`app.routers.v2.runtime._denial_body`); never a caller-parsed
 * plain HTTP error. */
export interface RuntimeDenial {
  decision: 'DENY'
  reason_code: string
  correlation_id: string | null
  semantic_snapshot_id: string | null
  ontology_release_id: string | null
  result: null
}
