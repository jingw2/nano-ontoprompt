import { apiClient } from './client'

export interface MemoryRevision {
  revision_no: number
  display_text: string
  confidence: number
  consent_basis: string
  created_at: string
  superseded_at: string | null
}

export interface MemoryConflictSummary {
  conflict_id: string
  other_memory_id: string
  other_display_text: string
}

export interface MemoryRecord {
  id: string
  subject_key: string
  predicate: string
  display_text: string
  confidence: number
  sensitivity: string
  status: 'pending_confirmation' | 'active' | 'conflicted' | 'deleted'
  consent_basis: string
  created_at: string
  updated_at: string
}

export interface MemoryDetail extends MemoryRecord {
  revisions: MemoryRevision[]
  conflict: MemoryConflictSummary | null
  embedding_status: 'current' | 'pending' | 'never_embedded'
}

export interface ConflictListItem {
  conflict_id: string
  subject_key: string
  predicate: string
  memory_id_a: string
  display_text_a: string
  memory_id_b: string
  display_text_b: string
  created_at: string
}

export const agentMemoriesApi = {
  list: (agentId: string, status?: string) =>
    apiClient.get<{ items: MemoryRecord[] }>(`/agents/${agentId}/memories`, { params: status ? { status } : {} }),
  get: (agentId: string, memoryId: string) =>
    apiClient.get<MemoryDetail>(`/agents/${agentId}/memories/${memoryId}`),
  confirm: (agentId: string, memoryId: string, consent: boolean) =>
    apiClient.post<MemoryDetail>(`/agents/${agentId}/memories/${memoryId}/confirm`, { consent }),
  reject: (agentId: string, memoryId: string) =>
    apiClient.post<{ status: string }>(`/agents/${agentId}/memories/${memoryId}/reject`),
  correct: (agentId: string, memoryId: string, displayText: string, confidence?: number) =>
    apiClient.post<MemoryDetail>(`/agents/${agentId}/memories/${memoryId}/correct`, {
      display_text: displayText, confidence,
    }),
  delete: (agentId: string, memoryId: string) =>
    apiClient.post<{ status: string }>(`/agents/${agentId}/memories/${memoryId}/delete`),
  listConflicts: (agentId: string) =>
    apiClient.get<{ items: ConflictListItem[] }>(`/agents/${agentId}/memories/conflicts`),
  resolveConflict: (agentId: string, conflictId: string, winningMemoryId: string) =>
    apiClient.post<MemoryDetail>(`/agents/${agentId}/memories/conflicts/${conflictId}/resolve`, {
      winning_memory_id: winningMemoryId,
    }),
}
