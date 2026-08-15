import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentSessionsApi, type AgentMessage, type AgentSession } from '@/api/agentSessions'
import { agentClarificationsApi } from '@/api/agentClarifications'
import {
  agentStreamApi, initialStreamState, parseSseChunk, streamReducer,
  type StreamState,
} from '@/api/agentStream'
import SessionSidebar from './SessionSidebar'
import ConversationPanel from './ConversationPanel'
import ExecutionTracePanel from './ExecutionTracePanel'
import OntologyAccessPanel from './OntologyAccessPanel'

interface Props {
  agentId: string
}

export default function AgentApplicationTab({ agentId }: Props) {
  const { t } = useTranslation()
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [stream, setStream] = useState<StreamState>(initialStreamState)
  const [clarification, setClarification] = useState<{ id: string; question: string; baseRevision: number } | null>(null)
  const [reconnectTick, setReconnectTick] = useState(0)
  // the most recent Turn: drives the persisted-event trace / ontology-access panels
  const [lastTurnId, setLastTurnId] = useState<string | null>(null)
  const [traceOpen, setTraceOpen] = useState(false)

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
    setLastTurnId(null)
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
        setLastTurnId(accepted.turn_id)
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
      setLastTurnId(result.turn_id)
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
    <div className="flex flex-col lg:flex-row h-[calc(100vh-220px)] border rounded-lg overflow-hidden relative" data-testid="agent-application-tab">
      <div className="flex flex-col flex-1 min-w-0">
        <div className="flex items-center justify-end px-3 py-1.5 border-b bg-gray-50 shrink-0">
          <button type="button" onClick={() => setTraceOpen(o => !o)} disabled={!lastTurnId}
            className="px-3 py-1 text-xs border rounded hover:bg-gray-100 disabled:opacity-40"
            data-testid="trace-toggle">
            {lastTurnId
              ? (traceOpen ? t('agent.app.trace_close', '关闭执行轨迹') : t('agent.app.trace_open', '执行轨迹'))
              : t('agent.app.trace_unavailable', '执行轨迹（暂无 Turn）')}
          </button>
        </div>
        <div className="flex flex-1 min-h-0">
          <SessionSidebar sessions={sessions} activeSessionId={activeSessionId}
            onSelect={selectSession} onNew={newSession} />
          <ConversationPanel messages={messages} stream={stream} clarification={clarification}
            onSend={sendMessage} onAnswerClarification={answerClarification} onRetry={retry} />
        </div>
      </div>
      {traceOpen && lastTurnId && (
        <div className="w-full max-h-72 overflow-y-auto border-t lg:absolute lg:inset-y-0 lg:right-0 lg:w-80 lg:border-l lg:bg-white lg:shadow-xl lg:max-h-none xl:static xl:shadow-none"
          data-testid="trace-panel">
          <ExecutionTracePanel turnId={lastTurnId} />
          <OntologyAccessPanel turnId={lastTurnId} />
        </div>
      )}
    </div>
  )
}
