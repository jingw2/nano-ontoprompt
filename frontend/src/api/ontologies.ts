import { apiClient } from './client'
import type { OntologyListItem, OntologyDetail, Entity, LogicRule, Action, UploadedFile, Prompt, ModelConfig } from '@/types/ontology'

export const ontologyApi = {
  list: (params?: { name?: string; page?: number; page_size?: number }) =>
    apiClient.get<{ items: OntologyListItem[]; total: number; page: number; page_size: number }>('/ontologies', { params }),
  create: (body: { name: string; domain: string; description?: string; build_mode?: string }) =>
    apiClient.post<OntologyDetail>('/ontologies', body),
  get: (id: string) => apiClient.get<OntologyDetail>(`/ontologies/${id}`),
  update: (id: string, body: Partial<OntologyDetail>) => apiClient.put<OntologyDetail>(`/ontologies/${id}`, body),
  delete: (id: string) => apiClient.delete(`/ontologies/${id}`),

  // Files
  listFiles: (oid: string) => apiClient.get<UploadedFile[]>(`/ontologies/${oid}/files`),
  deleteFile: (oid: string, fid: string) => apiClient.delete(`/ontologies/${oid}/files/${fid}`),

  // Graph
  getGraph: (oid: string) => apiClient.get<{ nodes: object[]; edges: object[]; meta: object }>(`/ontologies/${oid}/graph`),
  createRelation: (oid: string, body: object) => apiClient.post(`/ontologies/${oid}/graph/relations`, body),
  deleteRelation: (oid: string, rid: string) => apiClient.delete(`/ontologies/${oid}/graph/relations/${rid}`),

  // Entities
  listEntities: (oid: string) => apiClient.get<Entity[]>(`/ontologies/${oid}/entities`),
  createEntity: (oid: string, body: Partial<Entity>) => apiClient.post<Entity>(`/ontologies/${oid}/entities`, body),
  updateEntity: (oid: string, eid: string, body: Partial<Entity>) => apiClient.put<Entity>(`/ontologies/${oid}/entities/${eid}`, body),
  deleteEntity: (oid: string, eid: string) => apiClient.delete(`/ontologies/${oid}/entities/${eid}`),
  getEntityRelated: (oid: string, eid: string) =>
    apiClient.get<{ logic: any[]; actions: any[] }>(`/ontologies/${oid}/entities/${eid}/related`),

  // Logic
  listLogic: (oid: string) => apiClient.get<LogicRule[]>(`/ontologies/${oid}/logic`),
  createLogic: (oid: string, body: Partial<LogicRule>) => apiClient.post<LogicRule>(`/ontologies/${oid}/logic`, body),
  updateLogic: (oid: string, lid: string, body: Partial<LogicRule>) => apiClient.put<LogicRule>(`/ontologies/${oid}/logic/${lid}`, body),
  deleteLogic: (oid: string, lid: string) => apiClient.delete(`/ontologies/${oid}/logic/${lid}`),

  // Actions
  listActions: (oid: string) => apiClient.get<Action[]>(`/ontologies/${oid}/actions`),
  createAction: (oid: string, body: Partial<Action>) => apiClient.post<Action>(`/ontologies/${oid}/actions`, body),
  updateAction: (oid: string, aid: string, body: Partial<Action>) => apiClient.put<Action>(`/ontologies/${oid}/actions/${aid}`, body),
  deleteAction: (oid: string, aid: string) => apiClient.delete(`/ontologies/${oid}/actions/${aid}`),

  // Extraction
  startExtraction: (oid: string, body: { prompt_id: string; model_id: string; model_name: string; constraints?: string[] }) =>
    apiClient.post<{ task_id: string }>(`/ontologies/${oid}/execute`, body),
  getExtractionStatus: (oid: string, task_id: string) =>
    apiClient.get(`/ontologies/${oid}/execute/status?task_id=${task_id}`),

  // Export (must use authenticated request — plain links omit Bearer token)
  exportOntology: async (oid: string, format: string) => {
    const blob = (await apiClient.get(`/ontologies/${oid}/export`, {
      params: { format },
      responseType: 'blob',
    })) as unknown as Blob
    if (blob.type.includes('json')) {
      const err = JSON.parse(await blob.text())
      throw err
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ontology_${oid}.${format}`
    a.click()
    URL.revokeObjectURL(url)
  },

  // Audit
  startAudit: (oid: string, body: { model_id: string; model_name: string }) =>
    apiClient.post<{ task_id: string }>(`/ontologies/${oid}/audit`, body),
  getAuditStatus: (oid: string, task_id: string) =>
    apiClient.get(`/ontologies/${oid}/audit/status?task_id=${task_id}`),
}

export const promptApi = {
  list: (domain?: string) => apiClient.get<Prompt[]>('/prompts', { params: domain ? { domain } : {} }),
  getTemplates: () => apiClient.get<{ name: string; domain: string; content: string }[]>('/prompts/templates'),
  create: (body: Partial<Prompt>) => apiClient.post<Prompt>('/prompts', body),
  get: (id: string) => apiClient.get<Prompt>(`/prompts/${id}`),
  update: (id: string, body: Partial<Prompt>) => apiClient.put<Prompt>(`/prompts/${id}`, body),
  delete: (id: string) => apiClient.delete(`/prompts/${id}`),
  generateTemplate: (domain: string) =>
    apiClient.post<{ domain: string; content: string }>(`/prompts/generate-template?domain=${encodeURIComponent(domain)}&style=ontology_extraction`, {}),
}

export const modelApi = {
  list: () => apiClient.get<ModelConfig[]>('/models'),
  create: (body: Partial<ModelConfig> & { api_key?: string }) => apiClient.post<ModelConfig>('/models', body),
  get: (id: string) => apiClient.get<ModelConfig>(`/models/${id}`),
  update: (id: string, body: Partial<ModelConfig> & { api_key?: string }) => apiClient.put<ModelConfig>(`/models/${id}`, body),
  delete: (id: string) => apiClient.delete(`/models/${id}`),
  test: (id: string) => apiClient.post(`/models/${id}/test`),
}

export const settingsApi = {
  getRules: () => apiClient.get<{ rule_key: string; rule_value: string; rule_label_cn: string; rule_label_en: string; editable: boolean }[]>('/settings/rules'),
  updateRules: (rules: { rule_key: string; rule_value: string }[]) => apiClient.put('/settings/rules', rules),
}

export const usersApi = {
  list: () => apiClient.get<{ id: string; username: string; email: string; role: string; created_at: string }[]>('/users'),
  create: (body: { username: string; email: string; password: string; role: string }) =>
    apiClient.post('/users', body),
  update: (id: string, body: { username?: string; email?: string; password?: string; role?: string }) =>
    apiClient.put(`/users/${id}`, body),
  delete: (id: string) => apiClient.delete(`/users/${id}`),
}
