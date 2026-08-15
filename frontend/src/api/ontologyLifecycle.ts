import { apiClient } from './client'
import type { OntologyStatus } from '@/types/ontology'

export interface LifecycleReceipt {
  ontology_id: string
  status?: string
  runtime_disabled?: boolean
}

export interface OntologyReleaseSummary {
  id: string
  version_no: number
  version: string
  created_by?: string | null
  created_at?: string | null
}

export interface OntologyReleaseDetail extends OntologyReleaseSummary {
  ontology_id: string
  schema_hash: string
  manifest_projection?: Record<string, unknown> | null
}

export interface PublishReceipt {
  ontology_id?: string
  release?: { version_no: number; version: string } | null
  schema_hash?: string
  entities?: unknown[]
  relations?: unknown[]
  [key: string]: unknown
}

let idempotencyCounter = 0

/** P1C-API idempotency key: ^[\x21-\x7e]{16,128}$ printable ASCII. */
export function newIdempotencyKey(): string {
  idempotencyCounter += 1
  return `pub-${Date.now().toString(36)}-${idempotencyCounter.toString(36).padStart(9, '0')}`
}

export const ontologyLifecycleApi = {
  markCreated: (ontologyId: string) =>
    apiClient.post<LifecycleReceipt>(
      `/ontologies/${ontologyId}/mark-created`,
      {},
      { headers: { 'Idempotency-Key': newIdempotencyKey() } },
    ),
  publish: (ontologyId: string, body: { base_working_revision?: number; changelog?: string }) =>
    apiClient.post<PublishReceipt>(
      `/ontologies/${ontologyId}/publish`,
      body,
      { headers: { 'Idempotency-Key': newIdempotencyKey() } },
    ),
  archive: (ontologyId: string, reason?: string) =>
    apiClient.post<LifecycleReceipt>(`/ontologies/${ontologyId}/archive`, { reason }),
  runtimeDisable: (ontologyId: string, reason?: string) =>
    apiClient.post<LifecycleReceipt>(`/ontologies/${ontologyId}/runtime-disable`, { reason }),
  runtimeEnable: (ontologyId: string, reason?: string) =>
    apiClient.post<LifecycleReceipt>(`/ontologies/${ontologyId}/runtime-enable`, { reason }),
  listReleases: (ontologyId: string) =>
    apiClient.get<{ items: OntologyReleaseSummary[]; next_cursor: string | null; has_more: boolean }>(
      `/ontologies/${ontologyId}/releases`,
    ),
  getRelease: (ontologyId: string, releaseId: string) =>
    apiClient.get<OntologyReleaseDetail>(`/ontologies/${ontologyId}/releases/${releaseId}`),
}

export function displayStatus(status: OntologyStatus | string | undefined): string {
  switch (status) {
    case 'draft': return '草稿'
    case 'creating': return '创建中'
    case 'created': return '已创建'
    case 'published': return '已发布'
    case 'archived': return '已归档'
    default: return status ?? '未知'
  }
}
