import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClientV2 } from './client'
import { refreshApi } from './refresh'

/**
 * Task 27: proves every `refreshApi` method maps to exactly one durable
 * refresh REST endpoint (`backend/app/routers/v2/refresh.py`) — the
 * operator identity for `cancel` comes from the authenticated session on
 * the backend; the client never sends a lease/fence/cursor/source/payload,
 * and `replay` never sends a cursor, source URL, credential, broker, or
 * event payload.
 */
describe('refreshApi', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('maps refresh status, schedule, trigger, cancellation, and replay without accepting cursor or broker data', async () => {
    const get = vi.spyOn(apiClientV2, 'get').mockResolvedValue({})
    const put = vi.spyOn(apiClientV2, 'put').mockResolvedValue({})
    const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({})

    await refreshApi.getStatus('source-001')
    await refreshApi.setSchedule('source-001', { cron_expr: '0 2 * * *', timezone: 'Asia/Shanghai', enabled: true })
    await refreshApi.trigger('source-001', { mode: 'micro_batch' })
    await refreshApi.cancel('run-running-001', 'planned source maintenance')
    await refreshApi.replay('run-dead-001', 'dlq-001')

    expect(get).toHaveBeenCalledWith('/refresh/sources/source-001/status')
    expect(put).toHaveBeenCalledWith('/refresh/sources/source-001/schedule', { cron_expr: '0 2 * * *', timezone: 'Asia/Shanghai', enabled: true })
    expect(post).toHaveBeenCalledWith('/refresh/sources/source-001/run', { mode: 'micro_batch' })
    expect(post).toHaveBeenCalledWith('/refresh/runs/run-running-001/cancel', { reason: 'planned source maintenance' })
    expect(post).toHaveBeenCalledWith('/refresh/runs/run-dead-001/replay', { dead_letter_id: 'dlq-001' })
  })

  it('cancel sends only { reason } — never a lease, fence, cursor, source, or payload', async () => {
    const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({})
    await refreshApi.cancel('run-running-001', 'planned source maintenance')
    const body = post.mock.calls[0][1]
    expect(Object.keys(body as object)).toEqual(['reason'])
  })
})
