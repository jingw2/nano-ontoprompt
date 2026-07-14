import { apiClientV2 } from '@/api/client'

export interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
  quality_score: number | null
}

export interface CuratedPreview {
  dataset_id: string
  name: string
  rows: Record<string, string>[]
  count: number
  error?: string
}

export interface ReviewSession {
  review_id: string
  status: string
}

const curatedApi = {
  list: () => apiClientV2.get<CuratedDataset[]>('/curated'),
  get: (id: string) => apiClientV2.get<CuratedDataset>(`/curated/${id}`),
  preview: (id: string, limit = 200) =>
    apiClientV2.get<CuratedPreview>(`/curated/${id}/preview?limit=${limit}`),
  quality: (id: string) => apiClientV2.get(`/curated/${id}/quality`),

  /** Quick approve/reject (no review session needed) */
  approve: (id: string, notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=approve&notes=${encodeURIComponent(notes)}`),
  reject: (id: string, notes = '') =>
    apiClientV2.post(`/curated/${id}/review?action=reject&notes=${encodeURIComponent(notes)}`),

  /** Delete a curated dataset (admin only) */
  delete: (id: string) => apiClientV2.delete(`/curated/${id}`),

  /** Start a review session for row-level edits */
  startReview: (id: string) =>
    apiClientV2.post<ReviewSession>(`/curated/${id}/reviews`),

  /** Save batch edits within a review session */
  saveEdits: (reviewId: string, edits: Array<{ row_pk: string; field_name: string; old_value: string; new_value: string }>) =>
    apiClientV2.post<{ saved: number }>(`/curated/reviews/${reviewId}/edits`, { edits }),

  /** Approve/reject a review session */
  approveReview: (reviewId: string, notes = '') =>
    apiClientV2.post(`/curated/reviews/${reviewId}/approve?notes=${encodeURIComponent(notes)}`),
  rejectReview: (reviewId: string, notes = '') =>
    apiClientV2.post(`/curated/reviews/${reviewId}/reject?notes=${encodeURIComponent(notes)}`),
}

export default curatedApi
