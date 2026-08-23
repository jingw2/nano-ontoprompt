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

// Must match backend/app/services/agent/memory_settings.py exactly.
const DEFAULTS = {
  short_term_enabled: true,
  long_term_enabled: false,
  message_pairs: 12,
  summary_threshold: 24,
  summary_token_budget: 1200,
  recall_token_budget: 800,
  recall_count: 8,
}

const RANGES = {
  message_pairs: [2, 20] as const,
  summary_threshold: [8, 40] as const,
  summary_token_budget: [256, 2048] as const,
  recall_token_budget: [128, 1200] as const,
  recall_count: [1, 12] as const,
}

function boolOrDefault(v: unknown, d: boolean): boolean {
  return typeof v === 'boolean' ? v : d
}

function numOrDefault(v: unknown, d: number): number {
  return typeof v === 'number' ? v : d
}

function clamp(value: number, [lo, hi]: readonly [number, number]): number {
  if (Number.isNaN(value)) return lo
  return Math.min(Math.max(value, lo), hi)
}

export default function MemoryConfigTab({ agentId, activeVersion, canEdit, onSaved, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [shortTermEnabled, setShortTermEnabled] = useState(DEFAULTS.short_term_enabled)
  const [longTermEnabled, setLongTermEnabled] = useState(DEFAULTS.long_term_enabled)
  const [messagePairs, setMessagePairs] = useState(String(DEFAULTS.message_pairs))
  const [summaryThreshold, setSummaryThreshold] = useState(String(DEFAULTS.summary_threshold))
  const [summaryTokenBudget, setSummaryTokenBudget] = useState(String(DEFAULTS.summary_token_budget))
  const [recallTokenBudget, setRecallTokenBudget] = useState(String(DEFAULTS.recall_token_budget))
  const [recallCount, setRecallCount] = useState(String(DEFAULTS.recall_count))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const initial = (activeVersion?.memory_settings ?? {}) as Record<string, unknown>

  const initShortTermEnabled = boolOrDefault(initial.short_term_enabled, DEFAULTS.short_term_enabled)
  const initLongTermEnabled = boolOrDefault(initial.long_term_enabled, DEFAULTS.long_term_enabled)
  const initMessagePairs = String(numOrDefault(initial.message_pairs, DEFAULTS.message_pairs))
  const initSummaryThreshold = String(numOrDefault(initial.summary_threshold, DEFAULTS.summary_threshold))
  const initSummaryTokenBudget = String(numOrDefault(initial.summary_token_budget, DEFAULTS.summary_token_budget))
  const initRecallTokenBudget = String(numOrDefault(initial.recall_token_budget, DEFAULTS.recall_token_budget))
  const initRecallCount = String(numOrDefault(initial.recall_count, DEFAULTS.recall_count))

  useEffect(() => {
    void Promise.resolve().then(() => {
      setShortTermEnabled(initShortTermEnabled)
      setLongTermEnabled(initLongTermEnabled)
      setMessagePairs(initMessagePairs)
      setSummaryThreshold(initSummaryThreshold)
      setSummaryTokenBudget(initSummaryTokenBudget)
      setRecallTokenBudget(initRecallTokenBudget)
      setRecallCount(initRecallCount)
      setError('')
    })
    onDirtyChange(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeVersion, onDirtyChange])

  const dirty = shortTermEnabled !== initShortTermEnabled
    || longTermEnabled !== initLongTermEnabled
    || messagePairs !== initMessagePairs
    || summaryThreshold !== initSummaryThreshold
    || summaryTokenBudget !== initSummaryTokenBudget
    || recallTokenBudget !== initRecallTokenBudget
    || recallCount !== initRecallCount

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
        message_pairs: clamp(Number(messagePairs), RANGES.message_pairs),
        summary_threshold: clamp(Number(summaryThreshold), RANGES.summary_threshold),
        summary_token_budget: clamp(Number(summaryTokenBudget), RANGES.summary_token_budget),
        recall_token_budget: clamp(Number(recallTokenBudget), RANGES.recall_token_budget),
        recall_count: clamp(Number(recallCount), RANGES.recall_count),
      }
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
  }, [agentId, activeVersion, shortTermEnabled, longTermEnabled, messagePairs, summaryThreshold,
    summaryTokenBudget, recallTokenBudget, recallCount, onSaved, t])

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

      <div className="border rounded-lg p-3 space-y-3">
        <p className="text-xs font-medium text-gray-600">{t('agent.memory.short_term_group', '短期记忆参数')}</p>
        <div>
          <label className="block text-xs text-gray-500 mb-1" htmlFor="memory-message-pairs">
            {t('agent.memory.message_pairs', '保留对话轮次')}
          </label>
          <input id="memory-message-pairs" type="number" min={RANGES.message_pairs[0]} max={RANGES.message_pairs[1]}
            value={messagePairs} disabled={!canEdit}
            onChange={e => setMessagePairs(e.target.value)}
            className="w-48 border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
          <p className="text-xs text-gray-500 mt-1">{t('agent.memory.message_pairs_desc', '短期记忆中保留的最近对话轮次数量（2-20）。')}</p>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1" htmlFor="memory-summary-threshold">
            {t('agent.memory.summary_threshold', '摘要触发阈值')}
          </label>
          <input id="memory-summary-threshold" type="number" min={RANGES.summary_threshold[0]} max={RANGES.summary_threshold[1]}
            value={summaryThreshold} disabled={!canEdit}
            onChange={e => setSummaryThreshold(e.target.value)}
            className="w-48 border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
          <p className="text-xs text-gray-500 mt-1">{t('agent.memory.summary_threshold_desc', '对话轮次超过该阈值时自动生成摘要（8-40）。')}</p>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1" htmlFor="memory-summary-token-budget">
            {t('agent.memory.summary_token_budget', '摘要 Token 预算')}
          </label>
          <input id="memory-summary-token-budget" type="number" min={RANGES.summary_token_budget[0]} max={RANGES.summary_token_budget[1]}
            value={summaryTokenBudget} disabled={!canEdit}
            onChange={e => setSummaryTokenBudget(e.target.value)}
            className="w-48 border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
          <p className="text-xs text-gray-500 mt-1">{t('agent.memory.summary_token_budget_desc', '生成摘要时可使用的最大 Token 数（256-2048）。')}</p>
        </div>
      </div>

      <div className="border rounded-lg p-3 space-y-1">
        <label className="flex items-center gap-3 text-sm">
          <input type="checkbox" checked={longTermEnabled} disabled={!canEdit}
            onChange={e => setLongTermEnabled(e.target.checked)} />
          <span className="font-medium">{t('agent.memory.long_term', '长期记忆')}</span>
        </label>
        <p className="text-xs text-gray-500 pl-7">{t('agent.memory.long_term_desc', '跨会话保留重要事实与结论，写入长期存储（向量索引），供后续会话检索复用。')}</p>
      </div>

      <div className="border rounded-lg p-3 space-y-3 bg-gray-50 opacity-70">
        <div className="flex items-center justify-between">
          <p className="text-xs font-medium text-gray-600">{t('agent.memory.long_term_group', '长期记忆参数')}</p>
          <span className="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-600">{t('agent.memory.available_later', '暂未生效')}</span>
        </div>
        <p className="text-xs text-gray-500">{t('agent.memory.long_term_inert_note', '以下参数已保存，但要等长期记忆功能在后续版本上线后才会生效。')}</p>
        <div>
          <label className="block text-xs text-gray-500 mb-1" htmlFor="memory-recall-token-budget">
            {t('agent.memory.recall_token_budget', '召回 Token 预算')}
          </label>
          <input id="memory-recall-token-budget" type="number" min={RANGES.recall_token_budget[0]} max={RANGES.recall_token_budget[1]}
            value={recallTokenBudget} disabled={!canEdit}
            onChange={e => setRecallTokenBudget(e.target.value)}
            className="w-48 border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
          <p className="text-xs text-gray-500 mt-1">{t('agent.memory.recall_token_budget_desc', '长期记忆召回时可使用的最大 Token 数（128-1200）。')}</p>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1" htmlFor="memory-recall-count">
            {t('agent.memory.recall_count', '召回条数')}
          </label>
          <input id="memory-recall-count" type="number" min={RANGES.recall_count[0]} max={RANGES.recall_count[1]}
            value={recallCount} disabled={!canEdit}
            onChange={e => setRecallCount(e.target.value)}
            className="w-48 border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
          <p className="text-xs text-gray-500 mt-1">{t('agent.memory.recall_count_desc', '长期记忆单次召回的最大条目数（1-12）。')}</p>
        </div>
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
