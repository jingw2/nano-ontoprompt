import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import McpWriteRequestsPage from './McpWriteRequestsPage'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const ITEM = {
  id: 'req-1', ontology_id: 'onto-1', release_id: 'rel-1', descriptor_id: 'action:x',
  target_instance_id: null, parameters: { foo: 'bar' }, preview_hash: 'h'.repeat(64),
  preview_canonical: '{}', status: 'pending', created_at: '2026-01-01T00:00:00Z', resolved_at: null,
}

describe('McpWriteRequestsPage', () => {
  it('shows an empty state when there are no pending requests', async () => {
    server.use(http.get('*/api/v1/mcp/write-requests', () =>
      HttpResponse.json({ data: { items: [] }, message: 'ok' })))
    render(<McpWriteRequestsPage />)
    expect(await screen.findByTestId('mcp-write-requests-empty')).toBeTruthy()
  })

  it('lists a pending request and approves it', async () => {
    let approved = false
    server.use(
      http.get('*/api/v1/mcp/write-requests', () =>
        HttpResponse.json({ data: { items: approved ? [] : [ITEM] }, message: 'ok' })),
      http.post('*/api/v1/mcp/write-requests/req-1/approve', () => {
        approved = true
        return HttpResponse.json({ data: { id: 'req-1', status: 'approved' }, message: 'ok' })
      }),
    )
    render(<McpWriteRequestsPage />)
    expect(await screen.findByTestId('mcp-write-request-req-1')).toBeTruthy()
    await userEvent.click(screen.getByTestId('mcp-write-request-req-1').querySelector('button')!)
    expect(await screen.findByTestId('mcp-write-request-detail')).toBeTruthy()
    await userEvent.click(screen.getByTestId('mcp-write-request-approve'))
    await waitFor(() => expect(screen.getByTestId('mcp-write-requests-empty')).toBeTruthy())
  })

  it('shows an error state with a working retry button', async () => {
    let calls = 0
    server.use(http.get('*/api/v1/mcp/write-requests', () => {
      calls += 1
      return calls === 1
        ? HttpResponse.error()
        : HttpResponse.json({ data: { items: [] }, message: 'ok' })
    }))
    render(<McpWriteRequestsPage />)
    expect(await screen.findByTestId('mcp-write-requests-error')).toBeTruthy()
    await userEvent.click(screen.getByTestId('mcp-write-requests-error').querySelector('button')!)
    await waitFor(() => expect(screen.getByTestId('mcp-write-requests-empty')).toBeTruthy())
  })
})
