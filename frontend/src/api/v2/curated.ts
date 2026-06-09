import { apiClientV2 } from '@/api/client'

export interface CuratedDataset {
  id: string
  name: string
  status: string
  quality_score: number | null
}

const curatedApi = {
  list: () => apiClientV2.get<CuratedDataset[]>('/curated'),
  get: (id: string) => apiClientV2.get<CuratedDataset>(`/curated/${id}`),
  preview: (id: string) => apiClientV2.get(`/curated/${id}/preview`),
  quality: (id: string) => apiClientV2.get(`/curated/${id}/quality`),
  review: (id: string, action: 'approve' | 'reject', notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=${action}&notes=${encodeURIComponent(notes)}`),
}

export default curatedApi
