import { apiClientV2 } from './client'
import type {
  RefreshCancelResult,
  RefreshRunView,
  RefreshScheduleRequest,
  RefreshScheduleView,
  RefreshStatus,
  RefreshTriggerRequest,
} from '@/types/refresh'

/**
 * Task 27: thin REST adapters for the durable refresh operations surface
 * (`backend/app/routers/v2/refresh.py`). Each method does exactly one HTTP
 * call to its documented endpoint. The operator identity for `cancel` comes
 * from the authenticated session on the backend (`require_editor`); the
 * client never sends a lease/fence/cursor/source/payload, and `replay`
 * never sends a cursor, source URL, credential, broker, or event payload.
 */
export const refreshApi = {
  getStatus: (sourceId: string) => apiClientV2.get<RefreshStatus>(`/refresh/sources/${sourceId}/status`),

  getSchedule: (sourceId: string) => apiClientV2.get<RefreshScheduleView>(`/refresh/sources/${sourceId}/schedule`),

  setSchedule: (sourceId: string, request: RefreshScheduleRequest) =>
    apiClientV2.put<RefreshScheduleView>(`/refresh/sources/${sourceId}/schedule`, request),

  // Sends only the persisted policy/mode and a bounded backfill window.
  trigger: (sourceId: string, request: RefreshTriggerRequest) =>
    apiClientV2.post<RefreshRunView>(`/refresh/sources/${sourceId}/run`, request),

  cancel: (runId: string, reason: string) =>
    apiClientV2.post<RefreshCancelResult>(`/refresh/runs/${runId}/cancel`, { reason }),

  replay: (runId: string, deadLetterId?: string) =>
    apiClientV2.post<RefreshRunView>(`/refresh/runs/${runId}/replay`, { dead_letter_id: deadLetterId }),
}
