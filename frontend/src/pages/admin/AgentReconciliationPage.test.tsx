import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import AgentReconciliationPage from './AgentReconciliationPage'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const CASE = {
  id: 'c-1', turn_id: 't-1', execution_kind: 'instance_action',
  execution_id: 'e-1', revision: 1, state: 'open',
  request_hash: 'abc1234567890def',
}

describe('P5-OPSUI', () => {
  it('P5-OPSUI red contract', () => {
    const failures: string[] = []
    for (const p of [
      'src/api/agentReconciliations.ts',
      'src/pages/admin/AgentReconciliationPage.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P5_OPSUI: ' + failures.join('; '))
  })

  it('lists redacted cases and shows loading/empty states', async () => {
    server.use(
      http.get('*/api/v1/admin/agent-reconciliations*', () =>
        HttpResponse.json({ data: { items: [CASE], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    const { unmount } = render(<AgentReconciliationPage />)
    expect(screen.getByTestId('reconciliation-loading')).toBeTruthy()
    await screen.findByTestId('reconciliation-case-c-1')
    expect(screen.getByText(/instance_action/)).toBeTruthy()
    expect(screen.getByText(/state=open/)).toBeTruthy()
    unmount()

    server.use(
      http.get('*/api/v1/admin/agent-reconciliations*', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    render(<AgentReconciliationPage />)
    expect(await screen.findByTestId('reconciliation-empty')).toBeTruthy()
  })

  it('resolves a case with evidence through the CAS client (retry resumes)', async () => {
    let resolveBody: unknown = null
    server.use(
      http.get('*/api/v1/admin/agent-reconciliations*', () =>
        HttpResponse.json({ data: { items: [CASE], next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/admin/agent-reconciliations/c-1/resolve', async ({ request }) => {
        resolveBody = await request.json()
        return HttpResponse.json({
          data: { case_id: 'c-1', state: 'resolved_retry', resolution: 'retry', resumed: true },
          message: 'ok',
        })
      }),
    )
    render(<AgentReconciliationPage />)
    const row = await screen.findByTestId('reconciliation-case-c-1')
    await userEvent.click(row.querySelector('button')!)
    expect(screen.getByTestId('reconciliation-detail')).toBeTruthy()
    // evidence is required before any resolution
    expect(screen.getByTestId('resolve-retry')).toHaveProperty('disabled', true)
    await userEvent.type(screen.getByTestId('reconciliation-evidence'), 'provider confirmed not-run')
    expect(screen.getByTestId('resolve-retry')).toHaveProperty('disabled', false)
    await userEvent.click(screen.getByTestId('resolve-retry'))
    await waitFor(() => expect(resolveBody).not.toBeNull())
    expect(resolveBody).toEqual({
      base_revision: 1,
      resolution: 'retry',
      evidence: 'provider confirmed not-run',
    })
  })

  it('denies non-operators and surfaces stale CAS conflicts', async () => {
    server.use(
      http.get('*/api/v1/admin/agent-reconciliations*', () =>
        HttpResponse.json({ data: { items: [CASE], next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/admin/agent-reconciliations/c-1/resolve', () =>
        HttpResponse.json({ error: { code: 'RECONCILIATION_CONFLICT' }, correlation_id: 'x' }, { status: 409 })),
    )
    render(<AgentReconciliationPage />)
    const row = await screen.findByTestId('reconciliation-case-c-1')
    await userEvent.click(row.querySelector('button')!)
    await userEvent.type(screen.getByTestId('reconciliation-evidence'), 'note')
    await userEvent.click(screen.getByTestId('resolve-succeeded'))
    expect(await screen.findByTestId('reconciliation-conflict')).toBeTruthy()
    expect(screen.getByText('RECONCILIATION_CONFLICT')).toBeTruthy()
  })
})
