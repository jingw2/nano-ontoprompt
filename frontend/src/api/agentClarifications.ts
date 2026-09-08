import { apiClient } from './client'

export interface ClarificationRecord {
  id: string
  turn_id: string
  question: string
  answer?: string | null
  base_request_revision: number
  status: string
  created_at?: string | null
  answered_at?: string | null
}

export interface ClarificationAnswerResult {
  turn_id: string
  session_id: string
  status: string
  dispatch_generation: number
  correlation_id: string
}

export const agentClarificationsApi = {
  get: (clarificationId: string) =>
    apiClient.get<ClarificationRecord>(`/agent-clarifications/${clarificationId}`),
  answer: (clarificationId: string, baseRequestRevision: number, answer: string) =>
    apiClient.post<ClarificationAnswerResult>(`/agent-clarifications/${clarificationId}/answer`, {
      base_request_revision: baseRequestRevision,
      answer,
    }),
}
