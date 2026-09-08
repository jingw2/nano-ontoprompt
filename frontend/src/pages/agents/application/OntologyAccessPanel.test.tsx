import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import OntologyAccessPanel from './OntologyAccessPanel'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function eventsBody(items: unknown[]) {
  return { data: { items, next_cursor: null, has_more: false }, message: 'ok' }
}

describe('P4B-LINEAGE', () => {
  it('P4B-LINEAGE red contract (ontology access)', () => {
    if (!existsSync(resolve(__dirname, '../../../../src/pages/agents/application/OntologyAccessPanel.tsx'))) {
      throw new Error('RED_P4B_LINEAGE: missing OntologyAccessPanel.tsx')
    }
  })

  it('renders redacted release lineage citations from persisted events', async () => {
    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json(eventsBody([
        {
          id: 'e1', turn_id: 't-1', sequence: 1, event_type: 'assemble_context',
          payload: { citations: [{ type: 'release', release_id: 'rel-12345678-abcd', version_no: 3, entities: 12, relations: 5 }] },
        },
        {
          id: 'e2', turn_id: 't-1', sequence: 2, event_type: 'model_call',
          payload: { model_name: 'gpt-4o', hidden_reasoning: 'chain-of-thought-here' },
        },
      ]))),
    )
    render(<OntologyAccessPanel turnId="t-1" />)
    expect(await screen.findByTestId('ontology-access-panel')).toBeTruthy()
    expect(screen.getByTestId('lineage-citation')).toBeTruthy()
    // redacted: release id is truncated, entity/relation counts are visible
    expect(screen.getByText(/rel-1234/)).toBeTruthy()
    expect(screen.getByText(/12 entities/)).toBeTruthy()
    expect(screen.getByText(/5 relations/)).toBeTruthy()
    // no hidden reasoning, no synthetic lineage from non-citation payloads
    expect(screen.queryByText(/chain-of-thought-here/)).toBeNull()
    expect(screen.queryByText(/gpt-4o/)).toBeNull()
  })

  it('shows loading, empty and error states', async () => {
    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json(eventsBody([]))),
    )
    const { unmount } = render(<OntologyAccessPanel turnId="t-1" />)
    expect(screen.getByTestId('ontology-access-loading')).toBeTruthy()
    await screen.findByTestId('ontology-access-empty')
    unmount()

    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json({ error: { code: 'x' } }, { status: 500 })),
    )
    render(<OntologyAccessPanel turnId="t-1" />)
    expect(await screen.findByTestId('ontology-access-error')).toBeTruthy()
    expect(screen.getByText('LINEAGE_LOAD_FAILED')).toBeTruthy()
  })
})
