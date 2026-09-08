import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import MemoryInspectionDrawer from './MemoryInspectionDrawer'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const MEMORY_PENDING = {
  id: 'mem-1', subject_key: 'user', predicate: 'likes', display_text: 'Likes coffee',
  confidence: 0.8, sensitivity: 'low', status: 'pending_confirmation', consent_basis: 'inferred',
  created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:00Z',
}

const MEMORY_ACTIVE = {
  id: 'mem-2', subject_key: 'user', predicate: 'works_at', display_text: 'Works at Acme',
  confidence: 0.9, sensitivity: 'low', status: 'active', consent_basis: 'explicit',
  created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-02T00:00:00Z',
}

function mockEmptyConflicts() {
  return http.get('*/api/v1/agents/a-1/memories/conflicts', () =>
    HttpResponse.json({ data: { items: [] }, message: 'ok' }))
}

describe('P6B3-MEMORY-DRAWER', () => {
  it('renders nothing when open=false', () => {
    render(<MemoryInspectionDrawer open={false} onClose={() => {}} agentId="a-1" />)
    expect(screen.queryByTestId('memory-inspection-drawer')).toBeNull()
  })

  it('fetches and renders a list of memories when opened', async () => {
    server.use(
      http.get('*/api/v1/agents/a-1/memories', () =>
        HttpResponse.json({ data: { items: [MEMORY_PENDING, MEMORY_ACTIVE] }, message: 'ok' })),
      mockEmptyConflicts(),
    )
    render(<MemoryInspectionDrawer open onClose={() => {}} agentId="a-1" />)
    expect(await screen.findByText('Likes coffee')).toBeTruthy()
    expect(screen.getByText('Works at Acme')).toBeTruthy()
  })

  it('clicking Confirm without checking the consent checkbox does not call the API', async () => {
    let confirmCalled = false
    server.use(
      http.get('*/api/v1/agents/a-1/memories', () =>
        HttpResponse.json({ data: { items: [MEMORY_PENDING] }, message: 'ok' })),
      mockEmptyConflicts(),
      http.post('*/api/v1/agents/a-1/memories/mem-1/confirm', () => {
        confirmCalled = true
        return HttpResponse.json({ data: { ...MEMORY_PENDING, status: 'active' }, message: 'ok' })
      }),
    )
    render(<MemoryInspectionDrawer open onClose={() => {}} agentId="a-1" />)
    await screen.findByText('Likes coffee')
    await userEvent.click(screen.getByTestId('memory-confirm-mem-1'))
    expect(confirmCalled).toBe(false)
  })

  it('checking the checkbox then clicking Confirm calls agentMemoriesApi.confirm with consent: true', async () => {
    let confirmBody: unknown = null
    server.use(
      http.get('*/api/v1/agents/a-1/memories', () =>
        HttpResponse.json({ data: { items: [MEMORY_PENDING] }, message: 'ok' })),
      mockEmptyConflicts(),
      http.post('*/api/v1/agents/a-1/memories/mem-1/confirm', async ({ request }) => {
        confirmBody = await request.json()
        return HttpResponse.json({ data: { ...MEMORY_PENDING, status: 'active' }, message: 'ok' })
      }),
    )
    render(<MemoryInspectionDrawer open onClose={() => {}} agentId="a-1" />)
    await screen.findByText('Likes coffee')
    await userEvent.click(screen.getByTestId('memory-consent-checkbox-mem-1'))
    await userEvent.click(screen.getByTestId('memory-confirm-mem-1'))
    await waitFor(() => expect(confirmBody).toEqual({ consent: true }))
  })

  it('a conflict renders both sides with two "Keep this one" buttons', async () => {
    const CONFLICT = {
      conflict_id: 'c-1', subject_key: 'user', predicate: 'age',
      memory_id_a: 'mem-3', display_text_a: 'Age is 30',
      memory_id_b: 'mem-4', display_text_b: 'Age is 31',
      created_at: '2026-08-01T00:00:00Z',
    }
    server.use(
      http.get('*/api/v1/agents/a-1/memories', () =>
        HttpResponse.json({ data: { items: [] }, message: 'ok' })),
      http.get('*/api/v1/agents/a-1/memories/conflicts', () =>
        HttpResponse.json({ data: { items: [CONFLICT] }, message: 'ok' })),
    )
    render(<MemoryInspectionDrawer open onClose={() => {}} agentId="a-1" />)
    expect(await screen.findByText('Age is 30')).toBeTruthy()
    expect(screen.getByText('Age is 31')).toBeTruthy()
    expect(screen.getByTestId('conflict-keep-a-c-1')).toBeTruthy()
    expect(screen.getByTestId('conflict-keep-b-c-1')).toBeTruthy()
  })
})
