import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  agentMemoriesApi,
  type ConflictListItem,
  type MemoryDetail,
  type MemoryRecord,
} from '@/api/agentMemories'

interface Props {
  open: boolean
  onClose: () => void
  agentId: string
}

function statusLabel(status: MemoryRecord['status'], t: (key: string, fallback: string) => string): string {
  if (status === 'pending_confirmation') return t('agent.memory.status_pending', '待确认')
  if (status === 'active') return t('agent.memory.status_active', '生效中')
  if (status === 'conflicted') return t('agent.memory.status_conflicted', '存在冲突')
  return t('agent.memory.status_deleted', '已删除')
}

function statusBadgeClass(status: MemoryRecord['status']): string {
  if (status === 'pending_confirmation') return 'bg-amber-100 text-amber-700'
  if (status === 'active') return 'bg-green-100 text-green-700'
  if (status === 'conflicted') return 'bg-red-100 text-red-700'
  return 'bg-gray-100 text-gray-500'
}

function embeddingLabel(status: MemoryDetail['embedding_status'], t: (key: string, fallback: string) => string): string {
  if (status === 'current') return t('agent.memory.embedding_current', 'Embedding: current')
  if (status === 'pending') return t('agent.memory.embedding_pending', 'Embedding: pending')
  return t('agent.memory.embedding_never', 'Embedding: not yet embedded')
}

