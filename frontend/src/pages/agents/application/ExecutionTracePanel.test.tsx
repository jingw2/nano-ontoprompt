import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import ExecutionTracePanel from './ExecutionTracePanel'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function eventsBody(items: unknown[]) {
  return { data: { items, next_cursor: null, has_more: false }, message: 'ok' }
}

describe('P4B-LINEAGE', () => {
  it('P4B-LINEAGE red contract', () => {
    const failures: string[] = []
    for (const p of [
      'src/pages/agents/application/ExecutionTracePanel.tsx',
      'src/pages/agents/application/OntologyAccessPanel.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P4B_LINEAGE: ' + failures.join('; '))
  })

  it('renders the persisted event timeline with redacted payloads', async () => {
    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json(eventsBody([
        { id: 'e1', turn_id: 't-1', sequence: 1, event_type: 'turn_started', payload: {} },
        { id: 'e2', turn_id: 't-1', sequence: 2, event_type: 'model_call', payload: { model_name: 'gpt-4o', hidden_reasoning: 'top-secret' } },
        { id: 'e3', turn_id: 't-1', sequence: 3, event_type: 'final_response', payload: { message: 'done' } },
      ]))),
    )
    render(<ExecutionTracePanel turnId="t-1" />)
    await screen.findByTestId('execution-trace-panel')
    expect(screen.getByTestId('trace-event-1')).toBeTruthy()
    expect(screen.getByText('turn_started')).toBeTruthy()
    expect(screen.getByText('model_call')).toBeTruthy()
    expect(screen.getByText(/gpt-4o/)).toBeTruthy()
    // observable final message renders
    expect(screen.getByText(/message=done/)).toBeTruthy()
    // redaction: hidden reasoning must never render
    expect(screen.queryByText(/top-secret/)).toBeNull()
    expect(screen.queryByText(/hidden_reasoning/)).toBeNull()
  })

  it('shows loading, empty and error states', async () => {
    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json(eventsBody([]))),
    )
    const { unmount } = render(<ExecutionTracePanel turnId="t-1" />)
    expect(screen.getByTestId('execution-trace-loading')).toBeTruthy()
    await screen.findByTestId('execution-trace-empty')
    unmount()

    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json({ error: { code: 'x' } }, { status: 500 })),
    )
    render(<ExecutionTracePanel turnId="t-1" />)
    expect(await screen.findByTestId('execution-trace-error')).toBeTruthy()
    expect(screen.getByText('EVENTS_LOAD_FAILED')).toBeTruthy()
  })

  it('keeps events sorted by sequence', async () => {
    server.use(
      http.get('*/api/v1/agent-turns/t-1/events*', () => HttpResponse.json(eventsBody([
        { id: 'e3', turn_id: 't-1', sequence: 3, event_type: 'final_response', payload: {} },
        { id: 'e1', turn_id: 't-1', sequence: 1, event_type: 'turn_started', payload: {} },
        { id: 'e2', turn_id: 't-1', sequence: 2, event_type: 'model_call', payload: {} },
      ]))),
    )
    render(<ExecutionTracePanel turnId="t-1" />)
    await screen.findByTestId('execution-trace-panel')
    const items = [...document.querySelectorAll('[data-testid^="trace-event-"]')]
    expect(items.map(el => el.getAttribute('data-testid'))).toEqual([
      'trace-event-1', 'trace-event-2', 'trace-event-3',
    ])
  })
})
