import { apiClient } from './client'
import type { OntologyBinding } from './agentTools'

export interface AgentDetail {
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

export interface AgentVersion {
  id: string
  version_no: number
  name: string
  description?: string | null
  config_hash: string
  default_model_config_version_id?: string | null
  default_model_name?: string | null
  system_prompt?: string | null
  memory_settings?: Record<string, unknown> | null
  application_state_schema_version_id?: string | null
  change_note?: string | null
  prompt_generation_id?: string | null
  created_by?: string | null
  created_at?: string | null
  ontology_bindings?: OntologyBinding[] | null
}

export interface CatalogModel {
  id: string
  name: string
  provider?: string | null
  version_no?: number | null
  behavior_hash?: string | null
}

export interface SaveVersionResult {
  version_id: string
  version_no: number
  config_hash: string
}

export interface CreateAgentResult {
  agent_id: string
  version_id: string
  version_no: number
  config_hash: string
}

export interface PromptGenerationRecord {
  id: string
  status: string
  base_version_no?: number | null
  model_config_version_id?: string | null
  model_name?: string | null
  input_hash?: string | null
  output_hash?: string | null
  output_text?: string | null
  requester_id?: string | null
  requested_at?: string | null
  accepted_at?: string | null
}

let idempotencyCounter = 0

/** Agent mutation idempotency key: ^[\x21-\x7e]{16,128}$ printable ASCII. */
export function newAgentIdempotencyKey(): string {
  idempotencyCounter += 1
  return `ag-${Date.now().toString(36)}-${idempotencyCounter.toString(36).padStart(9, '0')}`
}

export interface BasicPatch {
  base_version_no: number
  name: string
  description?: string | null
  default_model_config_version_id: string
  default_model_name: string
  system_prompt?: string | null
  memory_settings?: Record<string, unknown>
  application_state_schema_version_id?: string | null
  change_note?: string | null
  prompt_generation_id?: string | null
  ontology_bindings?: OntologyBinding[] | null
}

export const agentDetailApi = {
  get: (agentId: string) => apiClient.get<AgentDetail>(`/agents/${agentId}`),
  versions: (agentId: string) =>
    apiClient.get<{ items: AgentVersion[]; next_cursor: string | null; has_more: boolean }>(`/agents/${agentId}/versions`),
  create: (body: {
    name: string
    description?: string | null
    default_model_config_version_id: string
    default_model_name: string
    system_prompt?: string | null
    memory_settings?: Record<string, unknown>
    ontology_bindings?: OntologyBinding[]
  }) =>
    apiClient.post<CreateAgentResult>('/agents', body, { headers: { 'Idempotency-Key': newAgentIdempotencyKey() } }),
  saveVersion: (agentId: string, body: BasicPatch) =>
    apiClient.post<SaveVersionResult>(`/agents/${agentId}/versions`, body, {
      headers: { 'Idempotency-Key': newAgentIdempotencyKey() },
    }),
  catalogModels: () => apiClient.get<{ items: CatalogModel[]; next_cursor: string | null; has_more: boolean }>('/agents/catalog/models'),
  generatePrompt: (agentId: string, body: {
    base_version_no: number
    model_config_version_id: string
    model_name: string
    input_text: string
  }) =>
    apiClient.post<PromptGenerationRecord>(`/agents/${agentId}/prompt-generations`, body, {
      headers: { 'Idempotency-Key': newAgentIdempotencyKey() },
    }),
  decidePrompt: (agentId: string, generationId: string, decision: 'accepted' | 'rejected') =>
    apiClient.post<PromptGenerationRecord>(`/agents/${agentId}/prompt-generations/${generationId}/decision`, { decision }),
}
