import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import {
  agentApprovalsApi,
  type ApprovalRecord,
  type ApprovalResolutionResult,
} from '@/api/agentApprovals'
import { apiClientV2 } from '@/api/client'
import { ontologyApi } from '@/api/ontologies'
import type { RuntimeEventRecord } from './ExecutionTracePanel'

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

// ---------------------------------------------------------------------------
// GovernedPlanPanel — the business-journey high-risk plan branches
// (`POST /api/v2/runtime/action-plans/from-turn` and its `/decide` sibling,
// `app.services.runtime.turn_plans`). This is a SEPARATE governance surface
// from the legacy card above: `GovernedTurnPlan` proposals are created FROM
// a plain conversational turn's own persisted tool evidence and decided by
// the signed-in browser session directly (`get_current_user`), never
// through the delegated Runtime credential the legacy `agentApprovals`
// flow above uses — so this panel talks to `apiClientV2` (ordinary session
// bearer), never `runtimeApiClient` (delegated bearer).

type GovernedPlanBranch = 'approved' | 'rejected' | 'expired'
const GOVERNED_PLAN_BRANCHES: readonly GovernedPlanBranch[] = ['approved', 'rejected', 'expired']

interface GovernedBranchState {
  targetId: string
  planId: string
  planHash: string
  approvalId: string
  status: string
  receiptId?: string
  beforeHash?: string
  afterHash?: string
}

interface CreatedPlanResponse {
  action_plan_id: string
  plan_hash: string
  approval_id: string
  status: string
}

interface DecidedPlanResponse {
  status: string
  receipt_id: string | null
  target_before_hash: string
  target_after_hash: string
}

/** Python `json.dumps(value, ensure_ascii=False, sort_keys=True, ...)`
 * equivalent, in the two exact separator styles this backend uses for its
 * two different SHA-256 digests (`app.services.runtime.turn_plans` uses
 * compact `(",", ":")`; `app.runtime.langgraph_runtime`'s tool-result hash
 * uses the DEFAULT `(", ", ": ")`). Object keys are sorted recursively,
 * matching `sort_keys=True`; strings are never ASCII-escaped, matching
 * `ensure_ascii=False`. Numeric formatting can diverge from Python's float
 * repr at the margins — this codebase's digest inputs here are ids/hashes/
 * branch names (always strings) plus whatever `row_data` a tool result
 * echoed back, so this is a best-effort, not a byte-for-byte guarantee for
 * arbitrary numeric fixture data. */
function pythonJsonDumps(value: unknown, opts: { compact: boolean }): string {
  const itemSep = opts.compact ? ',' : ', '
  const kvSep = opts.compact ? ':' : ': '
  const ser = (v: unknown): string => {
    if (v === null || v === undefined) return 'null'
    if (typeof v === 'boolean' || typeof v === 'number') return JSON.stringify(v)
    if (typeof v === 'string') return JSON.stringify(v)
    if (Array.isArray(v)) return `[${v.map(ser).join(itemSep)}]`
    if (typeof v === 'object') {
      const obj = v as Record<string, unknown>
      const keys = Object.keys(obj).sort()
      return `{${keys.map(k => `${JSON.stringify(k)}${kvSep}${ser(obj[k])}`).join(itemSep)}}`
    }
    throw new Error(`UNSERIALIZABLE_VALUE_${typeof v}`)
  }
  return ser(value)
}

async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text)
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest)).map(b => b.toString(16).padStart(2, '0')).join('')
}

/** Creates, then approves/rejects/expires, three independent
 * `GovernedTurnPlan` instances from the SAME succeeded turn's persisted
 * tool evidence — one per branch, each against its own disposable target
 * instance so no plan/approval/target identity is ever reused. */
