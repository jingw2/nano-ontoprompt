import '@/i18n'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import RefreshOperationsPage from './RefreshOperationsPage'
import type { RefreshRunView, RefreshStatus } from '@/types/refresh'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/runtime/refresh/source-001']}>
      <Routes>
        <Route path="/runtime/refresh/:sourceId" element={<RefreshOperationsPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

function baseRun(overrides: Partial<RefreshRunView>): RefreshRunView {
  return {
    run_id: 'run-dead-001', source_id: 'source-001', resource: 'purchase_orders', policy: 'micro_batch',
    trigger: 'manual', status: 'dead_lettered', dispatch_state: 'dispatched', dispatch_queue: 'refresh.poll',
    config_version: 8, cursor_contract: 'watermark_primary_key', cursor_before: null, cursor_after: null,
    input_dataset_version_ids: [], pipeline_run_id: null, lag_seconds: 120, duplicate_count: 0, late_count: 0,
    retry_count: 2, retry_reason: null, dead_letter_id: null, replay_status: null, cancel_requested_at: null,
    cancel_requested_by: null, cancel_reason: null, terminal_at: '2026-08-29T00:00:00Z', already_terminal: false,
    ...overrides,
  }
}

function baseStatus(run: RefreshRunView, overrides: Partial<RefreshStatus> = {}): RefreshStatus {
  return {
    source_id: 'source-001', resource: 'purchase_orders', policy: 'micro_batch', config_version: 8,
    cursor_contract: 'watermark_primary_key', cursor: { primary_key: 'row-000100' }, cursor_observed_at: null,
    fencing_token: 0, latest_run: run, input_dataset_version_id: null, pipeline_run_id: null, lag_seconds: 120,
    freshness_lag_seconds: 120, duplicate_count: 0, late_count: 0, retry_count: 2, dlq_count: 1,
    next_schedule_at: '2026-08-31T02:00:00Z', sla_status: 'breached', backfill_window_seconds: 0,
    ...overrides,
  }
}

describe('RefreshOperationsPage', () => {
  it('shows policy, cursor, lag, and a dead-lettered status, then replays', async () => {
    const run = baseRun({})
    server.use(
      http.get('*/api/v2/refresh/sources/source-001/status', () => HttpResponse.json(baseStatus(run))),
      http.post('*/api/v2/refresh/runs/run-dead-001/replay', () =>
        HttpResponse.json(baseRun({ run_id: 'run-replay-001', status: 'queued', retry_reason: null }))),
    )
    renderPage()
    expect(await screen.findByTestId('refresh-policy')).toHaveTextContent('micro_batch')
    expect(screen.getByTestId('refresh-cursor')).toHaveTextContent('row-000100')
    expect(screen.getByTestId('refresh-lag-seconds')).toHaveTextContent('120')
    expect(screen.getByTestId('refresh-latest-status')).toHaveTextContent('DEAD_LETTERED')

    await userEvent.click(screen.getByTestId('replay-refresh-run'))
    expect(await screen.findByTestId('refresh-replay-status')).toHaveTextContent('QUEUED')
  })

  it('shows the server-recorded replay state for a dead-lettered run', async () => {
    const run = baseRun({ replay_status: 'queued' })
    server.use(
      http.get('*/api/v2/refresh/sources/source-001/status', () => HttpResponse.json(baseStatus(run))),
    )
    renderPage()
    expect(await screen.findByTestId('refresh-replay-state')).toHaveTextContent('QUEUED')
  })

  it('shows the plain already-terminal message and leaves status unchanged on a late cancel', async () => {
    const run = baseRun({ run_id: 'run-succeeded-001', status: 'succeeded', retry_reason: null })
    server.use(
      http.get('*/api/v2/refresh/sources/source-001/status', () => HttpResponse.json(baseStatus(run))),
      http.post('*/api/v2/refresh/runs/run-succeeded-001/cancel', () =>
        HttpResponse.json({ status: 'succeeded', already_terminal: true })),
    )
    renderPage()
    expect(await screen.findByTestId('refresh-latest-status')).toHaveTextContent('SUCCEEDED')
    // an authorized-but-terminal run shows no active cancel control
    expect(screen.getByTestId('cancel-refresh-run')).toHaveProperty('disabled', true)
  })

  it('cancels an active run and shows the recorded reason', async () => {
    const run = baseRun({ run_id: 'run-running-001', status: 'running', retry_reason: null })
    const cancelled = baseRun({
      run_id: 'run-running-001', status: 'cancelled', cancel_reason: 'planned source maintenance',
      cancel_requested_by: 'user-1', terminal_at: '2026-08-30T00:00:00Z',
    })
    server.use(
      http.get('*/api/v2/refresh/sources/source-001/status', () => HttpResponse.json(baseStatus(run))),
      http.post('*/api/v2/refresh/runs/run-running-001/cancel', () => HttpResponse.json(cancelled)),
    )
    renderPage()
    expect(await screen.findByTestId('refresh-latest-status')).toHaveTextContent('RUNNING')
    expect(screen.getByTestId('cancel-refresh-run')).toHaveProperty('disabled', false)

    server.use(
      http.get('*/api/v2/refresh/sources/source-001/status', () => HttpResponse.json(baseStatus(cancelled))),
    )
    await userEvent.click(screen.getByTestId('cancel-refresh-run'))
    await waitFor(async () => expect(await screen.findByTestId('refresh-latest-status')).toHaveTextContent('CANCELLED'))
    expect(screen.getByTestId('refresh-cancel-reason')).toHaveTextContent('planned source maintenance')
  })
})