function extractError(err: unknown, fallback: string): string {
  const detail = (err as { detail?: string })?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

export default function MemoryInspectionDrawer({ open, onClose, agentId }: Props) {
  const { t } = useTranslation()
  const [memories, setMemories] = useState<MemoryRecord[] | null>(null)
  const [conflicts, setConflicts] = useState<ConflictListItem[] | null>(null)
  const [error, setError] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<MemoryDetail | null>(null)
  const [consentChecked, setConsentChecked] = useState<Record<string, boolean>>({})
  const [editText, setEditText] = useState<Record<string, string>>({})
  const [busyId, setBusyId] = useState<string | null>(null)

  const load = useCallback(() => {
    void Promise.resolve().then(() => { setError('') })
    Promise.all([
      agentMemoriesApi.list(agentId),
      agentMemoriesApi.listConflicts(agentId),
    ])
      .then(([memRes, confRes]) => {
        setMemories(memRes.items)
        setConflicts(confRes.items)
      })
      .catch(err => {
        setError(extractError(err, t('agent.memory.load_failed', '加载记忆失败')))
      })
  }, [agentId, t])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  const toggleExpand = (memoryId: string) => {
    if (expandedId === memoryId) {
      setExpandedId(null)
      setDetail(null)
      return
    }
    setExpandedId(memoryId)
    setDetail(null)
    agentMemoriesApi.get(agentId, memoryId).then(setDetail).catch(() => setDetail(null))
  }

  const handleConfirm = async (memoryId: string) => {
    if (!consentChecked[memoryId]) return
    setBusyId(memoryId)
    try {
      await agentMemoriesApi.confirm(agentId, memoryId, true)
      await load()
    } catch (err) {
      setError(extractError(err, t('agent.memory.action_failed', '操作失败')))
    } finally {
      setBusyId(null)
    }
  }

  const handleReject = async (memoryId: string) => {
    setBusyId(memoryId)
    try {
      await agentMemoriesApi.reject(agentId, memoryId)
      await load()
    } catch (err) {
      setError(extractError(err, t('agent.memory.action_failed', '操作失败')))
    } finally {
      setBusyId(null)
    }
  }

  const handleSave = async (memoryId: string) => {
    const text = editText[memoryId]
    if (text === undefined) return
    setBusyId(memoryId)
    try {
      await agentMemoriesApi.correct(agentId, memoryId, text)
      await load()
    } catch (err) {
      setError(extractError(err, t('agent.memory.action_failed', '操作失败')))
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (memoryId: string) => {
    if (!window.confirm(t('agent.memory.delete_confirm', '确认删除该记忆？'))) return
    setBusyId(memoryId)
    try {
      await agentMemoriesApi.delete(agentId, memoryId)
      await load()
    } catch (err) {
      setError(extractError(err, t('agent.memory.action_failed', '操作失败')))
    } finally {
      setBusyId(null)
    }
  }

  const handleResolve = async (conflictId: string, winningMemoryId: string) => {
    setBusyId(conflictId)
    try {
      await agentMemoriesApi.resolveConflict(agentId, conflictId, winningMemoryId)
      await load()
    } catch (err) {
      setError(extractError(err, t('agent.memory.action_failed', '操作失败')))
    } finally {
      setBusyId(null)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 bg-black/30 z-40" onClick={onClose}>
      <div
        role="dialog"
        aria-label={t('agent.memory.inspection_title', '记忆检查')}
        className="absolute right-0 top-0 h-full w-96 bg-white shadow-xl p-4 overflow-auto"
        onClick={e => e.stopPropagation()}
        data-testid="memory-inspection-drawer"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm">{t('agent.memory.inspection_title', '记忆检查')}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-black">✕</button>
        </div>

        {error && <p className="text-sm text-red-500 mb-3" role="alert">{error}</p>}

        {memories === null ? (
          <p className="text-sm text-gray-400" data-testid="memory-loading">{t('common.loading', '加载中…')}</p>
        ) : (
          <ul className="space-y-2">
            {memories.filter(m => m.status !== 'conflicted').map(memory => (
              <li key={memory.id} className="border rounded-lg px-3 py-2 text-sm" data-testid={`memory-row-${memory.id}`}>
                <div className="flex items-center justify-between gap-2">
                  <button type="button" className="text-left flex-1" onClick={() => toggleExpand(memory.id)}>
                    {memory.display_text}
                  </button>
                  <span
                    className={`px-2 py-0.5 rounded text-xs ${statusBadgeClass(memory.status)}`}
                    data-testid={`memory-status-${memory.id}`}
                  >
                    {statusLabel(memory.status, t)}
                  </span>
                </div>
                <div className="text-xs text-gray-500 mt-1">
                  {t('agent.memory.confidence', '置信度')}: {memory.confidence} · {memory.updated_at}
                </div>

                {expandedId === memory.id && detail && (
                  <p className="text-xs text-gray-400 mt-1" data-testid={`memory-embedding-${memory.id}`}>
                    {embeddingLabel(detail.embedding_status, t)}
                  </p>
                )}

                {memory.status === 'pending_confirmation' && (
                  <div className="mt-2 space-y-2">
                    <label className="flex items-center gap-2 text-xs">
                      <input
                        type="checkbox"
                        checked={Boolean(consentChecked[memory.id])}
                        onChange={e => setConsentChecked(prev => ({ ...prev, [memory.id]: e.target.checked }))}
                        data-testid={`memory-consent-checkbox-${memory.id}`}
                      />
                      {t('agent.memory.consent_checkbox', '我同意将此记忆存储为已确认事实')}
                    </label>
                    <div className="flex gap-2">
                      <button type="button" disabled={busyId === memory.id}
                        onClick={() => handleConfirm(memory.id)}
                        className="px-3 py-1 text-xs bg-black text-white rounded disabled:opacity-40"
                        data-testid={`memory-confirm-${memory.id}`}>
                        {t('agent.memory.confirm', 'Confirm')}
                      </button>
                      <button type="button" disabled={busyId === memory.id}
                        onClick={() => handleReject(memory.id)}
                        className="px-3 py-1 text-xs border rounded disabled:opacity-40"
                        data-testid={`memory-reject-${memory.id}`}>
                        {t('agent.memory.reject', 'Reject')}
                      </button>
                    </div>
                  </div>
                )}

                {memory.status === 'active' && (
                  <div className="mt-2 space-y-2">
                    <input
                      type="text"
                      value={editText[memory.id] ?? memory.display_text}
                      onChange={e => setEditText(prev => ({ ...prev, [memory.id]: e.target.value }))}
                      className="w-full border rounded px-2 py-1 text-xs"
                      data-testid={`memory-edit-input-${memory.id}`}
                    />
                    <div className="flex gap-2">
                      <button type="button" disabled={busyId === memory.id}
                        onClick={() => handleSave(memory.id)}
                        className="px-3 py-1 text-xs bg-black text-white rounded disabled:opacity-40"
                        data-testid={`memory-save-${memory.id}`}>
                        {t('agent.memory.save', 'Save')}
                      </button>
                      <button type="button" disabled={busyId === memory.id}
                        onClick={() => handleDelete(memory.id)}
                        className="px-3 py-1 text-xs border rounded disabled:opacity-40"
                        data-testid={`memory-delete-${memory.id}`}>
                        {t('agent.memory.delete', 'Delete')}
                      </button>
                    </div>
                  </div>
                )}
              </li>
            ))}
            {memories.length === 0 && (
              <li className="text-sm text-gray-400">{t('agent.memory.no_memories', '暂无记忆')}</li>
            )}
          </ul>
        )}

        <h4 className="font-medium text-sm mt-6 mb-2">{t('agent.memory.conflicts_title', '冲突')}</h4>
        {conflicts !== null && (
          <ul className="space-y-2">
            {conflicts.map(conflict => (
              <li key={conflict.conflict_id} className="border border-red-200 rounded-lg px-3 py-2 text-sm"
                data-testid={`conflict-row-${conflict.conflict_id}`}>
                <div className="flex items-center justify-between gap-2 border-b pb-2 mb-2">
                  <span>{conflict.display_text_a}</span>
                  <button type="button" disabled={busyId === conflict.conflict_id}
                    onClick={() => handleResolve(conflict.conflict_id, conflict.memory_id_a)}
                    className="px-3 py-1 text-xs border rounded disabled:opacity-40"
                    data-testid={`conflict-keep-a-${conflict.conflict_id}`}>
                    {t('agent.memory.keep_this_one', 'Keep this one')}
                  </button>
                </div>
                <div className="flex items-center justify-between gap-2">
                  <span>{conflict.display_text_b}</span>
                  <button type="button" disabled={busyId === conflict.conflict_id}
                    onClick={() => handleResolve(conflict.conflict_id, conflict.memory_id_b)}
                    className="px-3 py-1 text-xs border rounded disabled:opacity-40"
                    data-testid={`conflict-keep-b-${conflict.conflict_id}`}>
                    {t('agent.memory.keep_this_one', 'Keep this one')}
                  </button>
                </div>
              </li>
            ))}
            {conflicts.length === 0 && (
              <li className="text-sm text-gray-400">{t('agent.memory.no_conflicts', '暂无冲突')}</li>
            )}
          </ul>
        )}
      </div>
    </div>
  )
}
