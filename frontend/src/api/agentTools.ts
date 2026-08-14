import { apiClient } from './client'

export interface PublishedOntology {
  id: string
  name: string
  status: string
}

export interface ToolValidationRequest {
  ontology_ids: string[]
}

export interface ToolValidationResult {
  valid: boolean
  blocked?: string[] | null
  capabilities?: string[] | null
}

export const agentToolsApi = {
  listPublishedOntologies: () =>
    apiClient.get<{ items: PublishedOntology[]; next_cursor: string | null; has_more: boolean }>('/agents/catalog/ontologies'),
  validateAgentTools: (agentId: string, body: ToolValidationRequest) =>
    apiClient.post<ToolValidationResult>(`/agents/${agentId}/tool-validation`, body),
}
