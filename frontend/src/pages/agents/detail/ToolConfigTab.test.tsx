import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import ToolConfigTab from './ToolConfigTab'
import type { AgentVersion } from '@/api/agentDetail'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const PUBLISHED = [
  { id: 'o-1', name: 'Supply Ontology', status: 'published' },
  { id: 'o-2', name: 'Policy Ontology', status: 'published' },
]

const TOOLS_O1 = {
  ontology_id: 'o-1', published: true, release_id: 'r-1',
  tools: [
    { descriptor_id: 'query:o-1', version: 1, source_kind: 'builtin', source_id: 'query',
      capability: 'read_instances', timeout_ms: 10000, result_limit: 10, descriptor_hash: 'h' + '0'.repeat(63) },
    { descriptor_id: 'logic:rule-1', version: 1, source_kind: 'logic', source_id: 'rule-1',
      capability: 'execute_read_logic', timeout_ms: 10000, result_limit: 1, descriptor_hash: 'i' + '0'.repeat(63) },
    { descriptor_id: 'action:action-1', version: 1, source_kind: 'action', source_id: 'action-1',
      capability: 'execute_instance_action', timeout_ms: 30000, result_limit: 1, descriptor_hash: 'j' + '0'.repeat(63) },
  ],
}

const VERSION: AgentVersion = {
  id: 'v-1', version_no: 1, name: 'Support Agent', description: 'd',
  config_hash: 'c' + '0'.repeat(63),
  default_model_config_version_id: 'm-1', default_model_name: 'gpt-4o',
  system_prompt: null, memory_settings: {},
  application_state_schema_version_id: 'as-1', change_note: null,
  prompt_generation_id: null, created_by: 'u-1', created_at: '2026-08-01T00:00:00Z',
  ontology_bindings: [],
}

function toolsHandlers() {
  server.use(
    http.get('*/api/v1/agents/catalog/ontologies', () =>
      HttpResponse.json({ data: { items: PUBLISHED, next_cursor: null, has_more: false }, message: 'ok' })),
    http.get('*/api/v1/ontologies/o-1/tools', () => HttpResponse.json({ data: TOOLS_O1, message: 'ok' })),
  )
}

function renderTab(props: Partial<Parameters<typeof ToolConfigTab>[0]> = {}) {
  return render(<ToolConfigTab agentId="a-1" activeVersion={VERSION} canEdit
    onSaved={vi.fn()} onDirtyChange={vi.fn()} {...props} />)
}

