import { apiClientV2 } from '@/api/client'

export interface Dataset { id: string; name: string; kind: string }

const datasetsApi = {
  list: (kind?: string) => apiClientV2.get<Dataset[]>('/datasets', { params: kind ? { kind } : {} }),
  get: (id: string) => apiClientV2.get<Dataset>(`/datasets/${id}`),
  versions: (id: string) => apiClientV2.get(`/datasets/${id}/versions`),
  preview: (id: string, versionNo: number, limit = 100) =>
    apiClientV2.get(`/datasets/${id}/versions/${versionNo}/preview`, { params: { limit } }),
}

export default datasetsApi
