import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  agentToolsApi,
  TOOL_CAPABILITY_GROUPS,
  TOOL_CATEGORIES,
  type OntologyBinding,
  type PublishedOntology,
  type ToolCategory,
  type ToolDescriptor,
  type ToolValidationResult,
} from '@/api/agentTools'
import { agentDetailApi, type AgentVersion } from '@/api/agentDetail'
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

const CATEGORY_LABELS: Record<ToolCategory, { label: string; fallback: string }> = {
  mcp: { label: 'agent.tools.category_mcp', fallback: 'MCP 外部工具' },
  query: { label: 'agent.tools.category_query', fallback: '查询' },
  write: { label: 'agent.tools.category_write', fallback: '写入' },
  logic: { label: 'agent.tools.category_logic', fallback: 'Logic 规则' },
  action: { label: 'agent.tools.category_action', fallback: '实例 Action' },
}

function categoryOf(d: ToolDescriptor): ToolCategory {
  if (d.category) return d.category
  if (d.source_kind === 'builtin') return 'query'
  if (d.source_kind === 'logic') return 'logic'
  if (d.source_kind === 'action') return 'action'
  return 'mcp'
}

/** Categories in effect for a binding: explicit list when stored, otherwise
 * ALL (the legacy default keeps every category enabled). */
function effectiveCategories(b: OntologyBinding): ToolCategory[] {
  return b.enabled_categories ?? TOOL_CATEGORIES
}

/** Capabilities accumulate across every selected tool's capability. */
function deriveCapabilities(tools: ToolDescriptor[], selected: Set<string>): string[] {
  const extra = [...selected]
    .map(id => tools.find(d => d.descriptor_id === id)?.capability)
    .filter((c): c is string => Boolean(c))
  return [...new Set([...BASE_CAPABILITIES, ...extra])]
}

