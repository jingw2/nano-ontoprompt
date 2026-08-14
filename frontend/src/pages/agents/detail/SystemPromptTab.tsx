import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentDetailApi, type AgentVersion, type PromptGenerationRecord } from '@/api/agentDetail'
import { sanitizeText } from '@/security/sanitize'

interface Props {
  agentId: string
  activeVersion: AgentVersion | null
  canEdit: boolean
  onSaved: (result: { version_no: number }) => void
  onDirtyChange: (dirty: boolean) => void
}

export default function SystemPromptTab({ agentId, activeVersion, canEdit, onSaved, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(activeVersion?.system_prompt ?? '')
  const [generating, setGenerating] = useState(false)
  const [generation, setGeneration] = useState<PromptGenerationRecord | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void Promise.resolve().then(() => {
      setDraft(activeVersion?.system_prompt ?? '')
      setGeneration(null)
      setError('')
    })
    onDirtyChange(false)
  }, [activeVersion, onDirtyChange])

  const dirty = draft !== (activeVersion?.system_prompt ?? '')
  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])

  const generate = useCallback(async () => {
    if (!activeVersion) return
    setGenerating(true)
    setError('')
    try {
      const result = await agentDetailApi.generatePrompt(agentId, {
        base_version_no: activeVersion.version_no,
        model_config_version_id: activeVersion.default_model_config_version_id ?? '',
        model_name: activeVersion.default_model_name ?? '',
        input_text: sanitizeText(draft),
      })
      setGeneration(result)
    } catch {
      setError(t('agent.prompt.generate_failed', '生成失败'))
    } finally {
      setGenerating(false)
    }
  }, [agentId, activeVersion, draft, t])

  const applyGenerated = useCallback((mode: 'replace' | 'append') => {
    const output = generation?.output_text
    if (!output) return
    setDraft(prev => mode === 'replace' ? output : prev ? `${prev}\n\n${output}` : output)
    setGeneration(null)
  }, [generation])

  const save = useCallback(async () => {
    if (!activeVersion) return
    setSaving(true)
    setError('')
    try {
      const result = await agentDetailApi.saveVersion(agentId, {
        base_version_no: activeVersion.version_no,
        name: activeVersion.name,
        description: activeVersion.description ?? null,
        default_model_config_version_id: activeVersion.default_model_config_version_id ?? '',
        default_model_name: activeVersion.default_model_name ?? '',
        system_prompt: sanitizeText(draft) || null,
        memory_settings: activeVersion.memory_settings ?? {},
        application_state_schema_version_id: activeVersion.application_state_schema_version_id ?? null,
        change_note: t('agent.prompt.change_note', '系统提示词更新'),
        prompt_generation_id: generation?.status === 'accepted' ? generation.id : null,
      })
      onSaved(result)
    } catch (err) {
      const code = (err as { error?: { code?: string } })?.error?.code
      if (code === 'AGENT_VERSION_CONFLICT') setError(t('agent.prompt.conflict', '检测到新版本，请刷新后重试'))
      else setError(t('agent.prompt.save_failed', '保存失败'))
    } finally {
      setSaving(false)
    }
  }, [agentId, activeVersion, draft, generation, onSaved, t])

  if (!activeVersion) {
    return <div className="p-6 text-gray-400">{t('common.loading', '加载中...')}</div>
  }

  return (
    <div className="p-6 space-y-4" data-testid="system-prompt-tab">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium text-gray-700">{t('agent.prompt.title', '系统提示词')}</h3>
        {canEdit && (
          <button type="button" disabled={generating || !dirty} onClick={generate}
            className="px-3 py-1.5 text-xs border rounded-lg hover:bg-gray-50 disabled:opacity-40">
            {generating ? t('agent.prompt.generating', '生成中…') : t('agent.prompt.generate', 'Generate')}
          </button>
        )}
      </div>
      <textarea value={draft} disabled={!canEdit} onChange={e => setDraft(e.target.value)}
        className="w-full border rounded-lg px-3 py-2 text-sm font-mono disabled:bg-gray-50 disabled:text-gray-500" rows={12} />

      {generation && (
        <div className="border rounded-lg p-4 text-sm" data-testid="generation-provenance">
          <div className="flex items-center justify-between mb-2">
            <h4 className="font-medium">{t('agent.prompt.provenance', '生成来源')}</h4>
            <span className="text-xs text-gray-500">{generation.status}</span>
          </div>
          <p className="text-xs text-gray-500 mb-1">
            {t('agent.prompt.model', '模型')}: {generation.model_name ?? '—'} · {t('agent.prompt.base', '基准版本')}: v{generation.base_version_no ?? '—'}
          </p>
          <p className="text-xs text-gray-500 mb-2">
            input {generation.input_hash?.slice(0, 16) ?? '—'} → output {generation.output_hash?.slice(0, 16) ?? '—'}
          </p>
          {generation.output_text && (
            <pre className="bg-gray-50 border rounded p-3 text-xs whitespace-pre-wrap max-h-48 overflow-auto">{generation.output_text}</pre>
          )}
          {canEdit && (
            <div className="flex gap-3 mt-3">
              <button type="button" onClick={() => applyGenerated('replace')}
                className="px-3 py-1 text-xs border rounded-lg hover:bg-gray-50">
                {t('agent.prompt.replace', 'Replace')}
              </button>
              <button type="button" onClick={() => applyGenerated('append')}
                className="px-3 py-1 text-xs border rounded-lg hover:bg-gray-50">
                {t('agent.prompt.append', 'Append')}
              </button>
              <button type="button" onClick={() => setGeneration(null)}
                className="px-3 py-1 text-xs text-gray-500 hover:text-black">
                {t('agent.prompt.dismiss', '关闭')}
              </button>
            </div>
          )}
        </div>
      )}
      {error && <p className="text-sm text-red-500">{error}</p>}

      {canEdit && (
        <button type="button" disabled={saving || !dirty} onClick={save}
          className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40">
          {saving ? t('agent.prompt.saving', '保存中…') : t('agent.prompt.save', '保存')}
        </button>
      )}
    </div>
  )
}
