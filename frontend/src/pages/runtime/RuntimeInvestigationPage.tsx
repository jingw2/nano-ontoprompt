import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { runtimeApi } from '@/api/runtime'
import type { InvestigationResult } from '@/types/runtime'

/**
 * Task 27: operator-facing snapshot-pinned investigation surface
 * (`POST /api/v2/runtime/investigate`). Renders exactly what the server
 * returns — evidence citations, rule outcomes, freshness — and never
 * evaluates policy/authorization itself. A denial (`decision === 'DENY'`)
 * never carries `result`; an ALLOW always carries a collection (possibly
 * empty) — the UI must never conflate the two.
 */
export default function RuntimeInvestigationPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const [snapshotId, setSnapshotId] = useState(searchParams.get('snapshot_id') ?? '')
  const [ontologyId, setOntologyId] = useState(searchParams.get('ontology_id') ?? '')
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<InvestigationResult | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const runInvestigation = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await runtimeApi.investigate({
        semantic_snapshot_id: snapshotId,
        ontology_id: ontologyId,
        query: query || undefined,
      })
      setResult(res)
    } catch (err) {
      // A structured denial from the credential layer (401/403) arrives as
      // a thrown error body, not a 2xx `InvestigationResult` — surface it
      // the same way a 200 DENY result would render.
      const body = err as Partial<InvestigationResult> & { reason_code?: string }
      if (body && typeof body === 'object' && 'decision' in body) {
        setResult(body as InvestigationResult)
      } else {
        setError(t('runtime.investigate.load_failed', 'Investigation request failed'))
      }
    } finally {
      setLoading(false)
    }
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { void runInvestigation() }, [])

  return (
    <div data-testid="runtime-investigation-page">
      <h2 className="text-base font-medium mb-3">{t('runtime.investigate.title', 'Runtime Investigation')}</h2>

      <div className="grid grid-cols-3 gap-2 mb-3">
        <label className="block text-xs text-gray-500">
          {t('runtime.investigate.snapshot_id', 'Semantic snapshot ID')}
          <input value={snapshotId} onChange={e => setSnapshotId(e.target.value)}
            data-testid="investigate-snapshot-id" className="mt-1 w-full border rounded px-2 py-1 text-sm" />
        </label>
        <label className="block text-xs text-gray-500">
          {t('runtime.investigate.ontology_id', 'Ontology ID')}
          <input value={ontologyId} onChange={e => setOntologyId(e.target.value)}
            data-testid="investigate-ontology-id" className="mt-1 w-full border rounded px-2 py-1 text-sm" />
        </label>
        <label className="block text-xs text-gray-500">
          {t('runtime.investigate.query', 'Query')}
          <input value={query} onChange={e => setQuery(e.target.value)}
            data-testid="investigate-query" className="mt-1 w-full border rounded px-2 py-1 text-sm" />
        </label>
      </div>
      <button type="button" onClick={() => void runInvestigation()} disabled={loading}
        data-testid="run-investigation" className="px-3 py-1.5 text-xs bg-black text-white rounded disabled:opacity-40">
        {t('runtime.investigate.run', 'Run investigation')}
      </button>

      {error && <p className="mt-3 text-sm text-red-600" role="alert">{error}</p>}

      {result && (
        <div className="mt-4 border rounded-lg p-3 text-sm space-y-2" data-testid="investigation-result">
          <div>
            <span className="text-gray-500 mr-2">{t('runtime.investigate.decision', 'Decision')}</span>
            <span data-testid="investigation-decision" className={result.decision === 'ALLOW' ? 'text-green-700 font-medium' : 'text-red-700 font-medium'}>
              {result.decision}
            </span>
          </div>
          <div>
            <span className="text-gray-500 mr-2">{t('runtime.investigate.reason_code', 'Reason code')}</span>
            <span data-testid="investigation-reason-code" className="font-mono">{result.reason_code}</span>
          </div>
          {result.semantic_snapshot_id && (
            <div>
              <span className="text-gray-500 mr-2">{t('runtime.investigate.snapshot', 'Snapshot')}</span>
              <span data-testid="semantic-snapshot-id" className="font-mono">{result.semantic_snapshot_id}</span>
            </div>
          )}
          {result.correlation_id && (
            <div>
              <span className="text-gray-500 mr-2">{t('runtime.investigate.correlation_id', 'Correlation ID')}</span>
              <span data-testid="investigation-correlation-id" className="font-mono">{result.correlation_id}</span>
            </div>
          )}

          {result.decision === 'ALLOW' ? (
            <>
              <div data-testid="investigation-result-count" className="text-gray-500">
                {t('runtime.investigate.result_count', 'Rows returned')}: {(result.result ?? []).length}
              </div>
              {result.evidence_citations.length > 0 && (
                <ul data-testid="evidence-citations" className="list-disc pl-4 text-xs text-gray-600">
                  {result.evidence_citations.map((c, i) => (
                    <li key={i}>{c.source_type}:{c.source_id} · {c.locator}</li>
                  ))}
                </ul>
              )}
              {result.rule_outcome.length > 0 && (
                <ul data-testid="rule-outcomes" className="list-disc pl-4 text-xs text-gray-600">
                  {result.rule_outcome.map((r, i) => (
                    <li key={i}>{r.rule_id}: {r.result}</li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <p className="text-red-600 text-xs" data-testid="investigation-denied">
              {t('runtime.investigate.denied', 'This request was denied — no rows are returned.')}
            </p>
          )}
        </div>
      )}
    </div>
  )
}
