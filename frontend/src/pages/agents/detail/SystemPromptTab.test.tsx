import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import SystemPromptTab from './SystemPromptTab'
import type { AgentVersion } from '@/api/agentDetail'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const VERSION: AgentVersion = {
  id: 'v-1', version_no: 1, name: 'Support Agent', description: 'd',
  config_hash: 'c' + '0'.repeat(63),
  default_model_config_version_id: 'm-1', default_model_name: 'gpt-4o',
  system_prompt: 'Be helpful', memory_settings: {},
  application_state_schema_version_id: 'as-1', change_note: null,
  prompt_generation_id: null, created_by: 'u-1', created_at: '2026-08-01T00:00:00Z',
}

function renderTab(onSaved = vi.fn()) {
  return render(
    <SystemPromptTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={onSaved} onDirtyChange={vi.fn()} />,
  )
}

describe('P2C-DETAIL system prompt tab', () => {
  it('replaces the draft with the generated output', async () => {
    server.use(
      http.post('*/api/v1/agents/a-1/prompt-generations', () =>
        HttpResponse.json({
          data: {
            id: 'g-1', status: 'accepted', base_version_no: 1,
            model_config_version_id: 'm-1', model_name: 'gpt-4o',
            input_hash: 'i' + '0'.repeat(63), output_hash: 'o' + '0'.repeat(63),
            output_text: 'Replacement prompt', requested_at: '2026-08-02T00:00:00Z',
          }, message: 'ok',
        }, { status: 202 })),
    )
    renderTab()
    const textarea = screen.getByTestId('system-prompt-tab').querySelector('textarea')!
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toBe('Be helpful'))
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'old draft')
    await userEvent.click(screen.getByRole('button', { name: 'Generate' }))
    await screen.findByTestId('generation-provenance')
    await userEvent.click(screen.getByRole('button', { name: 'Replace' }))
    expect((textarea as HTMLTextAreaElement).value).toBe('Replacement prompt')
  })

  it('saves the draft as a new version with the sanitized prompt', async () => {
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    const onSaved = vi.fn()
    renderTab(onSaved)
    const textarea = screen.getByTestId('system-prompt-tab').querySelector('textarea')!
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toBe('Be helpful'))
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'new prompt text')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    expect(savedBody).toMatchObject({
      base_version_no: 1,
      name: 'Support Agent',
      system_prompt: 'new prompt text',
    })
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version_no: 2 }))
  })

  it('shows a conflict error when the save hits a 409', async () => {
    server.use(
      http.post('*/api/v1/agents/a-1/versions', () =>
        HttpResponse.json({ error: { code: 'AGENT_VERSION_CONFLICT', message: 'stale', details: [] } }, { status: 409 })),
    )
    renderTab()
    const textarea = screen.getByTestId('system-prompt-tab').querySelector('textarea')!
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toBe('Be helpful'))
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'new prompt text')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(await screen.findByText('检测到新版本，请刷新后重试')).toBeTruthy()
  })
})
