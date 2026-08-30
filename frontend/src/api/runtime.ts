import { apiClientV2 } from './client'
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
    apiClientV2.post<InvestigationResult>('/runtime/investigate', request),

  getActionPlan: (planId: string) => apiClientV2.get<ActionPlan>(`/runtime/action-plans/${planId}`),

  getSandboxSimulation: (planId: string) =>
    apiClientV2.get<SandboxResult>(`/runtime/action-plans/${planId}/sandbox`),

  approveActionPlan: (planId: string, planHash: string) =>
    apiClientV2.post<ApprovalReceipt>(`/runtime/action-plans/${planId}/approve`, { plan_hash: planHash }),

  // Sends ONLY { plan_hash } — never a selector or parameters override,
  // matching the backend's own ExecuteRequestBody `extra="forbid"` contract.
  executeActionPlan: (planId: string, planHash: string) =>
    apiClientV2.post<ExecutionReceipt>(`/runtime/action-plans/${planId}/execute`, { plan_hash: planHash }),

  getExecutionStatus: (planId: string) =>
    apiClientV2.get<ExecutionReceipt | ExecutionNotStarted>(`/runtime/execution-status/${planId}`),

  getReconciliation: (reconciliationId: string) =>
    apiClientV2.get<ReconciliationCase>(`/runtime/reconciliations/${reconciliationId}`),

  createRollbackPlan: (executionId: string) =>
    apiClientV2.post<ActionPlan>(`/runtime/executions/${executionId}/rollback-plans`, {}),
}
