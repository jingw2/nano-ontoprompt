import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import {
  agentApprovalsApi,
  type ApprovalRecord,
  type ApprovalResolutionResult,
} from '@/api/agentApprovals'

/** Governed action approval card (P5-UI). Renders the immutable approval —
 * exact preview hash, revision and designated actor — and offers only the two
 * governed decisions (approve/reject) to the exact designated actor.  No
 * parameter editing, no local authority, no re-preview.  Stale/expired/
 * already-resolved approvals surface their state with a retry. */
export default function ActionApprovalCard(
  { approvalId, onApprovalResolved }: { approvalId: string; onApprovalResolved: (result: ApprovalResolutionResult) => void },
) {
  const { t } = useTranslation()
  const actorId = useAuthStore(s => s.user?.id ?? null)
  const [approval, setApproval] = useState<ApprovalRecord | null>(null)
  const [error, setError] = useState('')
  const [resolving, setResolving] = useState(false)
  const [result, setResult] = useState<ApprovalResolutionResult | null>(null)

  const load = useCallback(() => {
    void Promise.resolve().then(() => {
      setError('')
      setResult(null)
    })
    agentApprovalsApi.get(approvalId)
      .then(setApproval)
      .catch(err => {
        const code = (err as { error?: { code?: string } })?.error?.code ?? ''
        setError(code || 'APPROVAL_LOAD_FAILED')
      })
  }, [approvalId])

  useEffect(() => {
    load()
  }, [load])

  const decide = async (decision: 'approved' | 'rejected') => {
    if (!approval || resolving) return
    setResolving(true)
    try {
      const outcome = decision === 'approved'
        ? await agentApprovalsApi.approve(approval.id, approval.revision, approval.preview_hash)
        : await agentApprovalsApi.reject(approval.id, approval.revision, approval.preview_hash)
      setResult(outcome)
      setApproval(prev => (prev ? { ...prev, status: decision } : prev))
      onApprovalResolved(outcome)
    } catch (err) {
      const code = (err as { error?: { code?: string } })?.error?.code ?? ''
      setError(code || 'APPROVAL_RESOLVE_FAILED')
      await load()
    } finally {
      setResolving(false)
    }
  }

  if (error) {
    return (
      <div className="border rounded-lg p-3 text-sm" role="alert" data-testid="approval-card-error">
        <p className="text-red-600">{error}</p>
        <button type="button" onClick={load}
          className="mt-2 px-3 py-1 text-xs border rounded hover:bg-gray-50">
          {t('agent.list.retry', 'Retry')}
        </button>
      </div>
    )
  }
  if (approval === null) {
    return <div className="text-xs text-gray-400" data-testid="approval-card-loading">{t('common.loading', '加载中…')}</div>
  }

  const stale = approval.status === 'stale' || approval.status === 'expired'
    || Boolean(approval.stale_reason)
    || ['approved', 'rejected', 'consumed'].includes(approval.status)
  const isDesignated = actorId === approval.designated_actor_id

  return (
    <div className="border rounded-lg p-3" data-testid="action-approval-card">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium">{t('agent.app.approval', 'Action Approval')}</h4>
        <span className="text-xs text-gray-500" data-testid="approval-status">{approval.status}</span>
      </div>

      <dl className="mt-2 text-xs text-gray-600 space-y-1">
        <div className="flex gap-2">
          <dt>{t('agent.app.approval_hash', 'Preview hash')}</dt>
          <dd className="font-mono" data-testid="approval-preview-hash">{approval.preview_hash.slice(0, 16)}…</dd>
        </div>
        <div className="flex gap-2">
          <dt>{t('agent.app.approval_revision', 'Revision')}</dt>
          <dd data-testid="approval-revision">{approval.revision}</dd>
        </div>
        <div className="flex gap-2">
          <dt>{t('agent.app.approval_actor', 'Designated actor')}</dt>
          <dd className="font-mono">{approval.designated_actor_id.slice(0, 8)}</dd>
        </div>
      </dl>

      {result ? (
        <div className="mt-3 text-xs" data-testid="approval-result">
          <p data-testid="approval-result-status">{t('agent.app.approval_resumed', '审批已提交，Turn 继续执行')}</p>
          <p className="text-gray-500">correlation_id: <span className="font-mono">{result.correlation_id}</span></p>
        </div>
      ) : stale ? (
        <div className="mt-3 text-xs text-amber-700" data-testid="approval-stale">
          <p>{approval.stale_reason ?? t('agent.app.approval_stale', '该审批已过期或已处理')}</p>
          <button type="button" onClick={load}
            className="mt-2 px-3 py-1 text-xs border rounded hover:bg-gray-50">
            {t('agent.app.approval_refresh', 'Refresh')}
          </button>
        </div>
      ) : isDesignated ? (
        <div className="mt-3 flex gap-2">
          <button type="button" disabled={resolving} onClick={() => decide('approved')}
            className="px-3 py-1 text-xs bg-black text-white rounded disabled:opacity-40" data-testid="approve-action">
            {t('agent.app.approve', 'Approve')}
          </button>
          <button type="button" disabled={resolving} onClick={() => decide('rejected')}
            className="px-3 py-1 text-xs border rounded disabled:opacity-40" data-testid="reject-action">
            {t('agent.app.reject', 'Reject')}
          </button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-gray-400" data-testid="approval-awaiting-actor">
          {t('agent.app.approval_awaiting', '等待指定审批人处理')}
        </p>
      )}
    </div>
  )
}
