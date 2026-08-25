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
  system_prompt: 'Be helpful',
  memory_settings: {
    short_term_enabled: false, long_term_enabled: false,
    message_pairs: 10, summary_threshold: 20, summary_token_budget: 1000,
    recall_token_budget: 700, recall_count: 6,
  },
  application_state_schema_version_id: 'as-1', change_note: null,
  prompt_generation_id: null, created_by: 'u-1', created_at: '2026-08-01T00:00:00Z',
}

const EMPTY_VERSION: AgentVersion = { ...VERSION, memory_settings: {} }

describe('P2C-MEMORY', () => {
  it('red contract: requires the memory settings tab', () => {
    const failures: string[] = []
    for (const p of ['src/pages/agents/detail/MemoryConfigTab.tsx']) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P2C_MEMORY: ' + failures.join('; '))
  })

  it('renders all 7 fields with spec defaults when memory_settings is absent/empty', async () => {
    render(<MemoryConfigTab agentId="a-1" activeVersion={EMPTY_VERSION} canEdit onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    expect((screen.getByLabelText('短期记忆') as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText('长期记忆') as HTMLInputElement).checked).toBe(false)
    await waitFor(() => expect((screen.getByLabelText('保留对话轮次') as HTMLInputElement).value).toBe('12'))
    expect((screen.getByLabelText('摘要触发阈值') as HTMLInputElement).value).toBe('24')
    expect((screen.getByLabelText('摘要 Token 预算') as HTMLInputElement).value).toBe('1200')
    expect((screen.getByLabelText('召回 Token 预算') as HTMLInputElement).value).toBe('800')
    expect((screen.getByLabelText('召回条数') as HTMLInputElement).value).toBe('8')
  })

  it('has correct min/max bounds on the 5 numeric fields, matching the backend RANGES', () => {
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    const check = (label: string, min: string, max: string) => {
      const el = screen.getByLabelText(label) as HTMLInputElement
      expect(el.min).toBe(min)
      expect(el.max).toBe(max)
    }
    check('保留对话轮次', '2', '20')
    check('摘要触发阈值', '8', '40')
    check('摘要 Token 预算', '256', '2048')
    check('召回 Token 预算', '128', '1200')
    check('召回条数', '1', '12')
  })

  it('clamps out-of-range numeric input before save', async () => {
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    const messagePairs = screen.getByLabelText('保留对话轮次') as HTMLInputElement
    await userEvent.clear(messagePairs)
    await userEvent.type(messagePairs, '999')
    const recallCount = screen.getByLabelText('召回条数') as HTMLInputElement
    await userEvent.clear(recallCount)
    await userEvent.type(recallCount, '0')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    expect((savedBody!.memory_settings as Record<string, unknown>).message_pairs).toBe(20)
    expect((savedBody!.memory_settings as Record<string, unknown>).recall_count).toBe(1)
  })

  it('saves all 7 keys as a new AgentVersion with correct types (zero memory API calls)', async () => {
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    const onSaved = vi.fn()
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={onSaved} onDirtyChange={vi.fn()} />)
    await userEvent.click(screen.getByLabelText('短期记忆'))
    const summaryThreshold = screen.getByLabelText('摘要触发阈值') as HTMLInputElement
    await userEvent.clear(summaryThreshold)
    await userEvent.type(summaryThreshold, '30')
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    expect(savedBody).toMatchObject({
      base_version_no: 1,
      memory_settings: {
        short_term_enabled: true,
        long_term_enabled: false,
        message_pairs: 10,
        summary_threshold: 30,
        summary_token_budget: 1000,
        recall_token_budget: 700,
        recall_count: 6,
      },
    })
    const settings = (savedBody as unknown as { memory_settings: Record<string, unknown> }).memory_settings
    expect(typeof settings.short_term_enabled).toBe('boolean')
    expect(typeof settings.long_term_enabled).toBe('boolean')
    expect(typeof settings.message_pairs).toBe('number')
    expect(typeof settings.summary_threshold).toBe('number')
    expect(typeof settings.summary_token_budget).toBe('number')
    expect(typeof settings.recall_token_budget).toBe('number')
    expect(typeof settings.recall_count).toBe('number')
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version_no: 2 }))
  })

  it('has no inspection-unavailable panel and opens the MemoryInspectionDrawer from a button', async () => {
    server.use(
      http.get('*/api/v1/agents/a-1/memories', () =>
        HttpResponse.json({ data: { items: [] }, message: 'ok' })),
      http.get('*/api/v1/agents/a-1/memories/conflicts', () =>
        HttpResponse.json({ data: { items: [] }, message: 'ok' })),
    )
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit={false} onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    expect(screen.queryByTestId('memory-unavailable')).toBeNull()
    expect(screen.queryByTestId('memory-inspection-drawer')).toBeNull()
    const openButton = screen.getByTestId('open-memory-inspection')
    await userEvent.click(openButton)
    expect(await screen.findByTestId('memory-inspection-drawer')).toBeTruthy()
  })

  it('explains short-term, long-term and the grouped numeric parameters with descriptive copy, with long-term params no longer marked inert', async () => {
    server.use()
    render(<MemoryConfigTab agentId="a-1" activeVersion={VERSION} canEdit onSaved={vi.fn()} onDirtyChange={vi.fn()} />)
    // short-term / long-term descriptions
    expect(screen.getByText(/保留当前会话内的上下文/)).toBeTruthy()
    expect(screen.getByText(/跨会话保留重要事实与结论/)).toBeTruthy()
    // grouped numeric parameter headings
    expect(screen.getByText('短期记忆参数')).toBeTruthy()
    const longTermGroupHeading = screen.getByText('长期记忆参数')
    expect(longTermGroupHeading).toBeTruthy()
    // long-term parameters are no longer marked "暂未生效" / inert
    expect(screen.queryByText('暂未生效')).toBeNull()
    expect(screen.queryByText(/长期记忆功能在后续版本上线后才会生效/)).toBeNull()
    const longTermGroupBlock = longTermGroupHeading.closest('.border')
    expect(longTermGroupBlock?.className).not.toMatch(/opacity-70/)
    // consent + retention notes
    expect(screen.getByText(/记忆写入需要 Agent 拥有写入权限/)).toBeTruthy()
    expect(screen.getByText(/短期记忆随会话保留/)).toBeTruthy()
  })
})