describe('P2C-TOOLS', () => {
  it('red contract: requires the tools client, tab and cards', () => {
    const failures: string[] = []
    for (const p of [
      'src/api/agentTools.ts',
      'src/pages/agents/detail/ToolConfigTab.tsx',
      'src/pages/agents/detail/OntologyBindingCard.tsx',
      'src/pages/agents/detail/ExternalToolCard.tsx',
      'src/pages/agents/detail/CapabilityDrawer.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P2C_TOOLS: ' + failures.join('; '))
  })

  it('lists published ontologies and validates the binding set', async () => {
    let validated: Record<string, unknown> | null = null
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', async ({ request }) => {
        validated = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { valid: true, capabilities: ['read_instances', 'traverse_relations'] }, message: 'ok' })
      }),
    )
    renderTab()
    expect(await screen.findByText('Supply Ontology')).toBeTruthy()
    expect(screen.getByText('Policy Ontology')).toBeTruthy()
    await userEvent.click(screen.getAllByRole('button', { name: '绑定' })[0])
    await waitFor(() => expect(validated).not.toBeNull())
    expect(validated).toMatchObject({ ontology_ids: ['o-1'] })
    expect(await screen.findByText('绑定配置有效')).toBeTruthy()
  })

  it('exposes per-tool toggles and saves the selection as version N+1', async () => {
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', () =>
        HttpResponse.json({ data: { valid: true, capabilities: ['read_instances'] }, message: 'ok' })),
    )
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    const onSaved = vi.fn()
    renderTab({ onSaved })
    await screen.findByText('Supply Ontology')
    await userEvent.click(screen.getAllByRole('button', { name: '绑定' })[0])
    // the ontology is bound with the built-in query tool selected; load tools
    expect(await screen.findByTestId('ontology-tools-o-1')).toBeTruthy()
    await waitFor(() => expect(screen.getByText(/Logic 规则/)).toBeTruthy())
    // toggle the Logic rule + Action on
    const logicCheckbox = screen.getByText(/Logic 规则/).closest('label')!.querySelector('input')!
    const actionCheckbox = screen.getByText(/实例 Action/).closest('label')!.querySelector('input')!
    await userEvent.click(logicCheckbox)
    await userEvent.click(actionCheckbox)
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    const bindings = savedBody!.ontology_bindings as { ontology_id: string; capabilities: string[]; selected_tools: string[] }[]
    expect(bindings).toHaveLength(1)
    expect(bindings[0].ontology_id).toBe('o-1')
    expect(new Set(bindings[0].selected_tools)).toEqual(new Set(['query:o-1', 'logic:rule-1', 'action:action-1']))
    expect(bindings[0].capabilities).toContain('execute_read_logic')
    expect(bindings[0].capabilities).toContain('execute_instance_action')
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version_no: 2 }))
  })

  it('restores persisted bindings from the active version', async () => {
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', () =>
        HttpResponse.json({ data: { valid: true, capabilities: [] }, message: 'ok' })),
    )
    const versionWithBinding: AgentVersion = {
      ...VERSION,
      ontology_bindings: [{
        ontology_id: 'o-1',
        capabilities: ['read_schema', 'read_instances', 'traverse_relations'],
        allowlists: {},
        selected_tools: ['query:o-1', 'logic:rule-1'],
      }],
    }
    renderTab({ activeVersion: versionWithBinding })
    expect(await screen.findByText('Supply Ontology')).toBeTruthy()
    expect(screen.getAllByRole('button', { name: '已绑定' })).toHaveLength(1)
    // dirty only when the selection actually changed
    const dirty = vi.fn()
    render(<ToolConfigTab agentId="a-1" activeVersion={versionWithBinding} canEdit onSaved={vi.fn()} onDirtyChange={dirty} />)
    await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false))
  })

  it('shows external tool cards as unavailable and issues zero P7 requests', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    renderTab()
    await waitFor(() => expect(screen.getByTestId('external-tool-cards')).toBeTruthy())
    const cards = screen.getAllByTestId('external-tool-card')
    expect(cards.length).toBeGreaterThanOrEqual(3)
    expect(screen.getAllByText('后续提供').length).toBeGreaterThanOrEqual(3)
    // onUnhandledRequest: 'error' above would fail the test on any P7 call
  })

  it('shows the capability intersection drawer', async () => {
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', () =>
        HttpResponse.json({ data: { valid: true, capabilities: ['read_instances', 'traverse_relations'] }, message: 'ok' })),
    )
    renderTab()
    await userEvent.click((await screen.findAllByRole('button', { name: '绑定' }))[0])
    await userEvent.click(await screen.findByRole('button', { name: '查看能力交集' }))
    expect(await screen.findByTestId('capability-drawer')).toBeTruthy()
    // the drawer chip + the query-tool capability row may both show the token
    expect(screen.getAllByText('read_instances').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('traverse_relations').length).toBeGreaterThanOrEqual(1)
  })

  it('gates binding to editors (viewer sees read-only cards)', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: PUBLISHED, next_cursor: null, has_more: false }, message: 'ok' })),
    )
    renderTab({ canEdit: false })
    await screen.findByText('Supply Ontology')
    expect((screen.getAllByRole('button', { name: '绑定' })[0] as HTMLButtonElement).disabled).toBe(true)
  })
})
