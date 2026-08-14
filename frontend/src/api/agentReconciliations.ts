import { apiClient } from './client'

export interface ReconciliationCase {
  id: string
  turn_id: string
  execution_kind: string
  execution_id: string
  revision: number
  state: string
  request_hash?: string | null
}

export interface ReconciliationResolution {
  case_id: string
  state: string
  resolution: string
  resumed: boolean
}

export type ResolutionKind = 'succeeded' | 'failed' | 'retry'

export const agentReconciliationsApi = {
  list: (status?: string) =>
    apiClient.get<{ items: ReconciliationCase[]; next_cursor: string | null; has_more: boolean }>(
      `/admin/agent-reconciliations${status ? `?status=${encodeURIComponent(status)}` : ''}`,
    ),
  resolve: (caseId: string, baseRevision: number, resolution: ResolutionKind, evidence: string) =>
    apiClient.post<ReconciliationResolution>(`/admin/agent-reconciliations/${caseId}/resolve`, {
      base_revision: baseRevision,
      resolution,
      evidence,
    }),
}
