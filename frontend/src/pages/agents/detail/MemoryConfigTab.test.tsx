import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import MemoryConfigTab from './MemoryConfigTab'
import type { AgentVersion } from '@/api/agentDetail'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const VERSION: AgentVersion = {
  id: 'v-1', version_no: 1, name: 'Support Agent', description: 'd',
  config_hash: 'c' + '0'.repeat(63),
  default_model_config_version_id: 'm-1', default_model_name: 'gpt-4o',
  system_prompt: 'Be helpful', memory_settings: { short_term_enabled: false, long_term_enabled: false, budget: 100 },
  application_state_schema_version_id: 'as-1', change_note: null,
  prompt_generation_id: null, created_by: 'u-1', created_at: '2026-08-01T00:00:00Z',
}

describe('P2C-MEMORY', () => {
  it('red contract: requires the memory settings tab', () => {
    const failures: string[] = []
    for (const p of ['src/pages/agents/detail/MemoryConfigTab.tsx']) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P2C_MEMORY: ' + failures.join('; '))
  })

  it('saves bounded memory settings as a new AgentVersion (zero memory API calls)', async () => {
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    const onSaved = vi.fn()
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={onSaved} onDirtyChange={vi.fn()} />)
    const budget = screen.getByLabelText('记忆预算（条数）') as HTMLInputElement
    await waitFor(() => expect(budget.value).toBe('100'))
    await userEvent.type(screen.getByLabelText('短期记忆'), ' ') // no-op; checkbox unchanged
    await userEvent.click(screen.getByLabelText('短期记忆'))
    await userEvent.clear(budget)
    await userEvent.type(budget, '250')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    expect(savedBody).toMatchObject({
      base_version_no: 1,
      memory_settings: { short_term_enabled: true, long_term_enabled: false, budget: 250 },
    })
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version_no: 2 }))
  })

  it('shows the inspection-unavailable panel and issues zero memory content calls', async () => {
    server.use()
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit={false} onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    expect(await screen.findByTestId('memory-unavailable')).toBeTruthy()
    expect(screen.getByText(/记忆检查将在记忆功能激活后提供/)).toBeTruthy()
    // onUnhandledRequest: 'error' would fail on any /memories call
  })

  it('explains short-term, long-term and budget settings with descriptive copy', async () => {
    server.use()
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    // short-term / long-term descriptions
    expect(screen.getByText(/保留当前会话内的上下文/)).toBeTruthy()
    expect(screen.getByText(/跨会话保留重要事实与结论/)).toBeTruthy()
    // budget explanation
    expect(screen.getByText(/记忆条目数量上限/)).toBeTruthy()
    // consent + retention notes
    expect(screen.getByText(/记忆写入需要 Agent 拥有写入权限/)).toBeTruthy()
    expect(screen.getByText(/短期记忆随会话保留/)).toBeTruthy()
  })
})
