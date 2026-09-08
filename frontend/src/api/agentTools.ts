import { apiClient } from './client'

export interface PublishedOntology {
  id: string
  name: string
  status: string
}

export interface ToolDescriptor {
  descriptor_id: string
  version: number
  source_kind: 'builtin' | 'logic' | 'action' | 'mcp'
  source_id: string
  input_schema?: Record<string, unknown>
  output_schema?: Record<string, unknown>
  capability: string
  timeout_ms: number
  result_limit: number
  descriptor_hash: string
  category?: ToolCategory
}

export type ToolCategory = 'mcp' | 'query' | 'write' | 'logic' | 'action'

export const TOOL_CATEGORIES: ToolCategory[] = ['mcp', 'query', 'write', 'logic', 'action']

export interface OntologyTools {
  ontology_id: string
  published: boolean
  release_id: string | null
  tools: ToolDescriptor[]
}

export interface OntologyBinding {
  ontology_id: string
  capabilities: string[]
  allowlists: Record<string, unknown>
  selected_tools: string[]
  /** Category-level enablement (MCP/query/write/logic/action). Absent/null
   * keeps the legacy selected_tools-only filter; a present list (possibly
   * empty) switches the runtime to category-mode filtering. */
  enabled_categories?: ToolCategory[] | null
}

export interface ToolValidationRequest {
  ontology_ids: string[]
}

export interface ToolValidationResult {
  valid: boolean
  blocked?: string[] | null
  capabilities?: string[] | null
}

export const TOOL_CAPABILITY_GROUPS: Record<string, { label: string; fallback: string; capabilities: string[] }> = {
  query: { label: 'agent.tools.tool_query', fallback: '查询（实例检索）', capabilities: ['read_instances'] },
  logic: { label: 'agent.tools.tool_logic', fallback: 'Logic 规则', capabilities: ['execute_read_logic'] },
  action: { label: 'agent.tools.tool_action', fallback: '实例 Action', capabilities: ['execute_instance_action'] },
}

export const agentToolsApi = {
  listPublishedOntologies: () =>
    apiClient.get<{ items: PublishedOntology[]; next_cursor: string | null; has_more: boolean }>('/agents/catalog/ontologies'),
  validateAgentTools: (agentId: string, body: ToolValidationRequest) =>
    apiClient.post<ToolValidationResult>(`/agents/${agentId}/tool-validation`, body),
  listOntologyTools: (ontologyId: string) =>
    apiClient.get<OntologyTools>(`/ontologies/${ontologyId}/tools`),
}
