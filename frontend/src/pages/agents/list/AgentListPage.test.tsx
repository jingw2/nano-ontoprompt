import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { MemoryRouter, Routes, Route } from 'react-router-dom'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function page(items: unknown[], has_more = false) {
  return HttpResponse.json({ data: { items, next_cursor: has_more ? 'next' : null, has_more }, message: 'ok' })
}

const AGENT = {
  agent_id: 'a-1', status: 'active', visibility: 'private',
  name: 'Support Agent', version_no: 2, config_hash: 'c' + '0'.repeat(63), versions_count: 2,
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
        expect(url.searchParams.get('page')).toBe('1')
        return page([AGENT], true)
      }),
    )
    const { default: AgentListPage } = await import('./AgentListPage')
    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes><Route path="/agents" element={<AgentListPage />} /></Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('Support Agent')).toBeTruthy()
    expect(screen.getByText('v2')).toBeTruthy()
    expect(screen.getByText('加载更多')).toBeTruthy()
  })

  it('filters the list via the filter controls', async () => {
    server.use(
      http.get('*/api/v1/agents', ({ request }) => {
        const url = new URL(request.url)
        if (url.searchParams.get('status') === 'archived') {
          return page([{ ...AGENT, agent_id: 'a-2', status: 'archived', name: 'Old Agent' }])
        }
        return page([AGENT])
      }),
    )
    const { default: AgentListPage } = await import('./AgentListPage')
    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes><Route path="/agents" element={<AgentListPage />} /></Routes>
      </MemoryRouter>,
    )
    await screen.findByText('Support Agent')
    await userEvent.selectOptions(screen.getByRole('combobox'), 'archived')
    expect(await screen.findByText('Old Agent')).toBeTruthy()
  })

  it('archives an agent through the client', async () => {
    server.use(
      http.get('*/api/v1/agents', () => page([AGENT])),
      http.post('*/api/v1/agents/a-1/archive', () =>
        HttpResponse.json({ data: { agent_id: 'a-1', status: 'archived' }, message: 'ok' })),
    )
    const { default: AgentListPage } = await import('./AgentListPage')
    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes><Route path="/agents" element={<AgentListPage />} /></Routes>
      </MemoryRouter>,
    )
    await userEvent.click(await screen.findByRole('button', { name: '归档' }))
    await waitFor(() => expect(screen.getByText('已归档')).toBeTruthy())
  })

  it('shows the loading gate before the list resolves', async () => {
    server.use(
      http.get('*/api/v1/agents', () => new Promise(() => {})),
    )
    const { default: AgentListPage } = await import('./AgentListPage')
    render(
      <MemoryRouter initialEntries={['/agents']}>
        <Routes><Route path="/agents" element={<AgentListPage />} /></Routes>
      </MemoryRouter>,
    )
    expect(await screen.findByText('加载中...')).toBeTruthy()
  })
})
