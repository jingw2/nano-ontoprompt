import { apiClient } from './client'

export interface AgentSession {
  id: string
  agent_id: string
  owner_user_id: string
  status: string
  active_turn_id?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface AgentMessage {
  id: string
  session_id: string
  turn_id?: string | null
  role: 'user' | 'assistant' | 'system' | 'tool'
  ordinal: number
  content?: string | null
  created_at?: string | null
}

export const agentSessionsApi = {
  list: (agentId: string) =>
    apiClient.get<{ items: AgentSession[]; next_cursor: string | null; has_more: boolean }>(`/agents/${agentId}/sessions`),
  create: (agentId: string, title?: string) =>
    apiClient.post<AgentSession>(`/agents/${agentId}/sessions`, { title }),
  messages: (sessionId: string) =>
    apiClient.get<{ items: AgentMessage[]; next_cursor: string | null; has_more: boolean }>(`/agent-sessions/${sessionId}/messages`),
}
