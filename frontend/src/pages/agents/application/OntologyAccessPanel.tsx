import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { apiClient } from '@/api/client'
import type { RuntimeEventRecord } from './ExecutionTracePanel'

interface LineageCitation {
  type: string
  release_id?: string
  version_no?: number
  entities?: number
  relations?: number
}

/** Ontology access / lineage highlight. Renders ONLY the redacted citations
 * and lineage identifiers the backend placed in persisted event payloads —
 * never inferred graph access, synthetic events, or hidden reasoning. */
export default function OntologyAccessPanel({ turnId }: { turnId: string }) {
  const { t } = useTranslation()
  const [citations, setCitations] = useState<LineageCitation[] | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      setCitations(null)
      setError('')
    })
    apiClient.get<{ items: RuntimeEventRecord[] }>(`/agent-turns/${turnId}/events?limit=100`)
      .then(res => {
        if (cancelled) return
        const items = Array.isArray(res.items) ? res.items : []
        const found: LineageCitation[] = []
        for (const evt of items) {
          const raw = evt.payload?.citations
          if (Array.isArray(raw)) {
            for (const c of raw) {
              if (c && typeof c === 'object' && typeof (c as LineageCitation).type === 'string') {
                found.push(c as LineageCitation)
              }
            }
          }
        }
        setCitations(found)
      })
      .catch(() => { if (!cancelled) setError('LINEAGE_LOAD_FAILED') })
    return () => { cancelled = true }
  }, [turnId])

  if (error) {
    return <div className="text-xs text-red-600" role="alert" data-testid="ontology-access-error">{error}</div>
  }
  if (citations === null) {
    return <div className="text-xs text-gray-400" data-testid="ontology-access-loading">{t('common.loading', '加载中…')}</div>
  }
  return (
    <div className="border-t p-3" data-testid="ontology-access-panel">
      <h4 className="text-xs font-medium mb-2">{t('agent.app.ontology_access', 'Ontology Access')}</h4>
      {citations.length === 0 ? (
        <p className="text-xs text-gray-400" data-testid="ontology-access-empty">{t('agent.app.ontology_empty', '暂无血缘数据')}</p>
      ) : (
        <ul className="space-y-1">
          {citations.map((c, i) => (
            <li key={`${c.release_id ?? 'r'}-${i}`} className="text-xs text-gray-600" data-testid="lineage-citation">
              {t('agent.app.lineage_release', 'Release')} {c.release_id ? String(c.release_id).slice(0, 8) : '—'}
              {c.version_no != null ? ` v${c.version_no}` : ''}
              {c.entities != null ? ` · ${c.entities} entities` : ''}
              {c.relations != null ? ` · ${c.relations} relations` : ''}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
