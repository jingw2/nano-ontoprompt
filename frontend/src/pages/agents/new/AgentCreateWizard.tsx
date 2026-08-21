import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { agentDetailApi, type CatalogModel } from '@/api/agentDetail'
import { agentExternalToolsApi, type ExternalToolCatalogItem } from '@/api/agentExternalTools'
import { agentToolsApi, type PublishedOntology } from '@/api/agentTools'
import { useOntologyToolSelection } from '@/pages/agents/shared/useOntologyToolSelection'
import OntologyToolSelector from '@/pages/agents/shared/OntologyToolSelector'
import ExternalToolCard, { type BoundExternalTool } from '@/pages/agents/detail/ExternalToolCard'

export default function AgentCreateWizard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [models, setModels] = useState<CatalogModel[]>([])
  const [ontologies, setOntologies] = useState<PublishedOntology[]>([])
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [modelId, setModelId] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [pendingExternalTools, setPendingExternalTools] = useState<BoundExternalTool[]>([])
  const [bindFailures, setBindFailures] = useState<string[]>([])
  const [createdAgentId, setCreatedAgentId] = useState<string | null>(null)

  const { bindings, toolsByOntology, bindOntology, unbindOntology, toggleCategory, toggleTool } =
    useOntologyToolSelection(ontologies)

  useEffect(() => {
    let cancelled = false
    agentDetailApi.catalogModels()
      .then(res => { if (!cancelled) setModels(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    agentToolsApi.listPublishedOntologies()
      .then(res => { if (!cancelled) setOntologies(Array.isArray(res.items) ? res.items : []) })
      .catch(() => undefined)
    return () => { cancelled = true }
  }, [])

  const bindPendingExternal = useCallback((item: ExternalToolCatalogItem, alias: string) => {
    setPendingExternalTools(prev => [...prev, {
      alias, tool_connection_version_id: item.tool_connection_version_id,
      provider_name: item.provider_name, provider_kind: item.provider_kind,
    }])
  }, [])

  const unbindPendingExternal = useCallback((alias: string) => {
    setPendingExternalTools(prev => prev.filter(p => p.alias !== alias))
  }, [])

  const submit = useCallback(async (event: React.FormEvent) => {
    event.preventDefault()
    const model = models.find(m => m.id === modelId)
    if (!name.trim() || !model) return
    setSaving(true)
    setError('')
    setBindFailures([])
    try {
      const result = await agentDetailApi.create({
        name: name.trim(),
        description: description.trim() || null,
        default_model_config_version_id: model.id,
        default_model_name: model.name,
        system_prompt: systemPrompt || null,
        memory_settings: {},
        ontology_bindings: bindings,
      })
      const failures: string[] = []
      for (const pick of pendingExternalTools) {
        try {
          await agentExternalToolsApi.bind(result.agent_id, result.version_id,
            { tool_connection_version_id: pick.tool_connection_version_id, alias: pick.alias })
        } catch {
          failures.push(pick.alias)
        }
      }
      if (failures.length > 0) {
        setCreatedAgentId(result.agent_id)
        setBindFailures(failures)
      } else {
        navigate(`/agents/${result.agent_id}`)
      }
    } catch {
      setError(t('agent.create.failed', '创建失败'))
    } finally {
      setSaving(false)
    }
  }, [name, description, modelId, models, systemPrompt, bindings, pendingExternalTools, navigate, t])

  return (
    <div className="max-w-2xl">
      <h2 className="text-xl font-semibold mb-4">{t('agent.create.title', '新建 Agent')}</h2>
      <form onSubmit={submit} className="bg-white border rounded-lg p-6 space-y-4" data-testid="agent-create-wizard">
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-name">{t('agent.create.name', '名称')}</label>
          <input id="agent-name" value={name} onChange={e => setName(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-description">{t('agent.create.description', '描述')}</label>
          <textarea id="agent-description" value={description} onChange={e => setDescription(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm" rows={3} />
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-model">{t('agent.create.model', '模型')}</label>
          <select id="agent-model" value={modelId} onChange={e => setModelId(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm">
            <option value="">{t('agent.create.select_model', '选择模型…')}</option>
            {models.map(m => (
              <option key={m.id} value={m.id}>{m.name} · v{m.version_no ?? '—'}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-sm text-gray-600 mb-1" htmlFor="agent-prompt">{t('agent.create.initial_prompt', '初始系统提示词')}</label>
          <textarea id="agent-prompt" value={systemPrompt} onChange={e => setSystemPrompt(e.target.value)}
            className="w-full border rounded-lg px-3 py-2 text-sm font-mono" rows={5}
            placeholder={t('agent.create.prompt_placeholder', '可选 — 可在详情页继续编辑')} />
        </div>
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.ontology_bindings', '本体绑定')}</h3>
          <OntologyToolSelector ontologies={ontologies} bindings={bindings} toolsByOntology={toolsByOntology}
            canEdit onBind={bindOntology} onUnbind={unbindOntology}
            onToggleCategory={toggleCategory} onToggleTool={toggleTool} />
        </div>
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.external', '外部工具')}</h3>
          <ExternalToolCard bindings={pendingExternalTools} canEdit
            onBind={bindPendingExternal} onUnbind={unbindPendingExternal} />
        </div>
        {error && <p className="text-sm text-red-500">{error}</p>}
        {bindFailures.length > 0 && createdAgentId && (
          <div className="border border-amber-300 bg-amber-50 rounded-lg p-3 text-sm text-amber-700">
            <p>{t('agent.create.partial_tool_bind_failure', 'Agent 已创建，但以下外部工具绑定失败：')} {bindFailures.join(', ')}</p>
            <button type="button" onClick={() => navigate(`/agents/${createdAgentId}`)}
              className="mt-2 px-3 py-1 text-xs border border-current rounded hover:opacity-80">
              {t('agent.create.go_to_detail', '前往详情页处理')}
            </button>
          </div>
        )}
        <div className="flex gap-3">
          <button type="submit" disabled={saving || !name.trim() || !modelId}
            className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40">
            {saving ? t('agent.create.saving', '创建中…') : t('agent.create.submit', '创建')}
          </button>
          <button type="button" onClick={() => navigate('/agents')}
            className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
            {t('agent.create.cancel', '取消')}
          </button>
        </div>
      </form>
    </div>
  )
}