export function GovernedPlanPanel(
  { turnId, ontologyId, events }: { turnId: string; ontologyId: string | null; events: RuntimeEventRecord[] },
) {
  const { t } = useTranslation()
  const [candidateTargets, setCandidateTargets] = useState<string[] | null>(null)
  const [activeBranch, setActiveBranch] = useState<GovernedPlanBranch | null>(null)
  const [selectedTarget, setSelectedTarget] = useState<Partial<Record<GovernedPlanBranch, string>>>({})
  const [branchState, setBranchState] = useState<Partial<Record<GovernedPlanBranch, GovernedBranchState>>>({})
  const [decidingBranch, setDecidingBranch] = useState<GovernedPlanBranch | null>(null)
  const [lastDecidedBranch, setLastDecidedBranch] = useState<GovernedPlanBranch | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  // Real, distinct disposable-fixture EntityInstance ids under the Agent's
  // bound ontology — one per branch — read through the SAME ontology-admin
  // instance listing the entity browser uses (never a client-side guess);
  // `create_governed_plan_from_turn` independently re-validates that each
  // target actually belongs to the bound, published ontology.
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => { if (!cancelled) setCandidateTargets(null) })
    if (!ontologyId) return () => { cancelled = true }
    ontologyApi.listEntities(ontologyId)
      .then(async entities => {
        const collected: string[] = []
        for (const entity of entities) {
          if (collected.length >= GOVERNED_PLAN_BRANCHES.length) break
          try {
            const rows = await ontologyApi.listEntityInstances(ontologyId, entity.id)
            for (const row of rows) {
              if (collected.length >= GOVERNED_PLAN_BRANCHES.length) break
              if (!collected.includes(row.id)) collected.push(row.id)
            }
          } catch { /* this entity type's instances are unreadable — try the next one */ }
        }
        if (!cancelled) setCandidateTargets(collected)
      })
      .catch(() => { if (!cancelled) setCandidateTargets([]) })
    return () => { cancelled = true }
  }, [ontologyId])

  const targetFor = (branch: GovernedPlanBranch): string | null =>
    candidateTargets?.[GOVERNED_PLAN_BRANCHES.indexOf(branch)] ?? null

  const submitPlan = useCallback(async () => {
    if (!activeBranch || busy) return
    const branch = activeBranch
    const targetId = selectedTarget[branch]
    if (!targetId) { setError('SELECT_TARGET_FIRST'); return }
    const finalEvent = events.find(e => e.event_type === 'final_response')
    const toolEvent = events.find(e => e.event_type === 'tool_executed')
    if (!finalEvent || !toolEvent) { setError('TURN_EVIDENCE_NOT_READY'); return }
    const toolExecutionId = String(toolEvent.payload.tool_execution_id ?? '')
    if (!toolExecutionId) { setError('TOOL_EVIDENCE_MISSING'); return }
    const items = Array.isArray(toolEvent.payload.items) ? toolEvent.payload.items : []
    const itemCount = typeof toolEvent.payload.item_count === 'number' ? toolEvent.payload.item_count : items.length
    if (itemCount > items.length) {
      // The persisted trace event bounds `items` to 5 (`_bound_summary`) —
      // beyond that this panel cannot reconstruct the exact bytes the
      // server hashed, so it fails closed rather than guessing.
      setError('TOOL_RESULT_TRUNCATED_CANNOT_RECONSTRUCT_DIGEST')
      return
    }
    setBusy(true)
    setError('')
    try {
      const resultPayload = { items, correlation_id: toolEvent.payload.correlation_id ?? null }
      const toolResultHash = await sha256Hex(pythonJsonDumps(resultPayload, { compact: false }))
      const responseContent = String(finalEvent.payload.message ?? '')
      const finalResponseHash = await sha256Hex(responseContent)
      const payloadDigest = await sha256Hex(pythonJsonDumps({
        turn_id: turnId, tool_execution_id: toolExecutionId, tool_result_hash: toolResultHash,
        final_response_hash: finalResponseHash, branch, target_fixture_id: targetId,
      }, { compact: true }))
      const idempotencyKey = `${turnId}:${branch}:${crypto.randomUUID()}`
      const created = await apiClientV2.post<CreatedPlanResponse>('/runtime/action-plans/from-turn', {
        turn_id: turnId, branch, target_fixture_id: targetId,
        idempotency_key: idempotencyKey, payload_digest: payloadDigest,
      })
      setBranchState(prev => ({
        ...prev,
        [branch]: {
          targetId, planId: created.action_plan_id, planHash: created.plan_hash,
          approvalId: created.approval_id, status: created.status,
        },
      }))
    } catch {
      setError('PLAN_CREATE_FAILED')
    } finally {
      setBusy(false)
    }
  }, [activeBranch, busy, selectedTarget, events, turnId])

  const decide = useCallback(async (branch: GovernedPlanBranch, decision: GovernedPlanBranch) => {
    const state = branchState[branch]
    if (!state || busy) return
    setBusy(true)
    setError('')
    try {
      const decided = await apiClientV2.post<DecidedPlanResponse>(
        `/runtime/action-plans/from-turn/${state.planId}/decide`,
        { decision, plan_hash: state.planHash },
      )
      setBranchState(prev => ({
        ...prev,
        [branch]: {
          ...(prev[branch] as GovernedBranchState),
          status: decided.status,
          receiptId: decided.receipt_id ?? undefined,
          beforeHash: decided.target_before_hash,
          afterHash: decided.target_after_hash,
        },
      }))
      setLastDecidedBranch(branch)
    } catch {
      setError('PLAN_DECIDE_FAILED')
    } finally {
      setBusy(false)
    }
  }, [branchState, busy])

  const canSubmit = activeBranch !== null && Boolean(selectedTarget[activeBranch]) && !busy

  return (
    <div className="border rounded-lg p-3 mt-3 space-y-3" data-testid="governed-plan-panel">
      <h4 className="text-sm font-medium">{t('agent.app.governed_plans', 'High-risk plan branches')}</h4>
      {error && <p className="text-xs text-red-600" role="alert">{error}</p>}

      <div className="flex gap-2 flex-wrap">
        {GOVERNED_PLAN_BRANCHES.map(branch => (
          <button key={branch} type="button" data-testid={`high-risk-plan-create-${branch}`}
            onClick={() => { setActiveBranch(branch); setError('') }}
            className={`px-3 py-1 text-xs border rounded ${activeBranch === branch ? 'bg-black text-white' : 'hover:bg-gray-50'}`}>
            {branch}
          </button>
        ))}
      </div>

      <div className="flex gap-2 flex-wrap">
        {GOVERNED_PLAN_BRANCHES.map(branch => {
          const targetId = targetFor(branch)
          return (
            <button key={branch} type="button" data-testid={`high-risk-target-${branch}`}
              data-target-id={targetId ?? ''} disabled={!targetId || activeBranch !== branch}
              onClick={() => { if (targetId) setSelectedTarget(prev => ({ ...prev, [branch]: targetId })) }}
              className="px-3 py-1 text-xs border rounded hover:bg-gray-50 disabled:opacity-40 font-mono">
              {targetId ? targetId.slice(0, 12) : '…'}
            </button>
          )
        })}
      </div>

      <button type="button" data-testid="high-risk-plan-submit" disabled={!canSubmit} onClick={submitPlan}
        className="px-3 py-1.5 text-xs bg-black text-white rounded disabled:opacity-40">
        {t('agent.app.submit_plan', 'Submit plan')}
      </button>

      {GOVERNED_PLAN_BRANCHES.map(branch => {
        const state = branchState[branch]
        if (!state) return null
        return (
          <div key={branch} className="border-t pt-2 text-xs space-y-1">
            <p>
              {branch}: <span data-testid={`journey-approval-status-${branch}`}>{state.status}</span>
            </p>
            <p className="font-mono break-all">
              plan <span data-testid={`high-risk-plan-id-${branch}`}>{state.planId}</span>
              {' · hash '}<span data-testid={`high-risk-plan-hash-${branch}`}>{state.planHash}</span>
              {' · approval '}<span data-testid={`high-risk-approval-id-${branch}`}>{state.approvalId}</span>
            </p>
            <button type="button" data-testid={`approval-branch-${branch}`}
              onClick={() => setDecidingBranch(branch)}
              className="px-2 py-1 border rounded hover:bg-gray-50">
              {t('agent.app.review_branch', 'Review')}
            </button>
            {state.beforeHash && state.afterHash && (
              <p className="font-mono break-all" data-testid={`journey-target-hash-${branch}`}
                data-before={state.beforeHash} data-after={state.afterHash}>
                {state.beforeHash.slice(0, 8)} → {state.afterHash.slice(0, 8)}
              </p>
            )}
            {branch === 'approved' && state.receiptId && (
              <p data-testid="journey-hitl-receipt-approved">{state.receiptId}</p>
            )}
          </div>
        )
      })}

      {decidingBranch && (
        <div className="border-t pt-2 flex gap-2">
          {decidingBranch === 'approved' && (
            <button type="button" data-testid="approve-action" disabled={busy}
              onClick={() => decide('approved', 'approved')}
              className="px-3 py-1 text-xs bg-black text-white rounded disabled:opacity-40">
              {t('agent.app.approve', 'Approve')}
            </button>
          )}
          {decidingBranch === 'rejected' && (
            <button type="button" data-testid="reject-action" disabled={busy}
              onClick={() => decide('rejected', 'rejected')}
              className="px-3 py-1 text-xs border rounded disabled:opacity-40">
              {t('agent.app.reject', 'Reject')}
            </button>
          )}
          {decidingBranch === 'expired' && (
            <button type="button" data-testid="expire-action" disabled={busy}
              onClick={() => decide('expired', 'expired')}
              className="px-3 py-1 text-xs border rounded disabled:opacity-40">
              {t('agent.app.expire', 'Expire')}
            </button>
          )}
        </div>
      )}

      {lastDecidedBranch && (
        <p data-testid="journey-branch-audit" className="text-xs text-gray-500">
          {lastDecidedBranch}: {branchState[lastDecidedBranch]?.receiptId ?? branchState[lastDecidedBranch]?.status ?? ''}
        </p>
      )}
    </div>
  )
}
