import { useCallback, useEffect, useState } from 'react'
import { agentSessionsApi, type AgentMessage, type AgentSession } from '@/api/agentSessions'
import { agentClarificationsApi } from '@/api/agentClarifications'
import {
  agentStreamApi, initialStreamState, parseSseChunk, streamReducer,
  type StreamState,
} from '@/api/agentStream'
import SessionSidebar from './SessionSidebar'
import ConversationPanel from './ConversationPanel'

interface Props {
  agentId: string
}

export default function AgentApplicationTab({ agentId }: Props) {
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [stream, setStream] = useState<StreamState>(initialStreamState)
  const [clarification, setClarification] = useState<{ id: string; question: string; baseRevision: number } | null>(null)
  const [reconnectTick, setReconnectTick] = useState(0)

  const loadSessions = useCallback(() => {
    agentSessionsApi.list(agentId).then(res => {
      setSessions(Array.isArray(res.items) ? res.items : [])
    }).catch(() => {})
  }, [agentId])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  const selectSession = useCallback((sessionId: string) => {
    setActiveSessionId(sessionId)
    setClarification(null)
    setStream(initialStreamState)
    agentSessionsApi.messages(sessionId).then(res => {
      setMessages(Array.isArray(res.items) ? res.items : [])
    }).catch(() => setMessages([]))
  }, [])

  const newSession = useCallback(() => {
    agentSessionsApi.create(agentId).then(session => {
      setSessions(prev => [session, ...prev])
      selectSession(session.id)
    }).catch(() => {})
  }, [agentId, selectSession])

  const sendMessage = useCallback((text: string) => {
    if (!activeSessionId) return
    setStream({ ...initialStreamState, phase: 'connecting' })
    agentStreamApi.createTurn(activeSessionId, text)
      .then(async accepted => {
        setMessages(prev => [...prev, {
          id: `local-${Date.now()}`, session_id: activeSessionId, role: 'user',
          ordinal: prev.length + 1, content: text,
        }])
        const ticket = await agentStreamApi.streamTicket(accepted.turn_id)
        await openStream(accepted.turn_id, ticket.ticket)
      })
      .catch(() => setStream({ ...initialStreamState, phase: 'error', error: 'TURN_CREATE_FAILED' }))
  }, [activeSessionId]) // eslint-disable-line react-hooks/exhaustive-deps

  const openStream = useCallback(async (tid: string, ticket: string, afterSeq?: number) => {
    const response = await agentStreamApi.openTurnStream(tid, ticket, afterSeq)
    if (!response.ok || !response.body) {
      setStream(s => ({ ...s, phase: 'error', error: 'STREAM_OPEN_FAILED' }))
      return
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    setStream(s => ({ ...s, phase: 'streaming' }))
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { events, rest } = parseSseChunk(buffer)
      buffer = rest
      for (const event of events) {
        setStream(prev => {
          const next = streamReducer(prev, event)
          if (event.event === 'request_clarification') {
            const q = event.data as { clarification_id?: string; question?: string; base_request_revision?: number }
            setClarification({
              id: String(q.clarification_id ?? ''),
              question: String(q.question ?? ''),
              baseRevision: Number(q.base_request_revision ?? 1),
            })
          }
          if (event.event === 'terminal' && afterSeq !== undefined) setReconnectTick(n => n + 1)
          return next
        })
      }
    }
  }, [])

  const answerClarification = useCallback(async (answer: string) => {
    if (!clarification) return
    const result = await agentClarificationsApi.answer(clarification.id, clarification.baseRevision, answer)
    setClarification(null)
    if (result.turn_id) {
      const ticket = await agentStreamApi.streamTicket(result.turn_id)
      await openStream(result.turn_id, ticket.ticket)
    }
  }, [clarification, openStream])

  const retry = useCallback(() => setReconnectTick(n => n + 1), [])

  // reconnect after terminal: reload messages
  useEffect(() => {
    if (activeSessionId && stream.terminal) {
      agentSessionsApi.messages(activeSessionId).then(res => {
        setMessages(Array.isArray(res.items) ? res.items : [])
      }).catch(() => {})
    }
  }, [reconnectTick, stream.terminal, activeSessionId])

  return (
    <div className="flex h-[calc(100vh-220px)] border rounded-lg overflow-hidden" data-testid="agent-application-tab">
      <SessionSidebar sessions={sessions} activeSessionId={activeSessionId}
        onSelect={selectSession} onNew={newSession} />
      <ConversationPanel messages={messages} stream={stream} clarification={clarification}
        onSend={sendMessage} onAnswerClarification={answerClarification} onRetry={retry} />
    </div>
  )
}
