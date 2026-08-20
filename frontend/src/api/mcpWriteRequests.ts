import { apiClient } from './client'

export interface McpWriteRequestItem {
  id: string
  ontology_id: string
  release_id: string
  descriptor_id: string
  target_instance_id: string | null
  parameters: Record<string, unknown>
  preview_hash: string
  preview_canonical: string
  status: string
  created_at: string
  resolved_at: string | null
}

export interface McpWriteRequestResolution {
  id: string
  status: string
}

export const mcpWriteRequestsApi = {
  list: () => apiClient.get<{ items: McpWriteRequestItem[] }>('/mcp/write-requests'),
  approve: (id: string) => apiClient.post<McpWriteRequestResolution>(`/mcp/write-requests/${id}/approve`),
  reject: (id: string) => apiClient.post<McpWriteRequestResolution>(`/mcp/write-requests/${id}/reject`),
}
