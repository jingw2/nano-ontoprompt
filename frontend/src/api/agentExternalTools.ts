import { apiClient } from './client'
import { newAgentIdempotencyKey } from './agentDetail'

export interface ExternalToolCatalogItem {
  tool_connection_version_id: string
  connection_id: string
  version_no: number
  provider_id: string
  provider_name: string
  provider_kind: 'search' | 'playwright' | 'external_mcp'
  health_status: 'healthy' | 'unhealthy' | 'unknown'
}

export interface ExternalToolBinding {
  id: string
  alias: string
  tool_connection_version_id: string
  connection_id: string
  version_no: number
  provider_name: string
  provider_kind: string
  approval_status: string
  health_status: string
}

export interface BindExternalToolPayload {
  tool_connection_version_id: string
  alias: string
}

export const agentExternalToolsApi = {
  listCatalog: () => apiClient.get<{ items: ExternalToolCatalogItem[] }>('/agents/catalog/external-tools'),
  listBindings: (agentId: string, versionId: string) =>
    apiClient.get<{ items: ExternalToolBinding[] }>(`/agents/${agentId}/versions/${versionId}/external-tools`),
  bind: (agentId: string, versionId: string, body: BindExternalToolPayload) =>
    apiClient.post<{ id: string; alias: string; tool_connection_version_id: string }>(
      `/agents/${agentId}/versions/${versionId}/external-tools`, body,
      { headers: { 'Idempotency-Key': newAgentIdempotencyKey() } },
    ),
  unbind: (agentId: string, versionId: string, alias: string) =>
    apiClient.delete<{ released: boolean }>(
      `/agents/${agentId}/versions/${versionId}/external-tools/${encodeURIComponent(alias)}`,
    ),
}
