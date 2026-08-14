import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  agentReconciliationsApi,
  type ReconciliationCase,
  type ResolutionKind,
} from '@/api/agentReconciliations'

/** Operator reconciliation page (P5-OPSUI). Admin-only list/detail/resolve:
 * every case shows a redacted persisted timeline; resolution requires an
 * evidence note and runs the backend CAS.  No provider guessing, hidden
 * reasoning, or automatic uncertain replay — only the three operator choices
 * succeed/failed/retry are offered. */
export default function AgentReconciliationPage() {
  const { t } = useTranslation()
  const [cases, setCases] = useState<ReconciliationCase[] | null>(null)
  const [statusFilter, setStatusFilter] = useState('open')
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [evidence, setEvidence] = useState('')
  const [resolving, setResolving] = useState(false)
  const [conflict, setConflict] = useState('')

  const load = useCallback((status: string) => {
    void Promise.resolve().then(() => {
      setCases(null)
      setError('')
      setConflict('')
    })
    agentReconciliationsApi.list(status)
      .then(res => setCases(Array.isArray(res.items) ? res.items : []))
      .catch(err => {
        const code = (err as { error?: { code?: string } })?.error?.code ?? ''
        setError(code === 'NOT_AUTHORIZED' || code === 'FORBIDDEN' ? 'DENIED' : 'RECONCILIATION_LOAD_FAILED')
      })
  }, [])

  useEffect(() => {
    load(statusFilter)
  }, [load, statusFilter])

  const selectCase = (caseId: string) => {
    setSelectedId(prev => (prev === caseId ? null : caseId))
    setEvidence('')
    setConflict('')
  }

  const resolveCase = async (record: ReconciliationCase, resolution: ResolutionKind) => {
    if (!evidence.trim() || resolving) return
    setResolving(true)
    setConflict('')
    try {
      await agentReconciliationsApi.resolve(record.id, record.revision, resolution, evidence.trim())
      await load(statusFilter)
      setSelectedId(null)
      setEvidence('')
    } catch (err) {
      const code = (err as { error?: { code?: string } })?.error?.code ?? ''
      await load(statusFilter)
      setConflict(code || 'RESOLUTION_FAILED')
    } finally {
      setResolving(false)
    }
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600" role="alert" data-testid="reconciliation-error">
        <p>{error === 'DENIED'
          ? t('agent.recon.denied', '仅管理员可查看和解操作')
          : t('agent.recon.load_failed', '加载失败')}</p>
        {error !== 'DENIED' && (
          <button type="button" onClick={() => load(statusFilter)}
            className="mt-2 px-3 py-1 text-xs border border-red-300 rounded hover:bg-red-100">
            {t('agent.list.retry', 'Retry')}
          </button>
        )}
      </div>
    )
  }
  if (cases === null) {
    return <div className="p-6 text-gray-400" data-testid="reconciliation-loading">{t('common.loading', '加载中…')}</div>
  }

  return (
    <div data-testid="agent-reconciliation-page">
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-base font-medium">{t('agent.recon.title', 'Agent 和解操作')}</h2>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}
          className="ml-auto border rounded px-2 py-1 text-sm" data-testid="reconciliation-status-filter">
          <option value="open">{t('agent.recon.open', 'open')}</option>
          <option value="resolved_succeeded">resolved_succeeded</option>
          <option value="resolved_failed">resolved_failed</option>
          <option value="resolved_retry">resolved_retry</option>
        </select>
      </div>

      {cases.length === 0 ? (
        <p className="text-sm text-gray-400" data-testid="reconciliation-empty">{t('agent.recon.empty', '暂无案件')}</p>
      ) : (
        <ul className="space-y-2" data-testid="reconciliation-list">
          {cases.map(record => (
            <li key={record.id} className="border rounded-lg p-3" data-testid={`reconciliation-case-${record.id}`}>
              <button type="button" onClick={() => selectCase(record.id)} className="w-full text-left text-sm">
                <span className="font-mono text-xs text-gray-400">{record.id.slice(0, 8)}</span>
                <span className="ml-2">{record.execution_kind}</span>
                <span className="ml-2 text-xs text-gray-500">state={record.state} · rev={record.revision}</span>
              </button>

              {selectedId === record.id && (
                <div className="mt-3 border-t pt-3 text-xs space-y-3" data-testid="reconciliation-detail">
                  <dl className="grid grid-cols-2 gap-1 text-gray-600">
                    <dt>{t('agent.recon.turn', 'Turn')}</dt><dd className="font-mono">{record.turn_id}</dd>
                    <dt>{t('agent.recon.execution', 'Execution')}</dt><dd className="font-mono">{record.execution_id}</dd>
                    <dt>{t('agent.recon.request_hash', 'Request hash')}</dt><dd className="font-mono">{record.request_hash ? record.request_hash.slice(0, 16) + '…' : '—'}</dd>
                  </dl>

                  {conflict && (
                    <p className="text-red-600" role="alert" data-testid="reconciliation-conflict">{conflict}</p>
                  )}

                  <label className="block">
                    <span className="text-gray-500">{t('agent.recon.evidence', 'Provider evidence')}</span>
                    <textarea value={evidence} onChange={e => setEvidence(e.target.value)}
                      data-testid="reconciliation-evidence"
                      className="mt-1 w-full border rounded p-2 text-sm"
                      placeholder={t('agent.recon.evidence_ph', '记录提供方证据…')} />
                  </label>

                  <div className="flex gap-2">
                    <button type="button" disabled={!evidence.trim() || resolving} onClick={() => resolveCase(record, 'retry')}
                      className="px-3 py-1 text-xs border rounded disabled:opacity-40" data-testid="resolve-retry">
                      {t('agent.recon.retry', 'Retry')}
                    </button>
                    <button type="button" disabled={!evidence.trim() || resolving} onClick={() => resolveCase(record, 'succeeded')}
                      className="px-3 py-1 text-xs bg-black text-white rounded disabled:opacity-40" data-testid="resolve-succeeded">
                      {t('agent.recon.succeeded', 'Confirmed result')}
                    </button>
                    <button type="button" disabled={!evidence.trim() || resolving} onClick={() => resolveCase(record, 'failed')}
                      className="px-3 py-1 text-xs border rounded disabled:opacity-40" data-testid="resolve-failed">
                      {t('agent.recon.failed', 'Confirmed not-run')}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
