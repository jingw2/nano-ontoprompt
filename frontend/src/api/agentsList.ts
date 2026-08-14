import { apiClient } from './client'

export interface AgentListItem {
  agent_id: string
  status: string
  visibility: string
  name?: string | null
  version_no?: number | null
  config_hash?: string | null
  versions_count: number
}

export interface AgentListPage {
  items: AgentListItem[]
  next_cursor: string | null
  has_more: boolean
}

export const agentsListApi = {
  list: (params?: { search?: string; domain?: string; status?: string; page?: number }) =>
    apiClient.get<AgentListPage>('/agents', { params }),
  archive: (agentId: string, reason?: string) =>
    apiClient.post<{ agent_id: string; status: string }>(`/agents/${agentId}/archive`, { reason }),
}
