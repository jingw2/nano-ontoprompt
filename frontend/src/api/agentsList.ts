import { apiClient } from './client'

export interface AgentListItem {
  agent_id: string
  status: string
  visibility: string
  name?: string | null
  version_no?: number | null
  config_hash?: string | null
  versions_count: number
  created_at?: string | null
  can_edit?: boolean
}

export interface AgentListPage {
  items: AgentListItem[]
  next_cursor: string | null
  has_more: boolean
}

export interface AgentListParams {
  q?: string
  id?: string
  name?: string
  created_from?: string
  created_before?: string
  cursor?: string | null
  limit?: number
}

export const agentsListApi = {
  list: (params?: AgentListParams) => apiClient.get<AgentListPage>('/agents', { params }),
  archive: (agentId: string) => apiClient.delete<void>(`/agents/${agentId}`),
}
