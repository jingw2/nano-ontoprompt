import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter } from 'react-router-dom'
import ApprovalsPage from './ApprovalsPage'

const server = setupServer(
  http.get('*/api/v1/admin/agent-reconciliations*', () => HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
  http.get('*/api/v1/mcp/write-requests', () => HttpResponse.json({ data: { items: [] }, message: 'ok' })),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage(initialEntries = ['/admin/approvals']) {
  return render(<MemoryRouter initialEntries={initialEntries}><ApprovalsPage /></MemoryRouter>)
}

describe('ApprovalsPage', () => {
  it('defaults to the reconciliation tab and switches to MCP on click', async () => {
    renderPage()
    expect(await screen.findByTestId('agent-reconciliation-page')).toBeTruthy()
    expect(screen.queryByTestId('mcp-write-requests-page')).toBeNull()
    await userEvent.click(screen.getByTestId('approvals-tab-mcp'))
    expect(await screen.findByTestId('mcp-write-requests-page')).toBeTruthy()
    expect(screen.queryByTestId('agent-reconciliation-page')).toBeNull()
  })

  it('honours ?tab=mcp on initial load', async () => {
    renderPage(['/admin/approvals?tab=mcp'])
    expect(await screen.findByTestId('mcp-write-requests-page')).toBeTruthy()
    expect(screen.queryByTestId('agent-reconciliation-page')).toBeNull()
  })
})
