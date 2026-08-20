import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { useAuthStore } from '@/stores/authStore'
import ActionApprovalCard from './ActionApprovalCard'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  useAuthStore.getState().logout()
})
afterAll(() => server.close())

const APPROVAL = {
  id: 'ap-1', turn_id: 't-1', tool_execution_id: 'te-1',
  designated_actor_id: 'u-1', revision: 2, status: 'pending',
  preview_hash: 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
  expires_at: null, stale_reason: null,
}

function setActor(id: string) {
  useAuthStore.getState().setAuth(
    { id, username: 'u', email: 'u@t.com', role: 'editor', is_active: true, created_at: '2026-01-01T00:00:00Z' },
    'token',
  )
}

describe('P5-UI', () => {
  it('P5-UI red contract', () => {
    const failures: string[] = []
    for (const p of [
      'src/api/agentApprovals.ts',
      'src/pages/agents/application/ActionApprovalCard.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P5_UI: ' + failures.join('; '))
  })

  it('designated actor approves with the exact immutable revision and hash', async () => {
    setActor('u-1')
    let approveBody: unknown = null
    server.use(
      http.get('*/api/v1/agent-approvals/ap-1', () =>
        HttpResponse.json({ data: APPROVAL, message: 'ok' })),
      http.post('*/api/v1/agent-approvals/ap-1/approve', async ({ request }) => {
        approveBody = await request.json()
        return HttpResponse.json({
          data: { approval_id: 'ap-1', turn_id: 't-1', status: 'queued', dispatch_generation: 3, correlation_id: 'c-1' },
          message: 'ok',
        }, { status: 202 })
      }),
    )
    render(<ActionApprovalCard approvalId="ap-1" onApprovalResolved={() => {}} />)
    await screen.findByTestId('action-approval-card')
    expect(screen.getByTestId('approval-preview-hash').textContent).toContain('deadbeefdeadbeef')
    expect(screen.getByTestId('approval-revision').textContent).toBe('2')
    await userEvent.click(screen.getByTestId('approve-action'))
    await waitFor(() => expect(approveBody).not.toBeNull())
    expect(approveBody).toEqual({ base_revision: 2, preview_hash: APPROVAL.preview_hash })
    expect(await screen.findByTestId('approval-result')).toBeTruthy()
    expect(screen.getByTestId('approval-result-status')).toBeTruthy()
  })

  it('non-designated actors see a read-only awaiting state (no local authority)', async () => {
    setActor('u-2')
    server.use(
      http.get('*/api/v1/agent-approvals/ap-1', () =>
        HttpResponse.json({ data: APPROVAL, message: 'ok' })),
    )
    render(<ActionApprovalCard approvalId="ap-1" onApprovalResolved={() => {}} />)
    expect(await screen.findByTestId('approval-awaiting-actor')).toBeTruthy()
    expect(screen.queryByTestId('approve-action')).toBeNull()
    expect(screen.queryByTestId('reject-action')).toBeNull()
  })

  it('renders stale/expired approvals with refresh and rejects with conflict reload', async () => {
    setActor('u-1')
    server.use(
      http.get('*/api/v1/agent-approvals/ap-1', () =>
        HttpResponse.json({ data: { ...APPROVAL, status: 'stale', stale_reason: 'APPROVAL_STALE' }, message: 'ok' })),
    )
    const { unmount } = render(<ActionApprovalCard approvalId="ap-1" onApprovalResolved={() => {}} />)
    expect(await screen.findByTestId('approval-stale')).toBeTruthy()
    expect(screen.getByText('APPROVAL_STALE')).toBeTruthy()
    unmount()

    let getCount = 0
    server.use(
      http.get('*/api/v1/agent-approvals/ap-1', () => {
        getCount += 1
        return HttpResponse.json({ data: APPROVAL, message: 'ok' })
      }),
      http.post('*/api/v1/agent-approvals/ap-1/reject', () =>
        HttpResponse.json({ error: { code: 'APPROVAL_STALE' }, correlation_id: 'x' }, { status: 409 })),
    )
    render(<ActionApprovalCard approvalId="ap-1" onApprovalResolved={() => {}} />)
    await screen.findByTestId('action-approval-card')
    await userEvent.click(screen.getByTestId('reject-action'))
    await waitFor(() => expect(getCount).toBeGreaterThanOrEqual(2))
  })
})
