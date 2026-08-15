import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  agentToolsApi,
  TOOL_CAPABILITY_GROUPS,
  type OntologyBinding,
  type PublishedOntology,
  type ToolDescriptor,
  type ToolValidationResult,
} from '@/api/agentTools'
import { agentDetailApi, type AgentVersion } from '@/api/agentDetail'
import OntologyBindingCard from './OntologyBindingCard'
import ExternalToolCard from './ExternalToolCard'
import CapabilityDrawer from './CapabilityDrawer'

interface Props {
  agentId: string
  activeVersion: AgentVersion | null
  canEdit: boolean
  onSaved: (result: { version_no: number }) => void
  onDirtyChange: (dirty: boolean) => void
}

const BASE_CAPABILITIES = ['read_schema', 'read_instances', 'traverse_relations']

export default function ToolConfigTab({ agentId, activeVersion, canEdit, onSaved, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [ontologies, setOntologies] = useState<PublishedOntology[]>([])
  const [bindings, setBindings] = useState<OntologyBinding[]>([])
  const [toolsByOntology, setToolsByOntology] = useState<Record<string, ToolDescriptor[]>>({})
  const [expanded, setExpanded] = useState<string | null>(null)
  const [validation, setValidation] = useState<ToolValidationResult | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [error, setError] = useState('')
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
      })))
      setError('')
    })
    onDirtyChange(false)
  }, [activeVersion, onDirtyChange])

  const loadTools = useCallback((ontologyId: string) => {
    setExpanded(ontologyId)
    setToolsByOntology(prev => prev[ontologyId] ? prev : { ...prev })
    agentToolsApi.listOntologyTools(ontologyId)
      .then(res => setToolsByOntology(prev => ({ ...prev, [ontologyId]: res.tools })))
      .catch(() => setError('AGENTS_TOOLS_LOAD_FAILED'))
  }, [])

  const toggleBinding = useCallback((ontology: PublishedOntology, bind: boolean) => {
    setBindings(prev => {
      if (!bind) return prev.filter(b => b.ontology_id !== ontology.id)
      if (prev.some(b => b.ontology_id === ontology.id)) return prev
      return [...prev, {
        ontology_id: ontology.id,
        capabilities: [...BASE_CAPABILITIES],
        allowlists: {},
        selected_tools: [`query:${ontology.id}`],
      }]
    })
  }, [])

  const toggleTool = useCallback((ontologyId: string, descriptorId: string, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const selected = new Set(b.selected_tools)
      if (on) selected.add(descriptorId)
      else selected.delete(descriptorId)
      // capabilities accumulate across every selected tool's capability
      const known = toolsByOntology[ontologyId] ?? []
      const extraCaps = [...selected]
        .map(id => known.find(d => d.descriptor_id === id)?.capability)
        .filter((c): c is string => Boolean(c))
      return { ...b, selected_tools: [...selected],
               capabilities: [...new Set([...BASE_CAPABILITIES, ...extraCaps])] }
    }))
  }, [toolsByOntology])

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
        <div className="space-y-2">
          {ontologies.map(o => (
            <div key={o.id} className="border rounded-lg">
              <OntologyBindingCard ontology={o} bound={bindings.some(b => b.ontology_id === o.id)}
                onToggle={toggleBinding} disabled={!canEdit}
                onExpand={bound => { if (bound) loadTools(o.id) }} />
              {bindings.some(b => b.ontology_id === o.id) && (
                <div className="px-4 pb-4" data-testid={`ontology-tools-${o.id}`}>
                  {expanded !== o.id && (
                    <button type="button" disabled={!canEdit} onClick={() => loadTools(o.id)}
                      className="text-xs text-gray-500 underline hover:text-black">
                      {t('agent.tools.load_tools', '加载工具列表')}
                    </button>
                  )}
                  {(toolsByOntology[o.id] ?? []).map(d => (
                    <label key={d.descriptor_id} className="flex items-start gap-2 py-1.5 text-sm">
                      <input type="checkbox" disabled={!canEdit}
                        checked={bindings.find(b => b.ontology_id === o.id)?.selected_tools.includes(d.descriptor_id) ?? false}
                        onChange={e => toggleTool(o.id, d.descriptor_id, e.target.checked)}
                        className="mt-1" />
                      <span>
                        <span className="font-medium">
                          {t(TOOL_CAPABILITY_GROUPS[d.source_kind]?.label ?? 'agent.tools.tool_other',
                             TOOL_CAPABILITY_GROUPS[d.source_kind]?.fallback ?? d.source_kind)}
                          {d.source_kind !== 'builtin' && ` · ${d.source_id.slice(0, 8)}`}
                        </span>
                        <span className="text-xs text-gray-400 ml-2 font-mono">{d.capability}</span>
                      </span>
                    </label>
                  ))}
                  {(toolsByOntology[o.id] ?? []).length === 0 && (
                    <p className="text-xs text-gray-400">{t('agent.tools.no_tools', '该本体暂无可用工具（需先发布）')}</p>
                  )}
                </div>
              )}
            </div>
          ))}
          {ontologies.length === 0 && !error && (
            <p className="text-sm text-gray-400">{t('agent.tools.no_ontologies', '没有可绑定的已发布本体')}</p>
          )}
        </div>
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
