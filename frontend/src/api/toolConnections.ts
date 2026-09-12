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
  name?: string | null
  active_version_endpoint?: string | null
  active_version_search_provider?: SearchProvider | null
}

export type SearchProvider = 'generic' | 'bing' | 'google' | 'serper' | 'brave'

export interface ToolConnectionVersion {
  id: string
  connection_id: string
  version_no: number
  endpoint: string | null
  audience: string | null
  scopes: string[]
  allowlists: Record<string, unknown>
  search_provider: SearchProvider | null
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
  search_provider?: SearchProvider
}

export interface UpdateConnectionVersionPayload {
  endpoint?: string
  audience?: string
  scopes?: string[]
  credential_reference?: string
  allowlists?: Record<string, unknown>
  search_provider?: SearchProvider
}

/** Known real search-API request shapes an admin can pick without having to
 * hand-assemble the right endpoint/auth contract themselves (see
 * app/services/tools/search.py — each shape differs in auth placement,
 * header name, and HTTP method, not just response format). `endpoint` is a
 * starting template the admin still edits (Google's needs a real `cx`
 * engine id substituted in). `labelKey`/`noteKey` resolve via i18n; the
 * paired `label`/`note` values are the fallback shown if the key is ever
 * missing (matching this codebase's t(key, fallback) convention). */
export const SEARCH_PROVIDER_PRESETS: Record<SearchProvider, {
  labelKey: string; label: string; endpoint: string; noteKey: string; note: string
}> = {
  generic: {
    labelKey: 'toolConnections.search_provider_generic', label: '自定义（通用）', endpoint: '',
    noteKey: 'toolConnections.search_provider_generic_note', note: 'GET 请求，密钥通过 Authorization: Bearer 头传递',
  },
  bing: {
    labelKey: 'toolConnections.search_provider_bing', label: 'Bing Web Search API v7',
    endpoint: 'https://api.bing.microsoft.com/v7.0/search',
    noteKey: 'toolConnections.search_provider_bing_note', note: '密钥通过 Ocp-Apim-Subscription-Key 头传递',
  },
  google: {
    labelKey: 'toolConnections.search_provider_google', label: 'Google Custom Search JSON API',
    endpoint: 'https://www.googleapis.com/customsearch/v1?cx=YOUR_SEARCH_ENGINE_ID',
    noteKey: 'toolConnections.search_provider_google_note', note: '请将 cx 替换为你的搜索引擎 ID；密钥作为 key 查询参数传递',
  },
  serper: {
    labelKey: 'toolConnections.search_provider_serper', label: 'Serper.dev',
    endpoint: 'https://google.serper.dev/search',
    noteKey: 'toolConnections.search_provider_serper_note', note: 'POST 请求，密钥通过 X-API-KEY 头传递',
  },
  brave: {
    labelKey: 'toolConnections.search_provider_brave', label: 'Brave Search API',
    endpoint: 'https://api.search.brave.com/res/v1/web/search',
    noteKey: 'toolConnections.search_provider_brave_note', note: '密钥通过 X-Subscription-Token 头传递',
  },
}

export interface IssueMcpTokenPayload {
  access_token: string
  refresh_token?: string
  expires_in_seconds: number
  scope: string[]
  audience?: string
}

export const PROVIDER_KINDS = ['search', 'playwright', 'external_mcp', 'skill', 'ontology_mcp', 'browser_use'] as const
export const LIVE_PROVIDER_KINDS = ['search', 'playwright', 'external_mcp', 'browser_use'] as const

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
  renameConnection: (connectionId: string, name: string) =>
    apiClientV2.put<{ id: string; name: string }>(`/tool-connections/${connectionId}`, { name }),
  listVersions: (connectionId: string) =>
    apiClientV2.get<{ items: ToolConnectionVersion[] }>(`/tool-connections/${connectionId}/versions`),
  createVersion: (payload: CreateConnectionVersionPayload) =>
    apiClientV2.post<ToolConnectionVersion>('/tool-connections/versions', payload),
  updateVersion: (versionId: string, payload: UpdateConnectionVersionPayload) =>
    apiClientV2.put<{ id: string; approval_status: string }>(`/tool-connections/versions/${versionId}`, payload),
  deleteVersion: (versionId: string) =>
    apiClientV2.delete<{ id: string; deleted: boolean }>(`/tool-connections/versions/${versionId}`),
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
