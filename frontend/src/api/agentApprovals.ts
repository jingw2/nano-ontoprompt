import { apiClient } from './client'

export interface ApprovalRecord {
  id: string
  turn_id: string
  tool_execution_id: string
  designated_actor_id: string
  revision: number
  status: string
  preview_hash: string
  expires_at?: string | null
  stale_reason?: string | null
}

export interface ApprovalResolutionResult {
  approval_id: string
  turn_id: string
  status: string
  dispatch_generation: number
  correlation_id: string
  stream_ticket?: string | null
  stream_ticket_url?: string | null
}

export const agentApprovalsApi = {
  get: (approvalId: string) =>
    apiClient.get<ApprovalRecord>(`/agent-approvals/${approvalId}`),
  approve: (approvalId: string, baseRevision: number, previewHash: string) =>
    apiClient.post<ApprovalResolutionResult>(`/agent-approvals/${approvalId}/approve`, {
      base_revision: baseRevision,
      preview_hash: previewHash,
    }),
  reject: (approvalId: string, baseRevision: number, previewHash: string) =>
    apiClient.post<ApprovalResolutionResult>(`/agent-approvals/${approvalId}/reject`, {
      base_revision: baseRevision,
      preview_hash: previewHash,
    }),
}
