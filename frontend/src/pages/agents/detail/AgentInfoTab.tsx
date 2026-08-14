import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentDetailApi, type AgentVersion } from '@/api/agentDetail'

interface Props {
  agentId: string
  activeVersion: AgentVersion | null
  versions: AgentVersion[]
  canEdit: boolean
  onSaved: (result: { version_no: number }) => void
  onReload: () => void
  onDirtyChange: (dirty: boolean) => void
}

export default function AgentInfoTab({ agentId, activeVersion, versions, canEdit, onSaved, onReload, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [name, setName] = useState(activeVersion?.name ?? '')
  const [description, setDescription] = useState(activeVersion?.description ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [conflict, setConflict] = useState('')

  useEffect(() => {
    void Promise.resolve().then(() => {
      setName(activeVersion?.name ?? '')
      setDescription(activeVersion?.description ?? '')
      setError('')
      setConflict('')
    })
    onDirtyChange(false)
  }, [activeVersion, onDirtyChange])

  const dirty = name !== (activeVersion?.name ?? '') || description !== (activeVersion?.description ?? '')
  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])

  const save = useCallback(async () => {
    if (!activeVersion) return
    setSaving(true)
    setError('')
    setConflict('')
    try {
      const result = await agentDetailApi.saveVersion(agentId, {
        base_version_no: activeVersion.version_no,
        name: name.trim() || activeVersion.name,
        description: description.trim() || null,
        default_model_config_version_id: activeVersion.default_model_config_version_id ?? '',
        default_model_name: activeVersion.default_model_name ?? '',
        system_prompt: activeVersion.system_prompt ?? null,
        memory_settings: activeVersion.memory_settings ?? {},
        application_state_schema_version_id: activeVersion.application_state_schema_version_id ?? null,
        change_note: t('agent.info.change_note_basic', 'Basic 信息更新'),
      })
      onSaved(result)
    } catch (err) {
      const code = (err as { error?: { code?: string } })?.error?.code
      if (code === 'AGENT_VERSION_CONFLICT') setConflict(t('agent.info.conflict', '检测到新版本，请重新加载'))
      else setError(t('agent.info.save_failed', '保存失败'))
    } finally {
      setSaving(false)
    }
  }, [agentId, activeVersion, name, description, onSaved, t])

  if (!activeVersion) {
    return <div className="p-6 text-gray-400">{t('common.loading', '加载中...')}</div>
  }

  return (
    <div className="p-6 space-y-4" data-testid="agent-info-tab">
      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('agent.info.id', 'ID')}</label>
        <p className="text-sm font-mono text-gray-600">{agentId}</p>
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1" htmlFor="info-name">{t('agent.info.name', '名称')}</label>
        <input id="info-name" value={name} disabled={!canEdit} onChange={e => setName(e.target.value)}
          className="w-full max-w-md border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1" htmlFor="info-desc">{t('agent.info.description', '描述')}</label>
        <textarea id="info-desc" value={description} disabled={!canEdit} onChange={e => setDescription(e.target.value)}
          className="w-full max-w-md border rounded-lg px-3 py-2 text-sm disabled:bg-gray-50 disabled:text-gray-500" rows={3} />
      </div>
      <div>
        <label className="block text-xs text-gray-500 mb-1">{t('agent.info.model', '模型')}</label>
        <p className="text-sm">{activeVersion.default_model_name ?? '—'}</p>
      </div>

      {conflict && (
        <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-700" role="alert">
          <p>{conflict}</p>
          <div className="flex gap-3 mt-2">
            <button type="button" onClick={onReload} className="px-3 py-1 text-xs border border-amber-300 rounded hover:bg-amber-100">
              {t('agent.info.reload', '重新加载')}
            </button>
            <button type="button" onClick={() => setConflict('')} className="px-3 py-1 text-xs text-amber-600 hover:underline">
              {t('agent.info.copy_draft', '保留草稿')}
            </button>
          </div>
        </div>
      )}
      {error && <p className="text-sm text-red-500">{error}</p>}

      {canEdit && (
        <button type="button" disabled={saving || !dirty} onClick={save}
          className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40">
          {saving ? t('agent.info.saving', '保存中…') : t('agent.info.save', '保存')}
        </button>
      )}

      <div>
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.info.version_history', '版本历史')}</h3>
        <div className="border rounded-lg overflow-hidden">
          <table className="w-full text-sm" data-testid="version-history">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.info.version', '版本')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.info.version_name', '名称')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.info.changed', '修改时间')}</th>
              </tr>
            </thead>
            <tbody>
              {versions.map(v => (
                <tr key={v.id} className="border-t">
                  <td className="px-4 py-2">v{v.version_no}</td>
                  <td className="px-4 py-2">{v.name}</td>
                  <td className="px-4 py-2 text-gray-500">{v.created_at ? new Date(v.created_at).toLocaleString() : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
