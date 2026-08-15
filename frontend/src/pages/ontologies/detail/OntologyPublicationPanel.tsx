import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ontologyLifecycleApi,
  displayStatus,
  type OntologyReleaseSummary,
  type PublishReceipt,
} from '@/api/ontologyLifecycle'
import type { OntologyStatus } from '@/types/ontology'

interface Props {
  ontologyId: string
  status?: OntologyStatus | string
  isDirty?: boolean
  onStatusChange?: (status: string) => void
  onMutated?: () => void
}

interface PublishErrorFinding {
  code?: string
  path?: string
  message?: string
}

function errorFindings(err: unknown): PublishErrorFinding[] {
  const rec = err as { detail?: unknown; message?: unknown }
  if (Array.isArray(rec.detail)) return rec.detail as PublishErrorFinding[]
  if (rec?.detail && typeof rec.detail === 'object') {
    const detail = rec.detail as { code?: string; message?: string }
    if (detail.code || detail.message) return [detail]
  }
  return []
}

function errorMessage(err: unknown): string {
  const rec = err as { detail?: unknown; message?: unknown }
  if (typeof rec?.detail === 'string') return rec.detail
  if (typeof rec?.message === 'string') return rec.message
  return ''
}

export default function OntologyPublicationPanel({ ontologyId, status, isDirty = false, onStatusChange, onMutated }: Props) {
  const { t } = useTranslation()
  const [releases, setReleases] = useState<OntologyReleaseSummary[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [retryTick, setRetryTick] = useState(0)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [changelog, setChangelog] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [findings, setFindings] = useState<PublishErrorFinding[]>([])
  const [receipt, setReceipt] = useState<PublishReceipt | null>(null)
  const [runtimeDisabled, setRuntimeDisabled] = useState(false)

  const published = releases !== null && releases.length > 0
  const entityCount = receipt ? (Array.isArray(receipt.entities) ? receipt.entities.length : 0) : 0
  const relationCount = receipt ? (Array.isArray(receipt.relations) ? receipt.relations.length : 0) : 0

  useEffect(() => {
    let cancelled = false
    ontologyLifecycleApi.listReleases(ontologyId)
      .then(res => { if (!cancelled) { setReleases(Array.isArray(res.items) ? res.items : []); setLoadError('') } })
      .catch(() => { if (!cancelled) setLoadError('RELEASES_LOAD_FAILED') })
    return () => { cancelled = true }
  }, [ontologyId, retryTick])

  const runAction = async (action: () => Promise<unknown>, okMessage: string) => {
    setBusy(true)
    setActionError('')
    setFindings([])
    try {
      const result = (await action()) as { status?: unknown; runtime_disabled?: unknown }
      if (typeof result.status === 'string') onStatusChange?.(result.status)
      if (typeof result.runtime_disabled === 'boolean') setRuntimeDisabled(result.runtime_disabled)
      setActionError(okMessage)
      setDialogOpen(false)
      // lifecycle transitions change status everywhere — drop stale list/overview cache
      onMutated?.()
    } catch (err) {
      const fs = errorFindings(err)
      setFindings(fs)
      setActionError(errorMessage(err) || t('lifecycle.action_failed', '操作失败'))
    } finally {
      setBusy(false)
    }
  }

  const markCreated = () => runAction(
    () => ontologyLifecycleApi.markCreated(ontologyId),
    t('lifecycle.marked_created', '已创建'),
  )
  const archive = () => runAction(
    () => ontologyLifecycleApi.archive(ontologyId),
    t('lifecycle.archived', '已归档'),
  )
  const toggleRuntime = () => runAction(
    () => runtimeDisabled
      ? ontologyLifecycleApi.runtimeEnable(ontologyId)
      : ontologyLifecycleApi.runtimeDisable(ontologyId),
    runtimeDisabled ? t('lifecycle.runtime_enabled', '已启用') : t('lifecycle.runtime_disabled', '已停用'),
  )

  const publish = () => runAction(async () => {
    const result = await ontologyLifecycleApi.publish(ontologyId, { changelog: changelog || undefined })
    setReceipt(result)
    // refresh release list so the new version appears immediately
    try {
      const res = await ontologyLifecycleApi.listReleases(ontologyId)
      setReleases(Array.isArray(res.items) ? res.items : [])
    } catch { /* list refresh failure is non-fatal */ }
    return result
  }, t('lifecycle.published', '发布成功'))

  if (releases === null) {
    if (loadError === 'RELEASES_LOAD_FAILED') {
      return (
        <div className="bg-white rounded-xl border p-4 space-y-3" data-testid="ontology-publication-panel" role="alert">
          <p className="text-xs text-red-500">{t('lifecycle.releases_load_failed', '发布记录加载失败')}</p>
          <button type="button" onClick={() => setRetryTick(n => n + 1)}
            className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50">
            {t('common.retry', '重试')}
          </button>
        </div>
      )
    }
    return (
      <div className="bg-white rounded-xl border p-4 text-sm text-gray-400">{t('common.loading', '加载中…')}</div>
    )
  }

  return (
    <div className="bg-white rounded-xl border p-4 space-y-3" data-testid="ontology-publication-panel">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-sm font-semibold">{t('lifecycle.title', '发布生命周期')}</span>
        <span className="px-2 py-0.5 rounded text-xs border border-gray-200 text-gray-600">{displayStatus(status)}</span>
        {published && (
          <span className="px-2 py-0.5 rounded text-xs bg-green-50 border border-green-200 text-green-700">
            {t('lifecycle.published_badge', '已发布')} ({releases.length})
          </span>
        )}
        {published && isDirty && (
          <span className="px-2 py-0.5 rounded text-xs bg-amber-50 border border-amber-200 text-amber-700">
            {t('lifecycle.dirty_badge', '有未发布修改')}
          </span>
        )}
        {runtimeDisabled && (
          <span className="px-2 py-0.5 rounded text-xs bg-red-50 border border-red-200 text-red-700">
            {t('lifecycle.runtime_disabled_badge', '运行时已停用')}
          </span>
        )}
      </div>

      {loadError === 'RELEASES_LOAD_FAILED' && <p className="text-xs text-red-500">{t('lifecycle.releases_load_failed', '发布记录加载失败')}</p>}

      {releases.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {releases.slice(0, 5).map(r => (
            <span key={r.id} title={r.created_at ?? ''} className="text-xs px-2 py-0.5 rounded bg-gray-100 text-gray-600">
              {r.version}
            </span>
          ))}
        </div>
      )}

      {findings.length > 0 && (
        <div className="border border-red-200 bg-red-50 rounded-lg p-3 space-y-1">
          <p className="text-xs font-medium text-red-700">{t('lifecycle.publish_blocked', '发布被阻止')}</p>
          {findings.map((f, i) => (
            <p key={i} className="text-xs text-red-600 font-mono">
              {f.code ? `${f.code}${f.path ? ` @${f.path}` : ''}: ` : ''}{f.message ?? ''}
            </p>
          ))}
        </div>
      )}

      {actionError && !findings.length && <p className="text-xs text-gray-600">{actionError}</p>}

      {receipt && (
        <div className="border border-green-200 bg-green-50 rounded-lg p-3 text-xs text-green-800">
          <p className="font-medium">{t('lifecycle.publish_receipt', '发布回执')}</p>
          <p>
            {t('lifecycle.release_version', '版本')}: <span data-testid="receipt-version">{receipt.release?.version ?? ''}</span>
          </p>
          {(entityCount > 0 || relationCount > 0) && (
            <p>
              {t('lifecycle.impact', '影响')}:{' '}
              <span data-testid="receipt-impact">
                {t('lifecycle.impact_summary', '实体 {{e}} · 关系 {{r}}', { e: entityCount, r: relationCount })}
              </span>
            </p>
          )}
          {receipt.schema_hash && <p className="font-mono break-all">{receipt.schema_hash.slice(0, 32)}…</p>}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap pt-1 border-t">
        {status === 'draft' && (
          <button type="button" disabled={busy} onClick={markCreated}
            className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-50">
            {t('lifecycle.mark_created', '标记为已创建')}
          </button>
        )}
        {(status === 'draft' || status === 'created') && (
          <button type="button" disabled={busy} onClick={() => { setActionError(''); setFindings([]); setDialogOpen(true) }}
            className="px-3 py-1.5 text-xs rounded-lg bg-black text-white hover:bg-gray-800 disabled:opacity-50">
            {t('lifecycle.publish', '发布')}
          </button>
        )}
        {status === 'created' && (
          <button type="button" disabled={busy} onClick={archive}
            className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-50">
            {t('lifecycle.archive', '归档')}
          </button>
        )}
        {(status === 'draft' || status === 'created') && (
          <button type="button" disabled={busy} onClick={toggleRuntime}
            className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-50">
            {runtimeDisabled ? t('lifecycle.runtime_enable', '启用运行时') : t('lifecycle.runtime_disable', '停用运行时')}
          </button>
        )}
      </div>

      {dialogOpen && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => { if (!busy) setDialogOpen(false) }}>
          <div className="bg-white rounded-lg shadow-lg p-5 w-[420px] space-y-3" onClick={e => e.stopPropagation()}>
            <h3 className="text-sm font-semibold">{t('lifecycle.publish_title', '发布本体')}</h3>
            <textarea
              value={changelog}
              onChange={e => setChangelog(e.target.value)}
              placeholder={t('lifecycle.changelog_placeholder', '变更说明（可选）')}
              rows={3}
              className="w-full border rounded-lg px-3 py-2 text-sm"
            />
            <div className="flex justify-end gap-2">
              <button type="button" disabled={busy} onClick={() => setDialogOpen(false)}
                className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-50">
                {t('common.cancel', '取消')}
              </button>
              <button type="button" disabled={busy} onClick={publish}
                className="px-3 py-1.5 text-xs rounded-lg bg-black text-white hover:bg-gray-800 disabled:opacity-50">
                {t('lifecycle.confirm_publish', '确认发布')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
