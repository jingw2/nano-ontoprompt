import { apiClientV2, runtimeApiClient } from './client'
import type {
  ActionPlan,
  ApprovalReceipt,
  ExecutionNotStarted,
  ExecutionReceipt,
  InvestigationRequest,
  InvestigationResult,
  ReconciliationCase,
  SandboxResult,
} from '@/types/runtime'

export interface RuntimeDelegationAgent {
  id: string
  client_name: string
  allowed_scopes: string[]
}

export interface RuntimeDelegation {
  token: string
  expires_in: number
}

export const runtimeDelegationApi = {
  listAgents: () => apiClientV2.get<RuntimeDelegationAgent[]>('/runtime/delegation-agents'),
  issue: (agentId: string, scopes: string[]) =>
    apiClientV2.post<RuntimeDelegation>('/runtime/delegations', { agent_id: agentId, scopes }),
}

/**
 * Task 27: thin REST adapters for the governed Runtime surface
 * (`backend/app/routers/v2/runtime.py`). Each method does exactly one HTTP
 * call to its documented endpoint — no client-side authorization, risk
 * evaluation, hash computation, or write-target construction. The backend
 * (`RuntimeService`/`execute_plan`/etc.) is the sole source of truth for all
 * of that; this module only maps method calls to paths/bodies.
 */
export const runtimeApi = {
  investigate: (request: InvestigationRequest) =>
    runtimeApiClient.post<InvestigationResult>('/runtime/investigate', request),

  getActionPlan: (planId: string) => runtimeApiClient.get<ActionPlan>(`/runtime/action-plans/${planId}`),

  getSandboxSimulation: (planId: string) =>
    runtimeApiClient.get<SandboxResult>(`/runtime/action-plans/${planId}/sandbox`),

  approveActionPlan: (planId: string, planHash: string) =>
    runtimeApiClient.post<ApprovalReceipt>(`/runtime/action-plans/${planId}/approve`, { plan_hash: planHash }),

  // Sends ONLY { plan_hash } — never a selector or parameters override,
  // matching the backend's own ExecuteRequestBody `extra="forbid"` contract.
  executeActionPlan: (planId: string, planHash: string) =>
    runtimeApiClient.post<ExecutionReceipt>(`/runtime/action-plans/${planId}/execute`, { plan_hash: planHash }),

  getExecutionStatus: (planId: string) =>
    runtimeApiClient.get<ExecutionReceipt | ExecutionNotStarted>(`/runtime/execution-status/${planId}`),

  getReconciliation: (reconciliationId: string) =>
    runtimeApiClient.get<ReconciliationCase>(`/runtime/reconciliations/${reconciliationId}`),

  createRollbackPlan: (executionId: string) =>
    runtimeApiClient.post<ActionPlan>(`/runtime/executions/${executionId}/rollback-plans`, {}),
}
