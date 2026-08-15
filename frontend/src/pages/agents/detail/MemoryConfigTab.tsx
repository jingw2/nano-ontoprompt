import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentDetailApi, type AgentVersion } from '@/api/agentDetail'

interface Props {
  agentId: string
  activeVersion: AgentVersion | null
  canEdit: boolean
  onSaved: (result: { version_no: number }) => void
  onDirtyChange: (dirty: boolean) => void
}

export default function MemoryConfigTab({ agentId, activeVersion, canEdit, onSaved, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [shortTermEnabled, setShortTermEnabled] = useState(false)
  const [longTermEnabled, setLongTermEnabled] = useState(false)
  const [budget, setBudget] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const initial = (activeVersion?.memory_settings ?? {}) as Record<string, unknown>

  useEffect(() => {
    void Promise.resolve().then(() => {
      setShortTermEnabled(initial.short_term_enabled === true)
      setLongTermEnabled(initial.long_term_enabled === true)
      setBudget(typeof initial.budget === 'number' ? String(initial.budget) : '')
      setError('')
    })
    onDirtyChange(false)
  }, [activeVersion, onDirtyChange]) // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = shortTermEnabled !== (initial.short_term_enabled === true)
    || longTermEnabled !== (initial.long_term_enabled === true)
    || budget !== (typeof initial.budget === 'number' ? String(initial.budget) : '')

  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])

  const save = useCallback(async () => {
    if (!activeVersion) return
    setSaving(true)
    setError('')
    try {
      const memory_settings: Record<string, unknown> = {
        short_term_enabled: shortTermEnabled,
        long_term_enabled: longTermEnabled,
      }
      if (budget !== '') memory_settings.budget = Number(budget)
      const result = await agentDetailApi.saveVersion(agentId, {
        base_version_no: activeVersion.version_no,
        name: activeVersion.name,
        description: activeVersion.description ?? null,
        default_model_config_version_id: activeVersion.default_model_config_version_id ?? '',
        default_model_name: activeVersion.default_model_name ?? '',
        system_prompt: activeVersion.system_prompt ?? null,
        memory_settings,
        application_state_schema_version_id: activeVersion.application_state_schema_version_id ?? null,
        change_note: t('agent.memory.change_note', 'Memory 设置更新'),
      })
      onSaved(result)
    } catch (err) {
      const code = (err as { error?: { code?: string } })?.error?.code
      if (code === 'AGENT_VERSION_CONFLICT') setError(t('agent.memory.conflict', '检测到新版本，请刷新后重试'))
      else setError(t('agent.memory.save_failed', '保存失败'))
    } finally {
      setSaving(false)
    }
  }, [agentId, activeVersion, shortTermEnabled, longTermEnabled, budget, onSaved, t])

  if (!activeVersion) {
    return <div className="p-6 text-gray-400">{t('common.loading', '加载中...')}</div>
  }

  return (
    <div className="p-6 space-y-4" data-testid="memory-config-tab">
      <h3 className="text-sm font-medium text-gray-700">{t('agent.memory.title', 'Memory 设置')}</h3>

      <div className="border rounded-lg p-3 space-y-1">
        <label className="flex items-center gap-3 text-sm">
          <input type="checkbox" checked={shortTermEnabled} disabled={!canEdit}
            onChange={e => setShortTermEnabled(e.target.checked)} />
          <span className="font-medium">{t('agent.memory.short_term', '短期记忆')}</span>
        </label>
        <p className="text-xs text-gray-500 pl-7">{t('agent.memory.short_term_desc', '保留当前会话内的上下文（最近对话轮次），用于保持对话连贯性。仅在本会话内有效。')}</p>
      </div>

      <div className="border rounded-lg p-3 space-y-1">
        <label className="flex items-center gap-3 text-sm">
          <input type="checkbox" checked={longTermEnabled} disabled={!canEdit}
            onChange={e => setLongTermEnabled(e.target.checked)} />
          <span className="font-medium">{t('agent.memory.long_term', '长期记忆')}</span>
        </label>
        <p className="text-xs text-gray-500 pl-7">{t('agent.memory.long_term_desc', '跨会话保留重要事实与结论，写入长期存储（向量索引），供后续会话检索复用。')}</p>
      </div>

      <div>
        <label className="block text-xs text-gray-500 mb-1" htmlFor="memory-budget">
          {t('agent.memory.budget', '记忆预算（条数）')}
        </label>
        <input id="memory-budget" type="number" min={0} value={budget} disabled={!canEdit}
          onChange={e => setBudget(e.target.value)}
          className="w-48 border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
        <p className="text-xs text-gray-500 mt-1">{t('agent.memory.budget_desc', '记忆条目数量上限：超出后按重要性自动裁剪最旧的条目，控制上下文预算（token）占用。')}</p>
      </div>

      <div className="border border-gray-200 rounded-lg p-3 text-xs text-gray-500 space-y-1">
        <p>{t('agent.memory.consent_note', '记忆写入需要 Agent 拥有写入权限；启用后模型可在推理时读取对应记忆。')}</p>
        <p>{t('agent.memory.retention_note', '短期记忆随会话保留；长期记忆按预算与保留策略自动归档，不会无限增长。')}</p>
      </div>

      <div className="border border-amber-200 bg-amber-50 rounded-lg p-3 text-sm text-amber-700" data-testid="memory-unavailable">
        <p>{t('agent.memory.inspection_unavailable', '记忆检查将在 Memory 功能激活后提供')}</p>
      </div>

      {error && <p className="text-sm text-red-500">{error}</p>}
      {canEdit && (
        <button type="button" disabled={saving || !dirty} onClick={save}
          className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40">
          {saving ? t('agent.memory.saving', '保存中…') : t('agent.memory.save', '保存')}
        </button>
      )}
    </div>
  )
}
