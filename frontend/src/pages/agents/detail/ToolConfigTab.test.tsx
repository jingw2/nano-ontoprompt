import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import ToolConfigTab from './ToolConfigTab'
import type { AgentVersion } from '@/api/agentDetail'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// default validation handler: every test may bind an ontology (re-registered
// after each resetHandlers)
function defaultHandlers() {
  server.use(
    http.post('*/api/v1/agents/a-1/tool-validation', () =>
      HttpResponse.json({ data: { valid: true, capabilities: [] }, message: 'ok' })),
  )
}

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
  defaultHandlers()
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

/** Select a published ontology from the code+name dropdown. */
async function pickOntology(name: string) {
  const select = screen.getByTestId('ontology-picker') as HTMLSelectElement
  const option = [...select.options].find(o => o.textContent?.includes(name))
  if (!option) throw new Error(`option ${name} not found in picker: ${[...select.options].map(o => o.textContent)}`)
  await userEvent.selectOptions(select, option.value)
}

describe('P2C-TOOLS', () => {
  it('red contract: requires the tools client, tab and the published-ontology dropdown', () => {
    const failures: string[] = []
    for (const p of [
      'src/api/agentTools.ts',
      'src/pages/agents/detail/ToolConfigTab.tsx',
      'src/pages/agents/detail/ExternalToolCard.tsx',
      'src/pages/agents/detail/CapabilityDrawer.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    const tab = resolve(__dirname, '../../../../src/pages/agents/detail/ToolConfigTab.tsx')
    if (existsSync(tab) && !readFileSync(tab, 'utf8').includes('ontology-picker')) {
      failures.push('ToolConfigTab missing the published-ontology dropdown')
    }
    if (failures.length) throw new Error('RED_P2C_TOOLS: ' + failures.join('; '))
  })

  it('lists published ontologies in a code+name dropdown and validates the binding set', async () => {
    let validated: Record<string, unknown> | null = null
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', async ({ request }) => {
        validated = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { valid: true, capabilities: ['read_instances', 'traverse_relations'] }, message: 'ok' })
      }),
    )
    renderTab()
    const select = await screen.findByTestId('ontology-picker') as HTMLSelectElement
    const labels = [...select.options].map(o => o.textContent)
    // the dropdown shows the ontology CODE and NAME
    expect(labels.some(l => l?.includes('Supply Ontology') && l?.includes('o-1'))).toBe(true)
    expect(labels.some(l => l?.includes('Policy Ontology') && l?.includes('o-2'))).toBe(true)
    await pickOntology('Supply Ontology')
    await waitFor(() => expect(validated).not.toBeNull())
    expect(validated).toMatchObject({ ontology_ids: ['o-1'] })
    expect(await screen.findByText('绑定配置有效')).toBeTruthy()
  })

  it('defaults ALL categories on and saves the selection as version N+1', async () => {
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
    await screen.findByTestId('ontology-picker')
    await pickOntology('Supply Ontology')
    // the ontology is bound with ALL tool categories on by default
    expect(await screen.findByTestId('ontology-tools-o-1')).toBeTruthy()
    await waitFor(() => expect(screen.getByText(/Logic 规则 · rule-1/)).toBeTruthy())
    // every category toggle defaults to checked
    for (const cat of ['mcp', 'query', 'write', 'logic', 'action']) {
      expect((screen.getByTestId(`category-o-1-${cat}`) as HTMLInputElement).checked).toBe(true)
    }
    // every exposed tool is selected under the default-all categories
    const queryCheckbox = screen.getByText(/查询（实例检索）/).closest('label')!.querySelector('input') as HTMLInputElement
    const logicCheckbox = screen.getByText(/Logic 规则 · rule-1/).closest('label')!.querySelector('input') as HTMLInputElement
    const actionCheckbox = screen.getByText(/实例 Action · action-1/).closest('label')!.querySelector('input') as HTMLInputElement
    expect(queryCheckbox.checked).toBe(true)
    expect(logicCheckbox.checked).toBe(true)
    expect(actionCheckbox.checked).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    const bindings = savedBody!.ontology_bindings as { ontology_id: string; capabilities: string[]; selected_tools: string[]; enabled_categories: string[] }[]
    expect(bindings).toHaveLength(1)
    expect(bindings[0].ontology_id).toBe('o-1')
    expect(new Set(bindings[0].selected_tools)).toEqual(new Set(['query:o-1', 'logic:rule-1', 'action:action-1']))
    expect(new Set(bindings[0].enabled_categories)).toEqual(new Set(['mcp', 'query', 'write', 'logic', 'action']))
    expect(bindings[0].capabilities).toContain('execute_read_logic')
    expect(bindings[0].capabilities).toContain('execute_instance_action')
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ version_no: 2 }))
  })

  it('toggling a category off removes its tools from the saved selection', async () => {
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', () =>
        HttpResponse.json({ data: { valid: true, capabilities: [] }, message: 'ok' })),
    )
    let savedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/agents/a-1/versions', async ({ request }) => {
        savedBody = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { version_id: 'v-2', version_no: 2, config_hash: 'd' + '0'.repeat(63) }, message: 'ok' }, { status: 201 })
      }),
    )
    renderTab()
    await screen.findByTestId('ontology-picker')
    await pickOntology('Supply Ontology')
    await screen.findByTestId('ontology-tools-o-1')
    await waitFor(() => expect(screen.getByText(/Logic 规则 · rule-1/)).toBeTruthy())
    // disable the Logic category: its tool is removed and becomes read-only
    const logicCategory = screen.getByTestId('category-o-1-logic') as HTMLInputElement
    await userEvent.click(logicCategory)
    expect(logicCategory.checked).toBe(false)
    const logicTool = screen.getByText(/Logic 规则 · rule-1/).closest('label')!.querySelector('input') as HTMLInputElement
    expect(logicTool.checked).toBe(false)
    expect(logicTool.disabled).toBe(true)
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(savedBody).not.toBeNull())
    const bindings = savedBody!.ontology_bindings as { selected_tools: string[]; enabled_categories: string[] }[]
    expect(new Set(bindings[0].selected_tools)).toEqual(new Set(['query:o-1', 'action:action-1']))
    expect(bindings[0].enabled_categories).not.toContain('logic')
  })

  it('binds at most one ontology: the picker locks once bound, unbind to switch', async () => {
    toolsHandlers()
    server.use(
      http.get('*/api/v1/ontologies/o-2/tools', () =>
        HttpResponse.json({ data: { ontology_id: 'o-2', published: true, release_id: 'r-2', tools: [
          { descriptor_id: 'query:o-2', version: 1, source_kind: 'builtin', source_id: 'query',
            capability: 'read_instances', timeout_ms: 10000, result_limit: 10, descriptor_hash: 'k' + '0'.repeat(63) },
        ] }, message: 'ok' })),
      http.post('*/api/v1/agents/a-1/tool-validation', () =>
        HttpResponse.json({ data: { valid: true, capabilities: [] }, message: 'ok' })),
    )
    renderTab()
    await screen.findByTestId('ontology-picker')
    await pickOntology('Supply Ontology')
    expect(await screen.findByTestId('ontology-tools-o-1')).toBeTruthy()
    // the ontology carries ALL categories by default
    expect((screen.getByTestId('category-o-1-query') as HTMLInputElement).checked).toBe(true)
    // once bound, the picker locks — an Agent binds at most one ontology
    expect((screen.getByTestId('ontology-picker') as HTMLSelectElement).disabled).toBe(true)
    // unbind, then a different ontology can be picked — it REPLACES, not adds
    await userEvent.click(screen.getByRole('button', { name: '解绑' }))
    expect(screen.queryByTestId('ontology-tools-o-1')).toBeNull()
    expect((screen.getByTestId('ontology-picker') as HTMLSelectElement).disabled).toBe(false)
    await pickOntology('Policy Ontology')
    expect(await screen.findByTestId('ontology-tools-o-2')).toBeTruthy()
    expect(screen.queryByTestId('ontology-tools-o-1')).toBeNull()
    expect((screen.getByTestId('ontology-picker') as HTMLSelectElement).disabled).toBe(true)
  })

  it('restores persisted bindings from the active version', async () => {
    let validated: Record<string, unknown> | null = null
    toolsHandlers()
    server.use(
      http.post('*/api/v1/agents/a-1/tool-validation', async ({ request }) => {
        validated = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { valid: true, capabilities: [] }, message: 'ok' })
      }),
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
    const dirty = vi.fn()
    const { unmount } = render(<ToolConfigTab agentId="a-1" activeVersion={versionWithBinding} canEdit
      onSaved={vi.fn()} onDirtyChange={dirty} />)
    expect(await screen.findByTestId('ontology-tools-o-1')).toBeTruthy()
    // dirty only when the selection actually changed
    await waitFor(() => expect(dirty).toHaveBeenLastCalledWith(false))
    // settle the pending validation request before teardown
    await waitFor(() => expect(validated).not.toBeNull())
    expect(validated).toMatchObject({ ontology_ids: ['o-1'] })
    unmount()
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
    await screen.findByTestId('ontology-picker')
    await pickOntology('Supply Ontology')
    await userEvent.click(await screen.findByRole('button', { name: '查看能力交集' }))
    expect(await screen.findByTestId('capability-drawer')).toBeTruthy()
    // the drawer chip + the query-tool capability row may both show the token
    expect(screen.getAllByText('read_instances').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('traverse_relations').length).toBeGreaterThanOrEqual(1)
  })

  it('gates binding to editors (viewer sees a disabled dropdown)', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: PUBLISHED, next_cursor: null, has_more: false }, message: 'ok' })),
    )
    renderTab({ canEdit: false })
    await screen.findByTestId('ontology-picker')
    expect((screen.getByTestId('ontology-picker') as HTMLSelectElement).disabled).toBe(true)
  })
})
