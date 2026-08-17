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
    await userEvent.click(screen.getByRole('button', { name: '+ 新会话' }))
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
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
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('SEQUENCE_GAP')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
  })

  it('shows an error for a session reopened after its last Turn failed', async () => {
    // the session's last message is a dangling user message with no
    // assistant reply — this happens when the Turn failed; without checking
    // its status, reopening the session looks exactly like the agent never
    // responded, with no error shown at all
    server.use(
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [SESSION], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agent-sessions/s-1/messages', () =>
        HttpResponse.json({
          data: {
            items: [
              { id: 'u-1', session_id: 's-1', turn_id: 't-1', role: 'user', ordinal: 1, content: 'hello', created_at: 'x' },
            ],
            next_cursor: null, has_more: false,
          },
          message: 'ok',
        })),
      http.get('*/api/v1/agent-turns/t-1', () =>
        HttpResponse.json({ data: { turn_id: 't-1', session_id: 's-1', status: 'failed',
                                    error_code: 'TOOL_ROUND_LIMIT', dispatch_generation: 1 }, message: 'ok' })),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    await userEvent.click(screen.getByText(/s-1/))
    expect(await screen.findByRole('alert')).toBeTruthy()
    expect(screen.getByText('TOOL_ROUND_LIMIT')).toBeTruthy()
  })

  it('does not show an error for a session that ended in a real assistant reply', async () => {
    server.use(
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [SESSION], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agent-sessions/s-1/messages', () =>
        HttpResponse.json({
          data: {
            items: [
              { id: 'u-1', session_id: 's-1', turn_id: 't-1', role: 'user', ordinal: 1, content: 'hello', created_at: 'x' },
              { id: 'a-1', session_id: 's-1', turn_id: 't-1', role: 'assistant', ordinal: 2, content: '你好', created_at: 'x' },
            ],
            next_cursor: null, has_more: false,
          },
          message: 'ok',
        })),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    await userEvent.click(screen.getByText(/s-1/))
    await waitFor(() => expect(screen.getByTestId('conversation-panel')).toBeTruthy())
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('toggles the execution trace and ontology access panels after a turn', async () => {
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
        const body = 'event: model_call\ndata: {}\n\nevent: final_response\ndata: {"message":"done"}\n\nevent: terminal\ndata: {}\n\n'
        return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } })
      }),
      http.get('*/api/v1/agent-turns/t-1/events*', () =>
        HttpResponse.json({
          data: {
            items: [
              { id: 'e1', turn_id: 't-1', sequence: 1, event_type: 'turn_started', payload: {} },
              { id: 'e2', turn_id: 't-1', sequence: 2, event_type: 'final_response', payload: { message: 'done' } },
            ],
            next_cursor: null, has_more: false,
          },
          message: 'ok',
        })),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    // no turn yet — the trace toggle is disabled
    expect(screen.getByTestId('trace-toggle')).toHaveProperty('disabled', true)
    await userEvent.click(screen.getByText(/s-1/))
    await waitFor(() => expect(screen.getByTestId('conversation-panel')).toBeTruthy())
    await userEvent.type(screen.getByPlaceholderText(/输入消息/), 'hello')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    // turn completed → toggle enables; the reasoning/trace panel stays
    // COLLAPSED by default until the user opens it
    await waitFor(() => expect(screen.getByTestId('trace-toggle')).toHaveProperty('disabled', false))
    expect(screen.queryByTestId('trace-panel')).toBeNull()
    await userEvent.click(screen.getByTestId('trace-toggle'))
    expect(await screen.findByTestId('trace-panel')).toBeTruthy()
    expect(await screen.findByTestId('execution-trace-panel')).toBeTruthy()
    expect(screen.getByTestId('ontology-access-panel')).toBeTruthy()
    expect(screen.getByText('turn_started')).toBeTruthy()
    // observable final message renders
    expect(screen.getByText(/message=done/)).toBeTruthy()
    // collapsing hides the panel again
    await userEvent.click(screen.getByTestId('trace-toggle'))
    await waitFor(() => expect(screen.queryByTestId('trace-panel')).toBeNull())
  })

  it('refreshes the conversation via the polling fallback when the SSE stream closes empty', async () => {
    let messagesCalls = 0
    let statusCalls = 0
    server.use(
      http.get('*/api/v1/agents/a-1/sessions', () =>
        HttpResponse.json({ data: { items: [SESSION], next_cursor: null, has_more: false }, message: 'ok' })),
      http.get('*/api/v1/agent-sessions/s-1/messages', () => {
        messagesCalls += 1
        // the reload after the terminal poll returns the assistant answer
        const items = messagesCalls >= 2
          ? [
              { id: 'u-1', session_id: 's-1', turn_id: 't-1', role: 'user', ordinal: 1, content: 'hello', created_at: 'x' },
              { id: 'a-1', session_id: 's-1', turn_id: 't-1', role: 'assistant', ordinal: 2, content: '轮询得到的答案', created_at: 'x' },
            ]
          : []
        return HttpResponse.json({ data: { items, next_cursor: null, has_more: false }, message: 'ok' })
      }),
      http.post('*/api/v1/agent-sessions/s-1/turns', () =>
        HttpResponse.json({ data: { turn_id: 't-1', session_id: 's-1', status: 'queued', dispatch_generation: 1, correlation_id: 'c' }, message: 'ok' }, { status: 202 })),
      http.post('*/api/v1/agent-turns/t-1/stream-ticket', () =>
        HttpResponse.json({ data: { turn_id: 't-1', ticket: 'tk', expires_at: 'x', stream_ticket_url: 'u' }, message: 'ok' }, { status: 201 })),
      // the single-shot SSE gap: the channel opens and closes EMPTY before the
      // worker persists any event (no terminal on the wire)
      http.get('*/api/v1/agent-turns/t-1/stream*', () =>
        new HttpResponse(': channel closed\n\n', { headers: { 'Content-Type': 'text/event-stream' } })),
      // polling fallback: queued on the first tick, succeeded afterwards
      http.get('*/api/v1/agent-turns/t-1', () => {
        statusCalls += 1
        return HttpResponse.json({ data: { turn_id: 't-1', session_id: 's-1', status: statusCalls >= 2 ? 'succeeded' : 'queued', dispatch_generation: 1 }, message: 'ok' })
      }),
      http.get('*/api/v1/agent-turns/t-1/events*', () =>
        HttpResponse.json({
          data: {
            items: [
              { id: 'e1', turn_id: 't-1', sequence: 4, event_type: 'final_response', payload: { message: '轮询得到的答案' } },
            ],
            next_cursor: 4, has_more: false, terminal: false,
          },
          message: 'ok',
        })),
    )
    render(<AgentApplicationTab agentId="a-1" />)
    await screen.findByTestId('session-sidebar')
    await userEvent.click(screen.getByText(/s-1/))
    await waitFor(() => expect(screen.getByTestId('conversation-panel')).toBeTruthy())
    await userEvent.type(screen.getByPlaceholderText(/输入消息/), 'hello')
    await userEvent.click(screen.getByRole('button', { name: '发送' }))
    // the SSE delivered nothing; the polling fallback surfaces the answer
    expect(await screen.findByText('轮询得到的答案', {}, { timeout: 5000 })).toBeTruthy()
    // the turn reaches terminal via the poll: the composer becomes usable
    // again without a manual reload
    await waitFor(() => expect(screen.queryByTestId('stream-indicator')).toBeNull(), { timeout: 6000 })
    await userEvent.type(screen.getByPlaceholderText(/输入消息/), '再问一句')
    expect((screen.getByRole('button', { name: '发送' }) as HTMLButtonElement).disabled).toBe(false)
  })
})
