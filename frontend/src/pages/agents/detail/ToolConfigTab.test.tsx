import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import ToolConfigTab from './ToolConfigTab'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const PUBLISHED = [
  { id: 'o-1', name: 'Supply Ontology', status: 'published' },
  { id: 'o-2', name: 'Policy Ontology', status: 'published' },
]

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
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: PUBLISHED, next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/agents/a-1/tool-validation', async ({ request }) => {
        validated = await request.json() as Record<string, unknown>
        return HttpResponse.json({ data: { valid: true, capabilities: ['read_instances', 'traverse_relations'] }, message: 'ok' })
      }),
    )
    render(<ToolConfigTab agentId="a-1" canEdit onDirtyChange={vi.fn()} />)
    expect(await screen.findByText('Supply Ontology')).toBeTruthy()
    expect(screen.getByText('Policy Ontology')).toBeTruthy()
    await userEvent.click(screen.getAllByRole('button', { name: '绑定' })[0])
    await waitFor(() => expect(validated).not.toBeNull())
    expect(validated).toMatchObject({ ontology_ids: ['o-1'] })
    expect(await screen.findByText('绑定配置有效')).toBeTruthy()
  })

  it('shows external tool cards as unavailable and issues zero P7 requests', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    render(<ToolConfigTab agentId="a-1" canEdit onDirtyChange={vi.fn()} />)
    await waitFor(() => expect(screen.getByTestId('external-tool-cards')).toBeTruthy())
    const cards = screen.getAllByTestId('external-tool-card')
    expect(cards.length).toBeGreaterThanOrEqual(3)
    expect(screen.getAllByText('Available later').length).toBeGreaterThanOrEqual(3)
    // onUnhandledRequest: 'error' above would fail the test on any P7 call
  })

  it('shows the capability intersection drawer', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: PUBLISHED, next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/agents/a-1/tool-validation', () =>
        HttpResponse.json({ data: { valid: true, capabilities: ['read_instances', 'traverse_relations'] }, message: 'ok' })),
    )
    render(<ToolConfigTab agentId="a-1" canEdit onDirtyChange={vi.fn()} />)
    await userEvent.click((await screen.findAllByRole('button', { name: '绑定' }))[0])
    await userEvent.click(await screen.findByRole('button', { name: '查看能力交集' }))
    expect(await screen.findByTestId('capability-drawer')).toBeTruthy()
    expect(screen.getByText('read_instances')).toBeTruthy()
    expect(screen.getByText('traverse_relations')).toBeTruthy()
  })

  it('gates binding to editors (viewer sees read-only cards)', async () => {
    server.use(
      http.get('*/api/v1/agents/catalog/ontologies', () =>
        HttpResponse.json({ data: { items: PUBLISHED, next_cursor: null, has_more: false }, message: 'ok' })),
    )
    render(<ToolConfigTab agentId="a-1" canEdit={false} onDirtyChange={vi.fn()} />)
    await screen.findByText('Supply Ontology')
    expect((screen.getAllByRole('button', { name: '绑定' })[0] as HTMLButtonElement).disabled).toBe(true)
  })
})
