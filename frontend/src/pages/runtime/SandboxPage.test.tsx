import '@/i18n'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import SandboxPage from './SandboxPage'

// Zero-coverage gap identified by review: this page had no test file at all.

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/runtime/action-plans/plan-sandbox-001/sandbox']}>
      <Routes>
        <Route path="/runtime/action-plans/:planId/sandbox" element={<SandboxPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

const SANDBOX_RESULT = {
  simulation_id: 'sim-001',
  action_plan_id: 'plan-sandbox-001',
  semantic_snapshot_id: 'snap-valid-001',
  expected_rows: 3,
  before_after_diff: { status: { before: 'pending', after: 'confirmed' } },
  impact_summary: { affected_entities: 1 },
  rule_outcome: [{ rule_id: 'r-1', result: 'matched' }],
  policy_result: { decision: 'ALLOW' },
  precondition_hashes: { row_hash: 'a'.repeat(64) },
  expires_at: '2026-09-01T00:00:00Z',
}

describe('SandboxPage', () => {
  it('renders the server-computed simulation, diff, impact, and policy result', async () => {
    server.use(
      http.get('*/api/v2/runtime/action-plans/plan-sandbox-001/sandbox', () => HttpResponse.json(SANDBOX_RESULT)),
    )
    renderPage()

    expect(await screen.findByTestId('sandbox-simulation-id')).toHaveTextContent('sim-001')
    expect(screen.getByTestId('sandbox-expected-rows')).toHaveTextContent('3')
    expect(screen.getByTestId('sandbox-diff')).toHaveTextContent('confirmed')
    expect(screen.getByTestId('sandbox-impact-summary')).toHaveTextContent('affected_entities')
    expect(screen.getByTestId('sandbox-rule-outcome')).toHaveTextContent('matched')
    expect(screen.getByTestId('sandbox-policy-result')).toHaveTextContent('ALLOW')
  })

  it('renders a load-failure message without crashing', async () => {
    server.use(
      http.get('*/api/v2/runtime/action-plans/plan-sandbox-001/sandbox', () =>
        HttpResponse.json({ decision: 'DENY', reason_code: 'POLICY_DENIED' }, { status: 403 })),
    )
    renderPage()
    expect(await screen.findByTestId('sandbox-error')).toBeTruthy()
  })
})
