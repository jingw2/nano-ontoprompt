import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import ToolConnectionsPage from './ToolConnectionsPage'

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
})
