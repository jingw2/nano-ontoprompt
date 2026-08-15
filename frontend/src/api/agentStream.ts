import { useAuthStore } from '@/stores/authStore'

export interface StreamEvent {
  event: string
  data: Record<string, unknown>
  sequence?: number
}

export interface TurnAccepted {
  turn_id: string
  session_id: string
  status: string
  dispatch_generation: number
  correlation_id: string
  stream_ticket?: string | null
  stream_ticket_url?: string | null
}

export interface TurnStatus {
  turn_id: string
  session_id: string
  status: string
  dispatch_generation: number
  error_code?: string | null
}

export interface TurnEventItem {
  id: string
  turn_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
}

export interface TurnEventsPage {
  items: TurnEventItem[]
  next_cursor: number | null
  has_more: boolean
  terminal: boolean
}

export const agentStreamApi = {
  createTurn: (sessionId: string, userMessage: string, turnId?: string) =>
    fetch(`/api/v1/agent-sessions/${sessionId}/turns`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${useAuthStore.getState().token ?? ''}`,
      },
      body: JSON.stringify({ user_message: userMessage, turn_id: turnId }),
    }).then(r => r.json()).then(body => body.data as TurnAccepted),
  cancelTurn: (turnId: string) =>
    fetch(`/api/v1/agent-turns/${turnId}/cancel`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${useAuthStore.getState().token ?? ''}`,
      },
      body: JSON.stringify({}),
    }).then(r => r.json()).then(body => body.data as TurnAccepted),
  streamTicket: (turnId: string) =>
    fetch(`/api/v1/agent-turns/${turnId}/stream-ticket`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${useAuthStore.getState().token ?? ''}`,
      },
      body: JSON.stringify({}),
    }).then(r => r.json()).then(body => body.data as { ticket: string }),
  openTurnStream: (turnId: string, ticket: string, afterSeq?: number) => {
    const url = `/api/v1/agent-turns/${turnId}/stream?ticket=${encodeURIComponent(ticket)}${afterSeq !== undefined ? `&after_seq=${afterSeq}` : ''}`
    return fetch(url, {
      headers: { Authorization: `Bearer ${useAuthStore.getState().token ?? ''}` },
    })
  },
  /** Turn status for the polling fallback (the SSE channel is single-shot and
   * may close before the worker persists events). */
  getTurnStatus: (turnId: string) =>
    fetch(`/api/v1/agent-turns/${turnId}`, {
      headers: { Authorization: `Bearer ${useAuthStore.getState().token ?? ''}` },
    }).then(r => r.json()).then(body => body.data as TurnStatus),
  /** Persisted events after a cursor for the polling fallback. */
  turnEvents: (turnId: string, afterSeq: number) =>
    fetch(`/api/v1/agent-turns/${turnId}/events?after_seq=${afterSeq}`, {
      headers: { Authorization: `Bearer ${useAuthStore.getState().token ?? ''}` },
    }).then(r => r.json()).then(body => body.data as TurnEventsPage),
}

/** Parse one SSE chunk into typed events. Every byte boundary is handled:
 *  CRLF/LF splits, multi-line data, and partial chunks are re-buffered by
 *  the caller. */
export function parseSseChunk(buffer: string): { events: StreamEvent[]; rest: string } {
  const events: StreamEvent[] = []
  let rest = buffer
  // Normalize CRLF to LF for split handling
  const normalized = rest.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  rest = blocks.pop() ?? '' // last block may be partial
  for (const block of blocks) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      else if (line.startsWith(':')) continue // comment
    }
    if (dataLines.length === 0) continue
    let data: Record<string, unknown>
    try {
      data = JSON.parse(dataLines.join('\n'))
    } catch {
      data = { raw: dataLines.join('\n') }
    }
    events.push({ event, data, sequence: typeof data.sequence === 'number' ? data.sequence : undefined })
  }
  return { events, rest }
}

export type StreamPhase = 'idle' | 'connecting' | 'streaming' | 'clarification' | 'terminal' | 'error'

export interface StreamState {
  phase: StreamPhase
  events: StreamEvent[]
  lastSequence: number
  terminal: boolean
  error?: string | null
}

export const initialStreamState: StreamState = {
  phase: 'idle',
  events: [],
  lastSequence: 0,
  terminal: false,
  error: null,
}

export function streamReducer(state: StreamState, event: StreamEvent): StreamState {
  const seq = event.sequence ?? state.lastSequence + 1
  const next: StreamState = {
    ...state,
    events: [...state.events, event],
    lastSequence: Math.max(state.lastSequence, seq),
  }
  if (event.event === 'terminal') {
    next.phase = 'terminal'
    next.terminal = true
  } else if (event.event === 'gap') {
    next.phase = 'error'
    next.error = 'SEQUENCE_GAP'
  } else if (event.event === 'request_clarification') {
    next.phase = 'clarification'
  } else if (state.phase === 'connecting' || state.phase === 'idle') {
    next.phase = 'streaming'
  }
  return next
}
