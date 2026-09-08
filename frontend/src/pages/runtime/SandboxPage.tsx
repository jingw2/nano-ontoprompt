import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { runtimeApi } from '@/api/runtime'
import type { SandboxResult } from '@/types/runtime'

/**
 * Task 27: read-only Sandbox simulation surface
 * (`GET /api/v2/runtime/action-plans/{planId}/sandbox`). Displays the
 * server-computed before/after diff, impact summary, rule outcomes, and
 * policy result exactly as returned — this page never simulates or
 * re-derives anything client-side.
 */
export default function SandboxPage() {
  const { t } = useTranslation()
  const { planId } = useParams<{ planId: string }>()
  const [result, setResult] = useState<SandboxResult | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!planId) return
    runtimeApi.getSandboxSimulation(planId)
      .then(setResult)
      .catch(() => setError(t('runtime.sandbox.load_failed', 'Failed to load the sandbox simulation')))
  }, [planId, t])

  if (error) return <p className="text-sm text-red-600" role="alert" data-testid="sandbox-error">{error}</p>
  if (!result) return <div className="p-6 text-gray-400" data-testid="sandbox-loading">{t('common.loading', 'Loading…')}</div>

  return (
    <div data-testid="sandbox-page">
      <h2 className="text-base font-medium mb-3">{t('runtime.sandbox.title', 'Sandbox Simulation')}</h2>
      <dl className="grid grid-cols-2 gap-1 text-sm mb-4">
        <dt className="text-gray-500">{t('runtime.sandbox.simulation_id', 'Simulation ID')}</dt>
        <dd className="font-mono" data-testid="sandbox-simulation-id">{result.simulation_id}</dd>
        <dt className="text-gray-500">{t('runtime.sandbox.snapshot', 'Snapshot')}</dt>
        <dd className="font-mono">{result.semantic_snapshot_id}</dd>
        <dt className="text-gray-500">{t('runtime.sandbox.expected_rows', 'Expected rows')}</dt>
        <dd data-testid="sandbox-expected-rows">{result.expected_rows}</dd>
        <dt className="text-gray-500">{t('runtime.sandbox.expires_at', 'Expires at')}</dt>
        <dd>{result.expires_at}</dd>
      </dl>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="border rounded-lg p-2">
          <p className="text-gray-500 mb-1">{t('runtime.sandbox.diff', 'Before/after diff')}</p>
          <pre className="whitespace-pre-wrap break-all" data-testid="sandbox-diff">{JSON.stringify(result.before_after_diff, null, 2)}</pre>
        </div>
        <div className="border rounded-lg p-2">
          <p className="text-gray-500 mb-1">{t('runtime.sandbox.impact', 'Impact summary')}</p>
          <pre className="whitespace-pre-wrap break-all" data-testid="sandbox-impact-summary">{JSON.stringify(result.impact_summary, null, 2)}</pre>
        </div>
        <div className="border rounded-lg p-2">
          <p className="text-gray-500 mb-1">{t('runtime.sandbox.rule_outcome', 'Rule outcome')}</p>
          <pre className="whitespace-pre-wrap break-all" data-testid="sandbox-rule-outcome">{JSON.stringify(result.rule_outcome, null, 2)}</pre>
        </div>
        <div className="border rounded-lg p-2">
          <p className="text-gray-500 mb-1">{t('runtime.sandbox.policy_result', 'Policy result')}</p>
          <pre className="whitespace-pre-wrap break-all" data-testid="sandbox-policy-result">{JSON.stringify(result.policy_result, null, 2)}</pre>
        </div>
      </div>
    </div>
  )
}
