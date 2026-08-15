import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  useAuthStore.getState().logout()
})
afterAll(() => server.close())

function page(items: unknown[], has_more = false, next_cursor: string | null = null) {
  return HttpResponse.json({ data: { items, next_cursor: has_more ? next_cursor ?? 'next' : null, has_more }, message: 'ok' })
}

const AGENT = {
  agent_id: 'a-1', status: 'active', visibility: 'private',
  name: 'Support Agent', version_no: 2, config_hash: 'c' + '0'.repeat(63), versions_count: 2,
  created_at: '2026-08-01T00:00:00Z', can_edit: true,
}

function setRole(role: 'admin' | 'editor' | 'viewer') {
  useAuthStore.getState().setAuth(
    { id: 'u-1', username: 'u', email: 'u@t.com', role, is_active: true, created_at: '2026-01-01T00:00:00Z' },
    'token',
  )
}

async function renderPage(initialEntries = ['/agents']) {
  const { default: AgentListPage } = await import('./AgentListPage')
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes><Route path="/agents" element={<AgentListPage />} /></Routes>
    </MemoryRouter>,
  )
}

describe('P2C-LIST', () => {
  it('red contract: requires the agent list client, page and filters', () => {
    const failures: string[] = []
    for (const p of ['src/api/agentsList.ts', 'src/pages/agents/list/AgentListPage.tsx', 'src/pages/agents/list/AgentFilters.tsx']) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P2C_LIST: ' + failures.join('; '))
  })

  it('renders the agent list with paging from the cursor envelope', async () => {
    server.use(
      http.get('*/api/v1/agents', ({ request }) => {
        const url = new URL(request.url)
        expect(url.searchParams.get('limit')).toBe('50')
        return page([AGENT], true, 'cursor-1')
      }),
    )
    await renderPage()
    expect(await screen.findByText('Support Agent')).toBeTruthy()
    expect(screen.getByText('v2')).toBeTruthy()
    expect(screen.getByRole('button', { name: '下一页' })).toBeTruthy()
  })

  it('serializes applied filters into the URL and the server query', async () => {
    const seen: string[] = []
    server.use(
      http.get('*/api/v1/agents', ({ request }) => {
        seen.push(request.url)
        return page([AGENT])
      }),
    )
    await renderPage()
    await screen.findByText('Support Agent')
    await userEvent.type(screen.getByLabelText('ID'), 'a-1')
    await userEvent.type(screen.getByLabelText('名称'), 'Support')
    await userEvent.type(screen.getByLabelText('创建时间（从）'), '2026-08-01T00:00:00Z')
    await userEvent.type(screen.getByLabelText('创建时间（至）'), '2026-08-31T23:59:59Z')
    await userEvent.click(screen.getByRole('button', { name: '筛选' }))
    await waitFor(() => {
      const last = new URL(seen[seen.length - 1])
      expect(last.searchParams.get('id')).toBe('a-1')
      expect(last.searchParams.get('name')).toBe('Support')
      expect(last.searchParams.get('created_from')).toBe('2026-08-01T00:00:00Z')
      expect(last.searchParams.get('created_before')).toBe('2026-08-31T23:59:59Z')
    })
  })

  it('preserves filter state in the URL (searchParams round-trip)', async () => {
    const seen: string[] = []
    server.use(
      http.get('*/api/v1/agents', ({ request }) => {
        seen.push(request.url)
        return page([AGENT])
      }),
    )
    await renderPage(['/agents?id=a-1&name=Support&created_from=2026-08-01T00:00:00Z'])
    await screen.findByText('Support Agent')
    const url = new URL(seen[seen.length - 1])
    expect(url.searchParams.get('id')).toBe('a-1')
    expect(url.searchParams.get('name')).toBe('Support')
    expect(url.searchParams.get('created_from')).toBe('2026-08-01T00:00:00Z')
    // filters render into the inputs
    expect((screen.getByLabelText('ID') as HTMLInputElement).value).toBe('a-1')
  })

  it('paginates with the opaque cursor and returns to the previous page', async () => {
    const seen: string[] = []
    server.use(
      http.get('*/api/v1/agents', ({ request }) => {
        seen.push(request.url)
        const url = new URL(request.url)
        if (url.searchParams.get('cursor') === 'cursor-1') {
          return page([{ ...AGENT, agent_id: 'a-2', name: 'Second Agent' }], false)
        }
        return page([AGENT], true, 'cursor-1')
      }),
    )
    await renderPage()
    await screen.findByText('Support Agent')
    await userEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('Second Agent')).toBeTruthy()
    const url = new URL(seen[seen.length - 1])
    expect(url.searchParams.get('cursor')).toBe('cursor-1')
    await userEvent.click(screen.getByRole('button', { name: '上一页' }))
    expect(await screen.findByText('Support Agent')).toBeTruthy()
  })

  it('gates Create on editor/admin role from the auth store', async () => {
    server.use(http.get('*/api/v1/agents', () => page([AGENT])))
    setRole('viewer')
    await renderPage()
    await screen.findByText('Support Agent')
    expect(screen.queryByRole('button', { name: '新建 Agent' })).toBeNull()
  })

  it('shows Create for editors and hides Archive without an edit grant', async () => {
    server.use(http.get('*/api/v1/agents', () => page([
      AGENT,
      { ...AGENT, agent_id: 'a-2', name: 'Read Only Agent', can_edit: false },
    ])))
    setRole('editor')
    await renderPage()
    await screen.findByText('Support Agent')
    expect(screen.getByRole('button', { name: '新建智能体' })).toBeTruthy()
    // first agent has the edit grant -> Archive visible; second has none -> hidden
    const rows = screen.getAllByRole('row')
    expect(rows.length).toBeGreaterThan(1)
    expect(screen.getAllByRole('button', { name: '归档' }).length).toBe(1)
  })

  it('archives an agent through DELETE and hides the button for archived rows', async () => {
    server.use(
      http.get('*/api/v1/agents', () => page([AGENT])),
      http.delete('*/api/v1/agents/a-1', () => new HttpResponse(null, { status: 204 })),
    )
    setRole('editor')
    await renderPage()
    await userEvent.click(await screen.findByRole('button', { name: '归档' }))
    await waitFor(() => expect(screen.getByText('已归档')).toBeTruthy())
  })

  it('shows the loading skeleton before the list resolves', async () => {
    server.use(
      http.get('*/api/v1/agents', () => new Promise(() => {})),
    )
    await renderPage()
    expect(await screen.findByTestId('agent-list-loading')).toBeTruthy()
  })

  it('shows the empty state with a clear-filters action', async () => {
    const seen: string[] = []
    server.use(
      http.get('*/api/v1/agents', ({ request }) => {
        seen.push(request.url)
        const url = new URL(request.url)
        if (url.searchParams.get('name')) return page([])
        return page([AGENT])
      }),
    )
    await renderPage()
    await screen.findByText('Support Agent')
    await userEvent.type(screen.getByLabelText('名称'), 'nope')
    await userEvent.click(screen.getByRole('button', { name: '筛选' }))
    expect(await screen.findByTestId('agent-list-empty')).toBeTruthy()
    expect(screen.getByText('没有符合筛选条件的智能体')).toBeTruthy()
    await userEvent.click(within(screen.getByTestId('agent-list-empty')).getByRole('button', { name: '清除筛选' }))
    expect(await screen.findByText('Support Agent')).toBeTruthy()
  })

  it('shows the error state with correlation ID and retries', async () => {
    let fail = true
    server.use(
      http.get('*/api/v1/agents', () => {
        if (fail) {
          return HttpResponse.json(
            { error: { code: 'AGENTS_LOAD_FAILED', message: 'boom', details: [] }, correlation_id: 'corr-123' },
            { status: 500 },
          )
        }
        return page([AGENT])
      }),
    )
    await renderPage()
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText(/corr-123/)).toBeTruthy()
    fail = false
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('Support Agent')).toBeTruthy()
  })
})
