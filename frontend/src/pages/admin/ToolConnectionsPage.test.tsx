import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import ToolConnectionsPage from './ToolConnectionsPage'
import type { ToolConnection, ToolConnectionVersion } from '@/api/toolConnections'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <ToolConnectionsPage />
    </QueryClientProvider>,
  )
}

const PROVIDER = { id: 'p-1', name: 'Web Search', kind: 'search', status: 'active' }

describe('ToolConnectionsPage', () => {
  it('shows an empty state when there are no providers', async () => {
    server.use(http.get('*/api/v2/tool-providers', () =>
      HttpResponse.json({ data: { items: [] }, message: 'ok' })))
    renderPage()
    expect(await screen.findByTestId('tool-providers-empty')).toBeTruthy()
  })

  it('lists a provider, expands it, and lists its connections', async () => {
    server.use(
      http.get('*/api/v2/tool-providers', () =>
        HttpResponse.json({ data: { items: [PROVIDER] }, message: 'ok' })),
      http.get('*/api/v2/tool-connections', () =>
        HttpResponse.json({ data: { items: [{ id: 'c-1', provider_id: 'p-1', status: 'active', active_version_id: null }] }, message: 'ok' })),
    )
    renderPage()
    expect(await screen.findByTestId('provider-card-p-1')).toBeTruthy()
    await userEvent.click(screen.getByTestId('provider-card-p-1').querySelector('button')!)
    expect(await screen.findByTestId('connection-row-c-1')).toBeTruthy()
  })

  it('creates a new provider via the modal', async () => {
    let created = false
    server.use(
      http.get('*/api/v2/tool-providers', () =>
        HttpResponse.json({ data: { items: created ? [PROVIDER] : [] }, message: 'ok' })),
      http.post('*/api/v2/tool-providers', () => {
        created = true
        return HttpResponse.json({ data: PROVIDER, message: 'ok' }, { status: 201 })
      }),
    )
    renderPage()
    await screen.findByTestId('tool-providers-empty')
    await userEvent.click(screen.getByTestId('create-provider-button'))
    await userEvent.type(screen.getByTestId('provider-name-input'), 'Web Search')
    await userEvent.click(screen.getByTestId('submit-create-provider'))
    await waitFor(() => expect(screen.getByTestId('provider-card-p-1')).toBeTruthy())
  })

  it('creates a version, approves it via the confirmation dialog, then activates it', async () => {
    const connection: ToolConnection = { id: 'c-1', provider_id: 'p-1', status: 'active', active_version_id: null }
    let versions: ToolConnectionVersion[] = [
      { id: 'v-1', connection_id: 'c-1', version_no: 1, endpoint: 'https://search.example.com', audience: null,
        scopes: ['search:read'], credential_reference: null, allowlists: {}, approval_status: 'pending' as const,
        health_status: 'unknown' as const, created_by: 'u-1', created_at: '2026-01-01T00:00:00Z' },
    ]
    server.use(
      http.get('*/api/v2/tool-providers', () => HttpResponse.json({ data: { items: [PROVIDER] }, message: 'ok' })),
      http.get('*/api/v2/tool-connections', () => HttpResponse.json({ data: { items: [connection] }, message: 'ok' })),
      http.get('*/api/v2/tool-connections/c-1/versions', () => HttpResponse.json({ data: { items: versions }, message: 'ok' })),
      http.post('*/api/v2/tool-connections/versions/v-1/approve', () => {
        versions = versions.map(v => v.id === 'v-1' ? { ...v, approval_status: 'approved' as const } : v)
        return HttpResponse.json({ data: { id: 'v-1', approval_status: 'approved' }, message: 'ok' })
      }),
      http.post('*/api/v2/tool-connections/activate', () => {
        connection.active_version_id = 'v-1'
        return HttpResponse.json({ data: { connection_id: 'c-1', active_version_id: 'v-1' }, message: 'ok' })
      }),
    )
    renderPage()
    await userEvent.click((await screen.findByTestId('provider-card-p-1')).querySelector('button')!)
    await userEvent.click((await screen.findByTestId('connection-row-c-1')).querySelector('button')!)
    expect(await screen.findByTestId('version-row-v-1')).toBeTruthy()

    await userEvent.click(screen.getByTestId('approve-version-v-1'))
    expect(await screen.findByTestId('approve-version-dialog')).toBeTruthy()
    await userEvent.click(screen.getByTestId('confirm-approve-version'))
    await waitFor(() => expect(screen.getByTestId('activate-version-v-1')).toBeTruthy())

    await userEvent.click(screen.getByTestId('activate-version-v-1'))
    await waitFor(() => expect(screen.queryByTestId('activate-version-v-1')).toBeNull())
  })
})