export default function ToolConfigTab({ agentId, activeVersion, canEdit, onSaved, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [ontologies, setOntologies] = useState<PublishedOntology[]>([])
  const [bindings, setBindings] = useState<OntologyBinding[]>([])
  const [toolsByOntology, setToolsByOntology] = useState<Record<string, ToolDescriptor[]>>({})
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
        // legacy bindings keep no enabled_categories until the user edits them
        ...(b.enabled_categories !== undefined && b.enabled_categories !== null
          ? { enabled_categories: b.enabled_categories }
          : {}),
      })))
      setError('')
    })
    onDirtyChange(false)
  }, [activeVersion, onDirtyChange])

  const loadTools = useCallback((ontologyId: string) => {
    agentToolsApi.listOntologyTools(ontologyId)
      .then(res => {
        const tools = Array.isArray(res.tools) ? res.tools : []
        setToolsByOntology(prev => ({ ...prev, [ontologyId]: tools }))
        // category-mode bindings keep their explicit selection in sync with
        // the enabled categories (legacy bindings keep the persisted tools)
        setBindings(prev => prev.map(b => {
          if (b.ontology_id !== ontologyId || b.enabled_categories === undefined || b.enabled_categories === null) return b
          const selected = new Set(
            tools.filter(d => effectiveCategories(b).includes(categoryOf(d))).map(d => d.descriptor_id),
          )
          return { ...b, selected_tools: [...selected], capabilities: deriveCapabilities(tools, selected) }
        }))
      })
      .catch(() => setError('AGENTS_TOOLS_LOAD_FAILED'))
  }, [])

  // auto-load the tool list for every bound ontology so category state is visible
  useEffect(() => {
    bindings.forEach(b => {
      if (b.ontology_id && !toolsByOntology[b.ontology_id]) loadTools(b.ontology_id)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings.map(b => b.ontology_id).join(',')])

  const bindOntology = useCallback((ontologyId: string) => {
    const ontology = ontologies.find(o => o.id === ontologyId)
    if (!ontology || bindings.some(b => b.ontology_id === ontologyId)) return
    setBindings(prev => [...prev, {
      ontology_id: ontology.id,
      capabilities: [...BASE_CAPABILITIES],
      allowlists: {},
      selected_tools: [],
      // default: ALL tool categories selected
      enabled_categories: [...TOOL_CATEGORIES],
    }])
    loadTools(ontology.id)
  }, [ontologies, bindings, loadTools])

  const unbindOntology = useCallback((ontologyId: string) => {
    setBindings(prev => prev.filter(b => b.ontology_id !== ontologyId))
  }, [])

  const toggleCategory = useCallback((ontologyId: string, category: ToolCategory, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const cats = new Set(effectiveCategories(b))
      if (on) cats.add(category)
      else cats.delete(category)
      const tools = toolsByOntology[ontologyId] ?? []
      const selected = new Set(b.selected_tools)
      for (const d of tools) {
        if (categoryOf(d) === category) {
          if (on) selected.add(d.descriptor_id)
          else selected.delete(d.descriptor_id)
        }
      }
      return { ...b, enabled_categories: [...cats],
               selected_tools: [...selected], capabilities: deriveCapabilities(tools, selected) }
    }))
  }, [toolsByOntology])

  const toggleTool = useCallback((ontologyId: string, descriptorId: string, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const selected = new Set(b.selected_tools)
      if (on) selected.add(descriptorId)
      else selected.delete(descriptorId)
      const known = toolsByOntology[ontologyId] ?? []
      return { ...b, selected_tools: [...selected],
               capabilities: deriveCapabilities(known, selected) }
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

  const boundIds = new Set(bindings.map(b => b.ontology_id))
  const pickable = ontologies.filter(o => !boundIds.has(o.id))

  return (
    <div className="p-6 space-y-6" data-testid="tool-config-tab">
      <div>
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.ontology_bindings', '本体绑定')}</h3>
        {error && <p className="text-sm text-red-500 mb-2">{error}</p>}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <select
              data-testid="ontology-picker"
              disabled={!canEdit || pickable.length === 0}
              value=""
              onChange={e => { if (e.target.value) bindOntology(e.target.value) }}
              className="border rounded-lg px-3 py-2 text-sm bg-white"
            >
              <option value="">
                {t('agent.tools.select_ontology', '选择要绑定的已发布本体…')}
              </option>
              {pickable.map(o => (
                <option key={o.id} value={o.id}>{o.name} ({o.id})</option>
              ))}
            </select>
            <span className="text-xs text-gray-400">{t('agent.tools.picker_note', '下拉选择已发布本体（编码 + 名称），绑定后默认启用全部工具类别')}</span>
          </div>
          {ontologies.length === 0 && !error && (
            <p className="text-sm text-gray-400">{t('agent.tools.no_ontologies', '没有可绑定的已发布本体')}</p>
          )}
        </div>
      </div>

      <div className="space-y-4" data-testid="bound-ontology-panels">
        {bindings.map(binding => {
          const ontology = ontologies.find(o => o.id === binding.ontology_id)
          const cats = effectiveCategories(binding)
          const tools = toolsByOntology[binding.ontology_id] ?? []
          return (
            <div key={binding.ontology_id} className="border rounded-lg p-4" data-testid={`ontology-tools-${binding.ontology_id}`}>
              <div className="flex items-center justify-between mb-2">
                <div>
                  <p className="text-sm font-medium">{ontology?.name ?? binding.ontology_id}</p>
                  <p className="text-xs text-gray-500 font-mono mt-0.5">{binding.ontology_id}</p>
                </div>
                <button type="button" disabled={!canEdit} onClick={() => unbindOntology(binding.ontology_id)}
                  className="px-3 py-1.5 text-xs rounded-lg border hover:bg-gray-50 disabled:opacity-40">
                  {t('agent.tools.unbind', '解绑')}
                </button>
              </div>
              <p className="text-xs text-gray-500 mb-1 mt-2">{t('agent.tools.categories', '工具类别')}</p>
              <div className="flex flex-wrap gap-3 mb-2" data-testid={`category-toggles-${binding.ontology_id}`}>
                {TOOL_CATEGORIES.map(cat => (
                  <label key={cat} className="flex items-center gap-1.5 text-xs">
                    <input type="checkbox" data-testid={`category-${binding.ontology_id}-${cat}`} disabled={!canEdit}
                      checked={cats.includes(cat)}
                      onChange={e => toggleCategory(binding.ontology_id, cat, e.target.checked)}
                      className="mt-0.5" />
                    {t(CATEGORY_LABELS[cat].label, CATEGORY_LABELS[cat].fallback)}
                  </label>
                ))}
              </div>
              <p className="text-xs text-gray-400 mb-2">{t('agent.tools.category_tools_note', '勾选的类别默认全部启用')}</p>
              {tools.map(d => {
                const dCat = categoryOf(d)
                const catOn = cats.includes(dCat)
                return (
                  <label key={d.descriptor_id} className="flex items-start gap-2 py-1.5 text-sm">
                    <input type="checkbox" disabled={!canEdit || !catOn}
                      checked={catOn && binding.selected_tools.includes(d.descriptor_id)}
                      onChange={e => toggleTool(binding.ontology_id, d.descriptor_id, e.target.checked)}
                      className="mt-1" />
                    <span>
                      <span className="font-medium">
                        {t(TOOL_CAPABILITY_GROUPS[dCat]?.label ?? 'agent.tools.tool_other',
                           TOOL_CAPABILITY_GROUPS[dCat]?.fallback ?? d.source_kind)}
                        {d.source_kind !== 'builtin' && ` · ${d.source_id.slice(0, 8)}`}
                      </span>
                      <span className="text-xs text-gray-400 ml-2 font-mono">{d.capability}</span>
                    </span>
                  </label>
                )
              })}
              {tools.length === 0 && (
                <p className="text-xs text-gray-400">{t('agent.tools.no_tools', '该本体暂无可用工具（需先发布）')}</p>
              )}
            </div>
          )
        })}
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
