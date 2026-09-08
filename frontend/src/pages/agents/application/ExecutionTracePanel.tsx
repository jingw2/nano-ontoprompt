import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiClient } from '@/api/client'

export interface RuntimeEventRecord {
  id: string
  turn_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  created_at?: string | null
}

/** Redaction whitelist: only observable fields may render — never hidden
 * reasoning, prompts, model outputs beyond the final message, or raw
 * payload keys the backend did not declare observable. */
const REDACTED_KEYS = new Set([
  'message', 'model_name', 'tool_alias', 'clarification_id', 'question',
  'approval_id', 'error_code',
  // Business-journey `model_call`/`tool_executed`/`resolve_snapshot`/
  // `final_response` payload fields (`app.runtime.langgraph_runtime`) —
  // every one of these is already a persisted, observable identifier or
  // counter, never a raw prompt/response.
  'citations', 'call_kind', 'logical_call_index', 'correlation_id',
  'model_caller', 'model_origin', 'requested_model', 'observed_model',
  'preflight_model_id', 'model_config_version_id', 'http_attempts', 'retry_count',
  'descriptor_id', 'outcome', 'tool_execution_id', 'item_count',
  'receipt_id', 'sandbox_receipt_id', 'audit_event_id', 'automatic_action',
])

function RedactedPayload({ payload }: { payload: Record<string, unknown> }) {
  const entries = Object.entries(payload).filter(([key]) => REDACTED_KEYS.has(key))
  if (entries.length === 0) return null
  return (
    <span className="text-gray-500 text-xs">
      {entries.map(([key, value]) => (
        <span key={key} className="ml-2" data-testid={`redacted-${key}`}>
          {key}={String(value ?? '')}
        </span>
      ))}
    </span>
  )
}

export default function ExecutionTracePanel({ turnId }: { turnId: string }) {
  const { t } = useTranslation()
  const [events, setEvents] = useState<RuntimeEventRecord[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      setEvents(null)
      setError('')
    })
    apiClient.get<{ items: RuntimeEventRecord[] }>(`/agent-turns/${turnId}/events?limit=100`)
      .then(res => {
        if (cancelled) return
        const items = Array.isArray(res.items) ? res.items : []
        setEvents([...items].sort((a, b) => a.sequence - b.sequence))
      })
      .catch(() => { if (!cancelled) setError('EVENTS_LOAD_FAILED') })
    return () => { cancelled = true }
  }, [turnId])

  if (error) {
    return <div className="text-xs text-red-600" role="alert" data-testid="execution-trace-error">{error}</div>
  }
  if (events === null) {
    return <div className="text-xs text-gray-400" data-testid="execution-trace-loading">{t('common.loading', '加载中…')}</div>
  }
  return (
    <div className="border-t p-3" data-testid="execution-trace-panel">
      <h4 className="text-xs font-medium mb-2">{t('agent.app.trace', 'Execution Trace')}</h4>
      {events.length === 0 ? (
        <p className="text-xs text-gray-400" data-testid="execution-trace-empty">{t('agent.app.trace_empty', '暂无事件')}</p>
      ) : (
        <ol className="space-y-1">
          {events.map(evt => (
            <li key={evt.id} className="text-xs flex items-baseline" data-testid={`trace-event-${evt.sequence}`}>
              <span className="text-gray-400 w-6 shrink-0">{evt.sequence}</span>
              <span className="font-medium">{evt.event_type}</span>
              <RedactedPayload payload={evt.payload} />
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
