import { apiClientV2 } from './client'

export interface ToolProvider {
  id: string
  name: string
  kind: string
  status: string
}

export interface ToolConnection {
  id: string
  provider_id: string
  status: string
  active_version_id: string | null
}

export interface ToolConnectionVersion {
  id: string
  connection_id: string
  version_no: number
  endpoint: string | null
  audience: string | null
  scopes: string[]
  credential_reference: string | null
  allowlists: Record<string, unknown>
  approval_status: 'pending' | 'approved' | 'rejected'
  health_status: 'healthy' | 'unhealthy' | 'unknown'
  created_by: string
  created_at: string
}

export interface CreateConnectionVersionPayload {
  connection_id: string
  endpoint?: string
  audience?: string
  scopes?: string[]
  credential_reference?: string
  allowlists?: Record<string, unknown>
}

export interface IssueMcpTokenPayload {
  access_token: string
  refresh_token?: string
  expires_in_seconds: number
  scope: string[]
  audience?: string
}

export const PROVIDER_KINDS = ['search', 'playwright', 'external_mcp', 'skill', 'ontology_mcp'] as const
export const LIVE_PROVIDER_KINDS = ['search', 'playwright', 'external_mcp'] as const

export const toolConnectionsApi = {
  listProviders: () => apiClientV2.get<{ items: ToolProvider[] }>('/tool-providers'),
  createProvider: (name: string, kind: string) =>
    apiClientV2.post<ToolProvider>('/tool-providers', { name, kind }),
  listConnections: (providerId?: string) =>
    apiClientV2.get<{ items: ToolConnection[] }>(
      `/tool-connections${providerId ? `?provider_id=${encodeURIComponent(providerId)}` : ''}`,
    ),
  createConnection: (providerId: string) =>
    apiClientV2.post<ToolConnection>('/tool-connections', { provider_id: providerId }),
  listVersions: (connectionId: string) =>
    apiClientV2.get<{ items: ToolConnectionVersion[] }>(`/tool-connections/${connectionId}/versions`),
  createVersion: (payload: CreateConnectionVersionPayload) =>
    apiClientV2.post<ToolConnectionVersion>('/tool-connections/versions', payload),
  approveVersion: (versionId: string) =>
    apiClientV2.post<{ id: string; approval_status: string }>(`/tool-connections/versions/${versionId}/approve`),
  activateVersion: (connectionId: string, versionId: string) =>
    apiClientV2.post<{ connection_id: string; active_version_id: string }>('/tool-connections/activate', {
      connection_id: connectionId, version_id: versionId,
    }),
  testVersion: (versionId: string) =>
    apiClientV2.post<{ status: string; detail: string }>(`/tool-connections/versions/${versionId}/test`),
  pinMcpSchema: (versionId: string) =>
    apiClientV2.post<{ connection_version_id: string; tool_schema_hash: string; tool_count: number }>(
      `/tool-connections/versions/${versionId}/mcp/pin-schema`,
    ),
  issueMcpToken: (versionId: string, payload: IssueMcpTokenPayload) =>
    apiClientV2.post<{ connection_version_id: string; scope: string[]; expires_in_seconds: number }>(
      `/tool-connections/versions/${versionId}/mcp/token`, payload,
    ),
}
