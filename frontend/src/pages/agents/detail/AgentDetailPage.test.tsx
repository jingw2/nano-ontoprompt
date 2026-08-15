import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
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

const VERSION = {
  id: 'v-1', version_no: 1, name: 'Support Agent', description: 'd',
  config_hash: 'c' + '0'.repeat(63),
  default_model_config_version_id: 'm-1', default_model_name: 'gpt-4o',
  system_prompt: 'Be helpful', memory_settings: {},
  application_state_schema_version_id: 'as-1', change_note: null,
  prompt_generation_id: null, created_by: 'u-1', created_at: '2026-08-01T00:00:00Z',
}

const AGENT = {
  agent_id: 'a-1', status: 'active', visibility: 'private', name: 'Support Agent',
  version_no: 1, config_hash: 'c' + '0'.repeat(63), versions_count: 1,
  created_at: '2026-08-01T00:00:00Z', can_edit: true,
}

function setRole(role: 'admin' | 'editor' | 'viewer') {
  useAuthStore.getState().setAuth(
    { id: 'u-1', username: 'u', email: 'u@t.com', role, is_active: true, created_at: '2026-01-01T00:00:00Z' },
    'token',
  )
}

async function renderDetail(initialEntries = ['/agents/a-1']) {
  const { default: AgentDetailPage } = await import('./AgentDetailPage')
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <Routes><Route path="/agents/:id" element={<AgentDetailPage />} /></Routes>
    </MemoryRouter>,
  )
}

function detailHandlers() {
  server.use(
    http.get('*/api/v1/agents/a-1', () => HttpResponse.json({ data: AGENT, message: 'ok' })),
    http.get('*/api/v1/agents/a-1/versions', () =>
      HttpResponse.json({ data: { items: [VERSION], next_cursor: null, has_more: false }, message: 'ok' })),
  )
}

describe('P2C-DETAIL', () => {
  it('red contract: requires the detail client, wizard, header, info and prompt tabs', () => {
    const failures: string[] = []
    for (const p of [
      'src/api/agentDetail.ts',
      'src/pages/agents/new/AgentCreateWizard.tsx',
      'src/pages/agents/detail/AgentDetailPage.tsx',
      'src/pages/agents/detail/AgentHeader.tsx',
      'src/pages/agents/detail/AgentInfoTab.tsx',
      'src/pages/agents/detail/SystemPromptTab.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P2C_DETAIL: ' + failures.join('; '))
  })

  it('shows the header, Basic tab and immutable version history', async () => {
    setRole('editor')
    detailHandlers()
    await renderDetail()
    expect(await screen.findAllByText('Support Agent')).toBeTruthy()
    expect(screen.getAllByText('v1').length).toBeGreaterThan(0)
    expect(screen.getByTestId('agent-info-tab')).toBeTruthy()
    expect(screen.getByText('gpt-4o')).toBeTruthy()
    const history = screen.getByTestId('version-history')
    expect(history.textContent).toContain('v1')
    expect(history.textContent).toContain('Support Agent')
  })

  it('saves a Basic edit as version N+1 through createAgentVersion', async () => {
    setRole('editor')
    detailHandlers()
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    await renderDetail()
    await screen.findAllByText('Support Agent')
    await userEvent.type(screen.getByLabelText('名称'), ' v2')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    expect(savedBody).toMatchObject({
      base_version_no: 1,
      name: 'Support Agent v2',
      default_model_config_version_id: 'm-1',
      default_model_name: 'gpt-4o',
    })
  })

  it('shows the conflict banner with reload on a 409 version conflict', async () => {
    setRole('editor')
    detailHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/versions', () =>
        HttpResponse.json({ error: { code: 'AGENT_VERSION_CONFLICT', message: 'stale', details: [] }, correlation_id: 'c-1' }, { status: 409 })),
    )
    await renderDetail()
    await screen.findAllByText('Support Agent')
    await userEvent.type(screen.getByLabelText('名称'), ' v2')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('检测到新版本，请重新加载')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重新加载' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '保留草稿' })).toBeTruthy()
  })

  it('gates editing to users with edit authority (viewer read-only)', async () => {
    setRole('viewer')
    detailHandlers()
    await renderDetail()
    await screen.findAllByText('Support Agent')
    expect((screen.getByLabelText('名称') as HTMLInputElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: '保存' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Generate' })).toBeNull()
  })

  it('shows System Prompt tab with generation provenance and append', async () => {
    setRole('editor')
    detailHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/prompt-generations', () =>
        HttpResponse.json({
          data: {
            id: 'g-1', status: 'accepted', base_version_no: 1,
            model_config_version_id: 'm-1', model_name: 'gpt-4o',
            input_hash: 'i' + '0'.repeat(63), output_hash: 'o' + '0'.repeat(63),
            output_text: 'Generated system prompt', requested_at: '2026-08-02T00:00:00Z',
          }, message: 'ok',
        }, { status: 202 })),
    )
    await renderDetail()
    await screen.findAllByText('Support Agent')
    await userEvent.click(screen.getByRole('button', { name: /系统提示词/ }))
    const textarea = (await screen.findByTestId('system-prompt-tab')).querySelector('textarea')!
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toBe('Be helpful'))
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'Draft text')
    await userEvent.click(screen.getByRole('button', { name: '生成' }))
    expect(await screen.findByTestId('generation-provenance')).toBeTruthy()
    expect(screen.getByText(/Generated system prompt/)).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: '追加' }))
    expect((textarea as HTMLTextAreaElement).value).toContain('Generated system prompt')
  })

  it('renders the Tools and Memory tabs and the live Application tab', async () => {
    setRole('editor')
    detailHandlers()
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [{ id: 'o-1', name: 'Supply Ontology', status: 'published' }], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    await renderDetail()
    await screen.findAllByText('Support Agent')
    await userEvent.click(screen.getByRole('button', { name: /工具/ }))
    expect(await screen.findByTestId('tool-config-tab')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /记忆/ }))
    expect(await screen.findByTestId('memory-config-tab')).toBeTruthy()
    await userEvent.click(screen.getByRole('button', { name: /智能体应用/ }))
    expect(await screen.findByTestId('agent-application-tab')).toBeTruthy()
    expect(screen.getByTestId('session-sidebar')).toBeTruthy()
  })
})
