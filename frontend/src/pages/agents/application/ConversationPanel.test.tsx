import '@/i18n'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import ConversationPanel from './ConversationPanel'
import type { AgentMessage } from '@/api/agentSessions'
import { initialStreamState, type StreamState } from '@/api/agentStream'

function renderPanel(messages: AgentMessage[], stream: StreamState = initialStreamState) {
  return render(<ConversationPanel messages={messages} stream={stream} clarification={null}
    pendingApprovalId={null} onSend={() => {}} onAnswerClarification={() => {}}
    onApprovalResolved={() => {}} onRetry={() => {}} />)
}

describe('P4A-CONVERSATION', () => {
  it('strips the legacy canned "Answer for …" prefix from persisted messages', () => {
    const messages: AgentMessage[] = [{
      id: 'm-1', session_id: 's-1', turn_id: 't-1', role: 'assistant',
      ordinal: 2, content: 'Answer for 库存低于安全线的订单有哪些？',
    }]
    renderPanel(messages)
    // the prefix is never rendered — only the actual answer text
    expect(screen.getByText('库存低于安全线的订单有哪些？')).toBeTruthy()
    expect(screen.queryByText(/Answer for/)).toBeNull()
  })

  it('renders a normal assistant answer verbatim', () => {
    const messages: AgentMessage[] = [{
      id: 'm-2', session_id: 's-1', turn_id: 't-1', role: 'assistant',
      ordinal: 2, content: '真实回答：库存充足',
    }]
    renderPanel(messages)
    expect(screen.getByText('真实回答：库存充足')).toBeTruthy()
  })

  it('renders the streamed final_response message without the prefix', () => {
    const stream: StreamState = {
      ...initialStreamState, phase: 'terminal', terminal: true,
      events: [{ event: 'final_response', data: { message: 'Answer for 问题' }, sequence: 2 }],
    }
    renderPanel([], stream)
    expect(screen.getByText('问题')).toBeTruthy()
  })
})
