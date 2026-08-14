import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { MemoryRouter, Routes, Route, useParams } from 'react-router-dom'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function DetailStub() {
  const { id } = useParams<{ id: string }>()
  return <div>DETAIL:{id}</div>
}

async function renderWizard() {
  const { default: AgentCreateWizard } = await import('./AgentCreateWizard')
  return render(
    <MemoryRouter initialEntries={['/agents/new']}>
      <Routes>
        <Route path="/agents/new" element={<AgentCreateWizard />} />
        <Route path="/agents/:id" element={<DetailStub />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('P2C-DETAIL create wizard', () => {
  it('creates an agent with the chosen model and navigates to the detail page', async () => {
    let createdBody: Record<string, unknown> | null = null
    server.use(
      http.get('*/api/v1/agents/catalog/models', () =>
        HttpResponse.json({ data: { items: [
          { id: 'm-1', name: 'gpt-4o', provider: 'openai', version_no: 3, behavior_hash: 'h' + '0'.repeat(63) },
          { id: 'm-2', name: 'claude-3', provider: 'anthropic', version_no: 1, behavior_hash: 'i' + '0'.repeat(63) },
        ], next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/agents', async ({ request }) => {
        createdBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { agent_id: 'a-new', version_id: 'v-1', version_no: 1, config_hash: 'c' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    await renderWizard()
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).options.length).toBe(3))
    await userEvent.type(screen.getByLabelText('名称'), 'New Agent')
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'm-1')
    await userEvent.type(screen.getByLabelText('初始系统提示词'), 'Be concise')
    await userEvent.click(screen.getByRole('button', { name: '创建' }))
    await waitFor(() => expect(createdBody).not.toBeNull())
    expect(createdBody).toMatchObject({
      name: 'New Agent',
      default_model_config_version_id: 'm-1',
      default_model_name: 'gpt-4o',
      system_prompt: 'Be concise',
      memory_settings: {},
    })
    expect(screen.getByText('DETAIL:a-new')).toBeTruthy()
  })

  it('disables submit until a name and model are chosen', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/models', () =>
        HttpResponse.json({ data: { items: [{ id: 'm-1', name: 'gpt-4o', version_no: 3 }], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    await renderWizard()
    await waitFor(() => expect((screen.getByRole('combobox') as HTMLSelectElement).options.length).toBe(2))
    expect((screen.getByRole('button', { name: '创建' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.type(screen.getByLabelText('名称'), 'X')
    expect((screen.getByRole('button', { name: '创建' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.selectOptions(screen.getByLabelText('模型'), 'm-1')
    expect((screen.getByRole('button', { name: '创建' }) as HTMLButtonElement).disabled).toBe(false)
  })
})
