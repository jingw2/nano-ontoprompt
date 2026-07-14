import { apiClientV2 } from '@/api/client'

export interface Pipeline {
  id: string
  name: string
  domain?: string
  description?: string
  source_dataset_id?: string | null
  route?: string | null
  spec?: Record<string, unknown>
  definition?: { nodes: unknown[]; edges: unknown[] } | null
  status: string
  branch?: string
  version?: number
  target_curated_ids?: string[]
  created_at?: string | null
  updated_at?: string | null
}

export interface PipelineCreateBody {
  name: string
  domain?: string
  description?: string
  source_dataset_id?: string | null
  route?: string | null
  spec?: Record<string, unknown>
  definition?: { nodes: unknown[]; edges: unknown[] } | null
}

export interface PipelineRunItem {
  id: string
  status: string
  started_at: string | null
  finished_at: string | null
}

export interface RunDetail {
  id: string
  status: string
  stats: Record<string, unknown> | null
  error_log: string | null
  started_at: string | null
  finished_at: string | null
}

export interface ValidateResult {
  valid: boolean
  errors: Array<{ node_id: string; severity: string; message: string }>
  warnings: Array<{ node_id: string; severity: string; message: string }>
}

const pipelinesApi = {
  /** Pipeline CRUD */
  list: (params?: { search?: string; domain?: string; status?: string }) =>
    apiClientV2.get<Pipeline[]>('/pipelines', { params }).then(r => r),
  get: (id: string) =>
    apiClientV2.get<Pipeline>(`/pipelines/${id}`).then(r => r),
  create: (body: PipelineCreateBody) =>
    apiClientV2.post<Pipeline>('/pipelines', body).then(r => r),
  update: (id: string, body: Partial<PipelineCreateBody> & { status?: string }) =>
    apiClientV2.put<Pipeline>(`/pipelines/${id}`, body).then(r => r),
  delete: (id: string) =>
    apiClientV2.delete(`/pipelines/${id}`).then(r => r),

  /** Validate & Publish */
  validate: (id: string) =>
    apiClientV2.post<ValidateResult>(`/pipelines/${id}/validate`).then(r => r),
  publish: (id: string) =>
    apiClientV2.post<{ id: string; status: string; version: number }>(`/pipelines/${id}/publish`).then(r => r),
  versions: (id: string) =>
    apiClientV2.get<Array<{ id: string; version: number; status: string; created_at: string | null }>>(`/pipelines/${id}/versions`).then(r => r),

  /** Runs */
  run: (id: string) =>
    apiClientV2.post<{ run_id: string; status: string }>(`/pipelines/${id}/run`).then(r => r),
  runSync: (id: string) =>
    apiClientV2.post<{ run_id: string; status: string; stats?: Record<string, unknown> }>(`/pipelines/${id}/run-sync`).then(r => r),
  runs: (id: string) =>
    apiClientV2.get<PipelineRunItem[]>(`/pipelines/${id}/runs`).then(r => r),
  getRun: (runId: string) =>
    apiClientV2.get<RunDetail>(`/pipelines/runs/${runId}`).then(r => r),
}

export default pipelinesApi
