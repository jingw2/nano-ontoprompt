import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import AgentApplicationTab from './AgentApplicationTab'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const SESSION = { id: 's-1', agent_id: 'a-1', owner_user_id: 'u-1', status: 'active' }

describe('P4B-STREAMUI', () => {
  it('red contract: requires the stream clients, tab, sidebar and panel', () => {
    const failures: string[] = []
    for (const p of [
      'src/api/agentSessions.ts', 'src/api/agentClarifications.ts', 'src/api/agentStream.ts',
      'src/pages/agents/application/SessionSidebar.tsx',
      'src/pages/agents/application/ConversationPanel.tsx',
      'src/pages/agents/application/AgentApplicationTab.tsx',
    ]) {
      if (!existsSync(resolve(__dirname, '../../../../' + p))) failures.push('missing ' + p)
    }
    if (failures.length) throw new Error('RED_P4B_STREAMUI: ' + failures.join('; '))
  })

  it('loads sessions and creates a new one', async () => {
    server.use(
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [SESSION], next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { ...SESSION, id: 's-2' }, message: 'ok' }, { status: 201 })),
      http.get('*/api/v1/agent-sessions/s-2/messages', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    await userEvent.click(screen.getByRole('button', { name: '+ New' }))
    expect(await screen.findByText(/s-2/)).toBeTruthy()
  })

  it('sends a message and streams events with clarification answer', async () => {
    let streamCount = 0
    server.use(
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [SESSION], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agent-sessions/s-1/messages', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/agent-sessions/s-1/turns', () =>
        HttpResponse.json({ data: { turn_id: 't-1', session_id: 's-1', status: 'queued', dispatch_generation: 1, correlation_id: 'c' }, message: 'ok' }, { status: 202 })),
      http.post('*/api/v1/agent-turns/t-1/stream-ticket', () =>
        HttpResponse.json({ data: { turn_id: 't-1', ticket: 'tk', expires_at: 'x', stream_ticket_url: 'u' }, message: 'ok' }, { status: 201 })),
      http.get('*/api/v1/agent-turns/t-1/stream*', () => {
        streamCount += 1
        const body = streamCount === 1
          ? [
              'event: model_call\ndata: {"m":1}\n\n',
              'event: request_clarification\ndata: {"clarification_id":"cl-1","question":"Which?","base_request_revision":1}\n\n',
              'event: terminal\ndata: {}\n\n',
            ].join('')
          : 'event: final_response\ndata: {"message":"done"}\n\nevent: terminal\ndata: {}\n\n'
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
      http.get('*/api/v1/agent-clarifications/cl-1', () =>
        HttpResponse.json({ data: { id: 'cl-1', turn_id: 't-1', question: 'Which?', base_request_revision: 1, status: 'pending' }, message: 'ok' })),
      http.post('*/api/v1/agent-clarifications/cl-1/answer', () =>
        HttpResponse.json({ data: { turn_id: 't-1', session_id: 's-1', status: 'queued', dispatch_generation: 2, correlation_id: 'c2' }, message: 'ok' }, { status: 202 })),
      http.post('*/api/v1/agent-turns/t-1/stream-ticket', () =>
        HttpResponse.json({ data: { turn_id: 't-1', ticket: 'tk2', expires_at: 'x', stream_ticket_url: 'u' }, message: 'ok' }, { status: 201 })),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    await userEvent.click(screen.getByText(/s-1/))
    await waitFor(() => expect(screen.getByTestId('conversation-panel')).toBeTruthy())
    await userEvent.type(screen.getByPlaceholderText(/输入消息/), 'hello')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByTestId('clarification-box')).toBeTruthy()
    expect(screen.getByText('Which?')).toBeTruthy()
    await userEvent.type(screen.getByPlaceholderText(/回答/), 'the blue one')
    await userEvent.click(screen.getByRole('button', { name: '回答' }))
    await waitFor(() => expect(screen.queryByTestId('clarification-box')).toBeNull())
  })

  it('shows the stream error with retry', async () => {
    server.use(
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [SESSION], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agent-sessions/s-1/messages', () =>
        HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })),
      http.post('*/api/v1/agent-sessions/s-1/turns', () =>
        HttpResponse.json({ data: { turn_id: 't-1', session_id: 's-1', status: 'queued', dispatch_generation: 1, correlation_id: 'c' }, message: 'ok' }, { status: 202 })),
      http.post('*/api/v1/agent-turns/t-1/stream-ticket', () =>
        HttpResponse.json({ data: { turn_id: 't-1', ticket: 'tk', expires_at: 'x', stream_ticket_url: 'u' }, message: 'ok' }, { status: 201 })),
      http.get('*/api/v1/agent-turns/t-1/stream*', () => {
        const body = 'event: gap\ndata: {}\n\n'
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    await userEvent.click(screen.getByText(/s-1/))
    await waitFor(() => expect(screen.getByTestId('conversation-panel')).toBeTruthy())
    await userEvent.type(screen.getByPlaceholderText(/输入消息/), 'hello')
    await userEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('SEQUENCE_GAP')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeTruthy()
  })
})
