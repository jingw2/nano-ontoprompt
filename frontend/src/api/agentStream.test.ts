import { describe, expect, it } from 'vitest'
import { initialStreamState, parseSseChunk, streamReducer } from './agentStream'

describe('P4B-STREAMUI agentStream byte parser', () => {
  it('parses a complete SSE block', () => {
    const { events, rest } = parseSseChunk('event: model_call\ndata: {"m":1}\n\n')
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('model_call')
    expect(events[0].data).toEqual({ m: 1 })
    expect(rest).toBe('')
  })

  it('handles every byte split boundary (partial chunks)', () => {
    const full = 'event: final_response\ndata: {"message":"hi"}\n\n'
    let rest = ''
    let count = 0
    for (const byte of full) {
      rest += byte
      const { events, rest: r } = parseSseChunk(rest)
      rest = r
      count += events.length
    }
    expect(count).toBe(1)
    expect(rest).toBe('')
  })

  it('handles CRLF line endings', () => {
    const { events } = parseSseChunk('event: turn_started\r\ndata: {}\r\n\r\n')
    expect(events[0].event).toBe('turn_started')
  })

  it('joins multi-line data blocks into one JSON payload', () => {
    const { events } = parseSseChunk('data: {"a":1,\ndata: "b":2}\n\n')
    expect(events[0].data).toEqual({ a: 1, b: 2 })
  })

  it('ignores comment lines', () => {
    const { events } = parseSseChunk(': keepalive\nevent: message\ndata: {}\n\n')
    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('message')
  })

  it('reducer accumulates events with monotonic sequence', () => {
    let state = initialStreamState
    state = streamReducer(state, { event: 'model_call', data: {}, sequence: 1 })
    state = streamReducer(state, { event: 'final_response', data: { message: 'x' }, sequence: 2 })
    expect(state.lastSequence).toBe(2)
    expect(state.events).toHaveLength(2)
    expect(state.phase).toBe('streaming')
  })

  it('reducer transitions to terminal/clarification/error phases', () => {
    let state = initialStreamState
    state = streamReducer(state, { event: 'request_clarification', data: {}, sequence: 1 })
    expect(state.phase).toBe('clarification')
    state = streamReducer(state, { event: 'terminal', data: {}, sequence: 2 })
    expect(state.phase).toBe('terminal')
    expect(state.terminal).toBe(true)
    state = streamReducer(state, { event: 'gap', data: {} })
    expect(state.phase).toBe('error')
    expect(state.error).toBe('SEQUENCE_GAP')
  })
})
