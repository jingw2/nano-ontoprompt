import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClientV2 } from './client'
import { runtimeApi } from './runtime'

/**
 * Task 27: proves every `runtimeApi` method maps to exactly one governed
 * Runtime REST endpoint (`backend/app/routers/v2/runtime.py`) with the
 * exact path/body the backend expects — no client-side authorization,
 * risk evaluation, hash computation, or write-target construction.
 */
describe('runtimeApi', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('maps every governed runtime operation to its REST endpoint', async () => {
    const get = vi.spyOn(apiClientV2, 'get').mockResolvedValue({})
    const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({})

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
    const get = vi.spyOn(apiClientV2, 'get').mockResolvedValue({})
    const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({})

    await runtimeApi.investigate({ semantic_snapshot_id: 'snap-valid-001', ontology_id: 'onto-1' })
    await runtimeApi.getActionPlan('plan-auto-001')
    await runtimeApi.getExecutionStatus('plan-auto-001')

    expect(post).toHaveBeenCalledWith('/runtime/investigate', { semantic_snapshot_id: 'snap-valid-001', ontology_id: 'onto-1' })
    expect(get).toHaveBeenCalledWith('/runtime/action-plans/plan-auto-001')
    expect(get).toHaveBeenCalledWith('/runtime/execution-status/plan-auto-001')
  })

  it('sends exact-hash execute with only plan_hash — never a selector or parameters override', async () => {
    const post = vi.spyOn(apiClientV2, 'post').mockResolvedValue({})
    await runtimeApi.executeActionPlan('plan-hitl-001', 'plan-hash-hitl-001')
    const body = post.mock.calls[0][1]
    expect(Object.keys(body as object)).toEqual(['plan_hash'])
  })
})
