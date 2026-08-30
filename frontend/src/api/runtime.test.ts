import { afterEach, describe, expect, it, vi } from 'vitest'
import { afterAll, beforeAll } from 'vitest'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { apiClientV2, runtimeApiClient } from './client'
import { runtimeApi, runtimeDelegationApi } from './runtime'
import { useAuthStore } from '@/stores/authStore'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterAll(() => server.close())

/**
 * Task 27: proves every `runtimeApi` method maps to exactly one governed
 * Runtime REST endpoint (`backend/app/routers/v2/runtime.py`) with the
 * exact path/body the backend expects — no client-side authorization,
 * risk evaluation, hash computation, or write-target construction.
 */
describe('runtimeApi', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    server.resetHandlers()
    useAuthStore.setState({ user: null, token: null })
  })

  it('maps every governed runtime operation to its REST endpoint', async () => {
    const get = vi.spyOn(runtimeApiClient, 'get').mockResolvedValue({})
    const post = vi.spyOn(runtimeApiClient, 'post').mockResolvedValue({})

    await runtimeApi.getSandboxSimulation('plan-auto-001')
    await runtimeApi.approveActionPlan('plan-hitl-001', 'plan-hash-hitl-001')
    await runtimeApi.executeActionPlan('plan-hitl-001', 'plan-hash-hitl-001')
    await runtimeApi.getReconciliation('recon-001')
    await runtimeApi.createRollbackPlan('execution-001')

    expect(get).toHaveBeenCalledWith('/runtime/action-plans/plan-auto-001/sandbox')
    expect(get).toHaveBeenCalledWith('/runtime/reconciliations/recon-001')
    expect(post).toHaveBeenCalledWith('/runtime/action-plans/plan-hitl-001/approve', { plan_hash: 'plan-hash-hitl-001' })
    expect(post).toHaveBeenCalledWith('/runtime/action-plans/plan-hitl-001/execute', { plan_hash: 'plan-hash-hitl-001' })
    expect(post).toHaveBeenCalledWith('/runtime/executions/execution-001/rollback-plans', {})
  })

  it('maps investigate, getActionPlan, and getExecutionStatus', async () => {
    const get = vi.spyOn(runtimeApiClient, 'get').mockResolvedValue({})
    const post = vi.spyOn(runtimeApiClient, 'post').mockResolvedValue({})

    await runtimeApi.investigate({ semantic_snapshot_id: 'snap-valid-001', ontology_id: 'onto-1' })
    await runtimeApi.getActionPlan('plan-auto-001')
    await runtimeApi.getExecutionStatus('plan-auto-001')

    expect(post).toHaveBeenCalledWith('/runtime/investigate', { semantic_snapshot_id: 'snap-valid-001', ontology_id: 'onto-1' })
    expect(get).toHaveBeenCalledWith('/runtime/action-plans/plan-auto-001')
    expect(get).toHaveBeenCalledWith('/runtime/execution-status/plan-auto-001')
  })

  it('sends exact-hash execute with only plan_hash — never a selector or parameters override', async () => {
    const post = vi.spyOn(runtimeApiClient, 'post').mockResolvedValue({})
    await runtimeApi.executeActionPlan('plan-hitl-001', 'plan-hash-hitl-001')
    const body = post.mock.calls[0][1]
    expect(Object.keys(body as object)).toEqual(['plan_hash'])
  })

  it('uses the ordinary session client only to obtain a short-lived runtime delegation', async () => {
    const get = vi.spyOn(apiClientV2, 'get').mockResolvedValue([])
    const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({ token: 'delegated', expires_in: 900 })
    await runtimeDelegationApi.listAgents()
    await runtimeDelegationApi.issue('agent-001', ['ontology:read'])
    expect(get).toHaveBeenCalledWith('/runtime/delegation-agents')
    expect(post).toHaveBeenCalledWith('/runtime/delegations', { agent_id: 'agent-001', scopes: ['ontology:read'] })
  })

  it('keeps the ordinary browser session after a runtime delegation denial', async () => {
    useAuthStore.setState({ user: {
      id: 'user-1', username: 'operator', email: 'operator@example.test', role: 'editor',
      is_active: true, created_at: '2026-08-30T00:00:00Z',
    }, token: 'session-token' })
    server.use(http.post('*/api/v2/runtime/investigate', () =>
      HttpResponse.json({ decision: 'DENY', reason_code: 'EXPIRED_DELEGATION', result: null }, { status: 401 })))

    await expect(runtimeApi.investigate({ semantic_snapshot_id: 'snap-1', ontology_id: 'onto-1' })).rejects.toMatchObject({ decision: 'DENY' })
    expect(useAuthStore.getState().token).toBe('session-token')
  })
})
