import { apiClient } from './client'
import { newAgentIdempotencyKey } from './agentDetail'

export interface SkillCatalogItem {
  skill_version_id: string
  package_id: string
  version_no: number
  package_name: string
}

export interface SkillBinding {
  id: string
  alias: string
  skill_version_id: string
  package_id: string
  version_no: number
  package_name: string
  approval_status: string
}

export interface BindSkillPayload {
  skill_version_id: string
  alias: string
}

export const agentSkillsApi = {
  listCatalog: () => apiClient.get<{ items: SkillCatalogItem[] }>('/agents/catalog/skills'),
  listBindings: (agentId: string, versionId: string) =>
    apiClient.get<{ items: SkillBinding[] }>(`/agents/${agentId}/versions/${versionId}/skills`),
  bind: (agentId: string, versionId: string, body: BindSkillPayload) =>
    apiClient.post<{ id: string; alias: string; skill_version_id: string }>(
      `/agents/${agentId}/versions/${versionId}/skills`, body,
      { headers: { 'Idempotency-Key': newAgentIdempotencyKey() } },
    ),
  unbind: (agentId: string, versionId: string, alias: string) =>
    apiClient.delete<{ released: boolean }>(
      `/agents/${agentId}/versions/${versionId}/skills/${encodeURIComponent(alias)}`,
    ),
}
