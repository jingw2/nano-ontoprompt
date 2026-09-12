import '@/i18n'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import OntologyToolSelector from './OntologyToolSelector'
import type { OntologyBinding, PublishedOntology, ToolDescriptor } from '@/api/agentTools'

const ONTOLOGIES: PublishedOntology[] = [{ id: 'o-1', name: 'Supply Ontology', status: 'published' }]
const TOOLS: ToolDescriptor[] = [
  { descriptor_id: 'logic:rule-1', version: 1, source_kind: 'logic', source_id: 'rule-1',
    capability: 'execute_read_logic', timeout_ms: 10000, result_limit: 1, descriptor_hash: 'h' + '0'.repeat(63) },
]

describe('OntologyToolSelector', () => {
  it('lists pickable ontologies and calls onBind when one is chosen', async () => {
    const onBind = vi.fn()
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[]} toolsByOntology={{}} canEdit
      onBind={onBind} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    await userEvent.selectOptions(screen.getByTestId('ontology-picker'), 'o-1')
    expect(onBind).toHaveBeenCalledWith('o-1')
  })

  it('renders a bound ontology panel and toggles a category', async () => {
    const onToggleCategory = vi.fn()
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: ['logic'],
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]}
      toolsByOntology={{ 'o-1': TOOLS }} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={onToggleCategory} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    expect(screen.getByTestId('ontology-tools-o-1')).toBeTruthy()
    await userEvent.click(screen.getByTestId('category-o-1-write'))
    expect(onToggleCategory).toHaveBeenCalledWith('o-1', 'write', true)
  })

  it('collapses tool items by category by default and expands on click', async () => {
    const onToggleTool = vi.fn()
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: ['logic'],
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]}
      toolsByOntology={{ 'o-1': TOOLS }} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={onToggleTool} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    // category header with count is visible, but the item checkbox is not, until expanded
    expect(screen.getByTestId('category-expand-o-1-logic')).toBeTruthy()
    expect(screen.getByTestId('category-expand-o-1-logic').textContent).toContain('(1)')
    expect(screen.queryByText('execute_read_logic')).toBeNull()
    await userEvent.click(screen.getByTestId('category-expand-o-1-logic'))
    expect(screen.getByText('execute_read_logic')).toBeTruthy()
  })

  it('shows the ontology-defined business rule/action name instead of a raw id', async () => {
    const named: ToolDescriptor[] = [
      { descriptor_id: 'logic:rule-1', version: 1, source_kind: 'logic', source_id: 'rule-1',
        name: '订单超额审批规则',
        capability: 'execute_read_logic', timeout_ms: 10000, result_limit: 1, descriptor_hash: 'h' + '0'.repeat(63) },
      { descriptor_id: 'action:act-1', version: 1, source_kind: 'action', source_id: 'act-1',
        name: 'ApproveOrder',
        capability: 'execute_instance_action', timeout_ms: 10000, result_limit: 1, descriptor_hash: 'h' + '1'.repeat(63) },
    ]
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: ['logic', 'action'],
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]}
      toolsByOntology={{ 'o-1': named }} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    await userEvent.click(screen.getByTestId('category-expand-o-1-logic'))
    await userEvent.click(screen.getByTestId('category-expand-o-1-action'))
    expect(screen.getByText('订单超额审批规则')).toBeTruthy()
    expect(screen.getByText('ApproveOrder')).toBeTruthy()
    expect(screen.queryByText(/rule-1/)).toBeNull()
    expect(screen.queryByText(/act-1/)).toBeNull()
  })

  it('falls back to a source_kind:id label when a descriptor has no name (pre-migration manifest)', async () => {
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: ['logic'],
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]}
      toolsByOntology={{ 'o-1': TOOLS }} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    await userEvent.click(screen.getByTestId('category-expand-o-1-logic'))
    expect(screen.getByText('logic:rule-1')).toBeTruthy()
  })

  it('disables the picker and unbind button when canEdit is false', () => {
    const binding: OntologyBinding = {
      ontology_id: 'o-1', capabilities: [], allowlists: {}, selected_tools: [], enabled_categories: null,
    }
    render(<OntologyToolSelector ontologies={ONTOLOGIES} bindings={[binding]} toolsByOntology={{}} canEdit={false}
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    expect((screen.getByTestId('ontology-picker') as HTMLSelectElement).disabled).toBe(true)
    expect((screen.getByText('解绑') as HTMLButtonElement).disabled).toBe(true)
  })

  it('suppresses the "no ontologies" message when the caller has its own fetch error', () => {
    render(<OntologyToolSelector ontologies={[]} bindings={[]} toolsByOntology={{}} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()}
      error="AGENTS_TOOLS_CATALOG_FAILED" />)
    expect(screen.queryByText('没有可绑定的已发布本体')).toBeNull()
  })

  it('shows the "no ontologies" message when the list is empty and there is no error', () => {
    render(<OntologyToolSelector ontologies={[]} bindings={[]} toolsByOntology={{}} canEdit
      onBind={vi.fn()} onUnbind={vi.fn()} onToggleCategory={vi.fn()} onToggleTool={vi.fn()} onSetToolCatalogLimit={vi.fn()} onSetEntitySearchDepth={vi.fn()} />)
    expect(screen.getByText('没有可绑定的已发布本体')).toBeTruthy()
  })
})
