import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentSessionsApi, type AgentMessage, type AgentSession } from '@/api/agentSessions'
import { agentClarificationsApi } from '@/api/agentClarifications'
import type { ApprovalResolutionResult } from '@/api/agentApprovals'
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

const TURN_POLL_INTERVAL_MS = 1500
const TURN_TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])

export default function AgentApplicationTab({ agentId }: Props) {
  const { t } = useTranslation()
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<AgentMessage[]>([])
  const [stream, setStream] = useState<StreamState>(initialStreamState)
  const [clarification, setClarification] = useState<{ id: string; question: string; baseRevision: number } | null>(null)
  const [pendingApprovalId, setPendingApprovalId] = useState<string | null>(null)
  const [reconnectTick, setReconnectTick] = useState(0)
  // the most recent Turn: drives the persisted-event trace / ontology-access panels
  const [lastTurnId, setLastTurnId] = useState<string | null>(null)
  const [traceOpen, setTraceOpen] = useState(false)
  // the single-shot SSE channel has closed (possibly empty, before the worker
  // persisted events) — the polling fallback takes over from here
  const [sseDone, setSseDone] = useState(false)
  const lastSeqRef = useRef(0)

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
    setPendingApprovalId(null)
    setStream(initialStreamState)
    setLastTurnId(null)
    setSseDone(false)
    agentSessionsApi.messages(sessionId).then(async res => {
      const items = Array.isArray(res.items) ? res.items : []
      setMessages(items)
      // a session reopened after its last Turn failed has a dangling user
      // message with no assistant reply — without this check it looks
      // exactly like the agent silently never responded, with no error
      // shown (only a Turn that fails while the tab is open surfaces one)
      const last = items[items.length - 1]
      if (last?.role === 'user' && last.turn_id) {
        setLastTurnId(last.turn_id)
        try {
          const status = await agentStreamApi.getTurnStatus(last.turn_id)
          if (status.status === 'failed') {
            setStream({ ...initialStreamState, terminal: true, phase: 'error',
                        error: status.error_code ?? 'TURN_FAILED' })
          }
        } catch { /* transient — leave the composer usable, no error shown */ }
      }
    }).catch(() => setMessages([]))
  }, [])

  const newSession = useCallback(() => {
    agentSessionsApi.create(agentId).then(session => {
      setSessions(prev => [session, ...prev])
      selectSession(session.id)
    }).catch(() => {})
  }, [agentId, selectSession])

  const openStream = useCallback(async (tid: string, ticket: string, afterSeq?: number) => {
    try {
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
            if (event.event === 'approval_required') {
              const a = event.data as { approval_id?: string }
              setPendingApprovalId(String(a.approval_id ?? ''))
            }
            if (event.event === 'terminal' && afterSeq !== undefined) setReconnectTick(n => n + 1)
            return next
          })
        }
      }
    } finally {
      // single-shot channel closed (possibly empty): the polling fallback now
      // owns fetching the persisted status/events until the Turn is terminal
      setSseDone(true)
    }
  }, [])

  const sendMessage = useCallback((text: string) => {
    if (!activeSessionId) return
    setStream({ ...initialStreamState, phase: 'connecting' })
    setSseDone(false)
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

  // keep the polling cursor in sync with the latest seen sequence
  useEffect(() => {
    lastSeqRef.current = stream.lastSequence
  }, [stream.lastSequence])

  // polling fallback: the single-shot SSE can close before the worker
  // persists events, so while a Turn is in flight and the channel is closed
  // (not terminal), poll the turn status + events until terminal, then reload
  // messages and re-enable the composer — the answer appears without a manual
  // reload even when the stream delivered nothing.
  useEffect(() => {
    if (!lastTurnId || !activeSessionId || !sseDone || stream.terminal) return
    const timer = setInterval(async () => {
      try {
        const status = await agentStreamApi.getTurnStatus(lastTurnId)
        if (TURN_TERMINAL_STATUSES.has(status.status)) {
          setStream(prev => prev.terminal ? prev : {
            ...prev,
            terminal: true,
            phase: status.status === 'failed' ? 'error' : 'terminal',
            ...(status.status === 'failed' ? { error: status.error_code ?? 'TURN_FAILED' } : {}),
          })
          return
        }
        // not terminal yet: pull any newly persisted events for live deltas
        const page = await agentStreamApi.turnEvents(lastTurnId, lastSeqRef.current)
        if (page.items.length > 0 || page.terminal) {
          setStream(prev => {
            let next = prev
            for (const item of page.items) {
              if (item.sequence <= prev.lastSequence) continue
              next = streamReducer(next, { event: item.event_type, data: item.payload, sequence: item.sequence })
            }
            if (page.terminal && !next.terminal) {
              next = streamReducer(next, { event: 'terminal', data: { turn_id: lastTurnId } })
            }
            lastSeqRef.current = next.lastSequence
            return next
          })
        }
      } catch {
        // transient (worker still warming up / network): keep polling
      }
    }, TURN_POLL_INTERVAL_MS)
    return () => clearInterval(timer)
  }, [lastTurnId, activeSessionId, sseDone, stream.terminal])

  const answerClarification = useCallback(async (answer: string) => {
    if (!clarification) return
    const result = await agentClarificationsApi.answer(clarification.id, clarification.baseRevision, answer)
    setClarification(null)
    setSseDone(false)
    if (result.turn_id) {
      setLastTurnId(result.turn_id)
      const ticket = await agentStreamApi.streamTicket(result.turn_id)
      await openStream(result.turn_id, ticket.ticket)
    }
  }, [clarification, openStream])

  const onApprovalResolved = useCallback(async (result: ApprovalResolutionResult) => {
    // ActionApprovalCard sets its own local "approval-result" confirmation in
    // the same synchronous continuation as this callback; a bare
    // `setTimeout(0)` only separates that commit from the unmount below by a
    // sub-millisecond window, too narrow to reliably observe (flaky under
    // test). 50ms guarantees the confirmation actually commits/renders
    // before the card unmounts, and doubles as a brief, visible confirmation
    // for the user.
    await new Promise(resolve => setTimeout(resolve, 50))
    setPendingApprovalId(null)
    setSseDone(false)
    if (result.turn_id) {
      setLastTurnId(result.turn_id)
      const ticket = await agentStreamApi.streamTicket(result.turn_id)
      await openStream(result.turn_id, ticket.ticket)
    }
  }, [openStream])

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
            pendingApprovalId={pendingApprovalId} onSend={sendMessage}
            onAnswerClarification={answerClarification} onApprovalResolved={onApprovalResolved}
            onRetry={retry} />
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
