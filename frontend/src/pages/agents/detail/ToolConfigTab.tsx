import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  agentToolsApi,
  type PublishedOntology,
  type ToolValidationResult,
} from '@/api/agentTools'
import { agentDetailApi, type AgentVersion } from '@/api/agentDetail'
import ExternalToolCard from './ExternalToolCard'
import CapabilityDrawer from './CapabilityDrawer'
import { useOntologyToolSelection } from '@/pages/agents/shared/useOntologyToolSelection'
import OntologyToolSelector from '@/pages/agents/shared/OntologyToolSelector'

interface Props {
  agentId: string
  activeVersion: AgentVersion | null
  canEdit: boolean
  onSaved: (result: { version_no: number }) => void
  onDirtyChange: (dirty: boolean) => void
}

export default function ToolConfigTab({ agentId, activeVersion, canEdit, onSaved, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [ontologies, setOntologies] = useState<PublishedOntology[]>([])
  const {
    bindings, setBindings, toolsByOntology, error, setError,
    bindOntology, unbindOntology, toggleCategory, toggleTool,
  } = useOntologyToolSelection(ontologies)
  const [validation, setValidation] = useState<ToolValidationResult | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    agentToolsApi.listPublishedOntologies()
      .then(res => { if (!cancelled) setOntologies(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_TOOLS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  // seed the working binding set from the active version's persisted bindings
  useEffect(() => {
    void Promise.resolve().then(() => {
      const persisted = activeVersion?.ontology_bindings ?? []
      setBindings(persisted.map(b => ({
        ontology_id: b.ontology_id,
        capabilities: Array.isArray(b.capabilities) ? b.capabilities : [],
        allowlists: b.allowlists ?? {},
        selected_tools: Array.isArray(b.selected_tools) ? b.selected_tools : [],
        // legacy bindings keep no enabled_categories until the user edits them
        ...(b.enabled_categories !== undefined && b.enabled_categories !== null
          ? { enabled_categories: b.enabled_categories }
          : {}),
      })))
      setError('')
    })
    onDirtyChange(false)
  }, [activeVersion, onDirtyChange])

  // validate the proposed binding set whenever it changes
  useEffect(() => {
    if (!canEdit || bindings.length === 0) return
    let cancelled = false
    agentToolsApi.validateAgentTools(agentId, { ontology_ids: bindings.map(b => b.ontology_id) })
      .then(result => { if (!cancelled) setValidation(result) })
      .catch(() => { if (!cancelled) setError('AGENTS_TOOLS_VALIDATION_FAILED') })
    return () => { cancelled = true }
  }, [agentId, bindings, canEdit])

  const persistedBindings = (activeVersion?.ontology_bindings ?? []).map(b => ({
    ontology_id: b.ontology_id,
    capabilities: Array.isArray(b.capabilities) ? b.capabilities : [],
    allowlists: b.allowlists ?? {},
    selected_tools: Array.isArray(b.selected_tools) ? b.selected_tools : [],
    ...(b.enabled_categories !== undefined && b.enabled_categories !== null
      ? { enabled_categories: b.enabled_categories }
      : {}),
  }))
  const dirty = JSON.stringify(bindings) !== JSON.stringify(persistedBindings)
  useEffect(() => {
    onDirtyChange(dirty)
  }, [dirty, onDirtyChange])

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
        system_prompt: activeVersion.system_prompt ?? null,
        memory_settings: activeVersion.memory_settings ?? {},
        application_state_schema_version_id: activeVersion.application_state_schema_version_id ?? null,
        change_note: t('agent.tools.change_note', '工具选择更新'),
        ontology_bindings: bindings,
      })
      onSaved(result)
    } catch (err) {
      const code = (err as { error?: { code?: string } })?.error?.code
      if (code === 'AGENT_VERSION_CONFLICT') setError(t('agent.tools.conflict', '检测到新版本，请刷新后重试'))
      else setError(t('agent.tools.save_failed', '保存失败'))
    } finally {
      setSaving(false)
    }
  }, [agentId, activeVersion, bindings, onSaved, t])

  return (
    <div className="p-6 space-y-6" data-testid="tool-config-tab">
      <div>
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.ontology_bindings', '本体绑定')}</h3>
        {error && <p className="text-sm text-red-500 mb-2">{error}</p>}
        {/* OntologyToolSelector renders the published-ontology dropdown (data-testid="ontology-picker") and bound-ontology panels */}
        <OntologyToolSelector ontologies={ontologies} bindings={bindings} toolsByOntology={toolsByOntology}
          canEdit={canEdit} onBind={bindOntology} onUnbind={unbindOntology}
          onToggleCategory={toggleCategory} onToggleTool={toggleTool} />
      </div>

      {validation && (
        <div className={`border rounded-lg p-3 text-sm ${validation.valid ? 'bg-green-50 border-green-200 text-green-700' : 'bg-amber-50 border-amber-200 text-amber-700'}`}>
          <p>{validation.valid
            ? t('agent.tools.valid', '绑定配置有效')
            : t('agent.tools.invalid', '绑定配置存在冲突')}
          </p>
          {validation.blocked && validation.blocked.length > 0 && (
            <p className="text-xs mt-1">{validation.blocked.join(', ')}</p>
          )}
          <button type="button" onClick={() => setDrawerOpen(true)}
            className="mt-2 px-3 py-1 text-xs border border-current rounded hover:opacity-80">
            {t('agent.tools.view_capabilities', '查看能力交集')}
          </button>
        </div>
      )}

      {canEdit && (
        <button type="button" disabled={saving || !dirty} onClick={save}
          className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800 disabled:opacity-40">
          {saving ? t('agent.tools.saving', '保存中…') : t('agent.tools.save', '保存')}
        </button>
      )}

      <div>
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.external', '外部工具')}</h3>
        <ExternalToolCard />
      </div>

      <p className="text-xs text-gray-400">
        {t('agent.tools.next_turn_refresh', '工具选择将在下一次 Turn 生效并刷新')}
      </p>

      <CapabilityDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)}
        capabilities={validation?.capabilities ?? []} />
    </div>
  )
}
