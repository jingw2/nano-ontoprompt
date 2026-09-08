import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'

/**
 * Task 27: approval-queue landing surface (`/runtime/approvals`).
 *
 * The governed Runtime REST surface (Task 16/26A) exposes no "list pending
 * action plans" endpoint — only `GET /action-plans/{planId}` by exact id.
 * Rather than fabricate a client-side queue against data the backend never
 * returns, this page is a plan-id lookup that hands off to
 * `ActionPlanPage`, where the real review/approve/execute controls (backed
 * by the actual governed endpoints) live.
 */
export default function ApprovalQueuePage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [planId, setPlanId] = useState('')

  const open = () => {
    if (!planId.trim()) return
    navigate(`/runtime/action-plans/${planId.trim()}`)
  }

  return (
    <div data-testid="approval-queue-page">
      <h2 className="text-base font-medium mb-3">{t('runtime.approvals.title', 'Approval Queue')}</h2>
      <p className="text-xs text-gray-500 mb-3">
        {t('runtime.approvals.help', 'Enter a plan ID to review its evidence, risk classification, and exact plan hash before approving or executing it.')}
      </p>
      <div className="flex gap-2">
        <input value={planId} onChange={e => setPlanId(e.target.value)}
          placeholder={t('runtime.approvals.plan_id_placeholder', 'Plan ID')}
          data-testid="approval-queue-plan-id" className="border rounded px-2 py-1 text-sm flex-1" />
        <button type="button" onClick={open} data-testid="approval-queue-open"
          className="px-3 py-1.5 text-xs bg-black text-white rounded">
          {t('runtime.approvals.open', 'Open plan')}
        </button>
      </div>
    </div>
  )
}
