import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight } from 'lucide-react'
import {
  TOOL_CATEGORIES, TOOL_CAPABILITY_GROUPS,
  type OntologyBinding, type PublishedOntology, type ToolCategory, type ToolDescriptor,
} from '@/api/agentTools'
import { categoryOf, effectiveCategories, DEFAULT_ENTITY_SEARCH_DEPTH } from './useOntologyToolSelection'

const CATEGORY_LABELS: Record<ToolCategory, { label: string; fallback: string }> = {
  mcp: { label: 'agent.tools.category_mcp', fallback: 'MCP 外部工具' },
  query: { label: 'agent.tools.category_query', fallback: '查询' },
  write: { label: 'agent.tools.category_write', fallback: '写入' },
  logic: { label: 'agent.tools.category_logic', fallback: 'Logic 规则' },
  action: { label: 'agent.tools.category_action', fallback: '实例 Action' },
}

interface Props {
  ontologies: PublishedOntology[]
  bindings: OntologyBinding[]
  toolsByOntology: Record<string, ToolDescriptor[]>
  canEdit: boolean
  onBind: (ontologyId: string) => void
  onUnbind: (ontologyId: string) => void
  onToggleCategory: (ontologyId: string, category: ToolCategory, on: boolean) => void
  onToggleTool: (ontologyId: string, descriptorId: string, on: boolean) => void
  onSetToolCatalogLimit: (ontologyId: string, limit: number | null) => void
  onSetEntitySearchDepth: (ontologyId: string, depth: number | null) => void
  /** When the caller has its own ontology-fetch error, suppress the "no ontologies" empty
   * state so the two messages don't render at once (caller still renders its own error text). */
  error?: string
}

export default function OntologyToolSelector({
  ontologies, bindings, toolsByOntology, canEdit, onBind, onUnbind, onToggleCategory, onToggleTool,
  onSetToolCatalogLimit, onSetEntitySearchDepth, error,
}: Props) {
  const { t } = useTranslation()
  // one Agent binds at most one Ontology: once bound, the picker is disabled —
  // unbind first to switch to a different published ontology
  const pickable = bindings.length > 0 ? [] : ontologies
  // per-ontology, per-category expand/collapse; collapsed by default so an
  // ontology with many Logic rules/Actions doesn't render a wall of checkboxes
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const toggleExpanded = (key: string) => setExpanded(prev => ({ ...prev, [key]: !prev[key] }))

  return (
    <div>
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <select
            data-testid="ontology-picker"
            disabled={!canEdit || bindings.length > 0 || pickable.length === 0}
            value=""
            onChange={e => { if (e.target.value) onBind(e.target.value) }}
            className="border rounded-lg px-3 py-2 text-sm bg-white"
          >
            <option value="">{t('agent.tools.select_ontology', '选择要绑定的已发布本体…')}</option>
            {pickable.map(o => (
              <option key={o.id} value={o.id}>{o.name} ({o.id})</option>
            ))}
          </select>
          <span className="text-xs text-gray-400">{t('agent.tools.picker_note', '每个 Agent 仅能绑定一个已发布本体；绑定后默认启用全部工具类别，如需更换请先解绑')}</span>
        </div>
        {ontologies.length === 0 && !error && (
          <p className="text-sm text-gray-400">{t('agent.tools.no_ontologies', '没有可绑定的已发布本体')}</p>
        )}
      </div>

      <div className="space-y-4 mt-4" data-testid="bound-ontology-panels">
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
                <button type="button" disabled={!canEdit} onClick={() => onUnbind(binding.ontology_id)}
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
                      onChange={e => onToggleCategory(binding.ontology_id, cat, e.target.checked)}
                      className="mt-0.5" />
                    {t(CATEGORY_LABELS[cat].label, CATEGORY_LABELS[cat].fallback)}
                  </label>
                ))}
              </div>
              <p className="text-xs text-gray-400 mb-2">{t('agent.tools.category_tools_note', '勾选的类别默认全部启用')}</p>
              <label className="flex items-center gap-2 text-xs mb-3">
                {t('agent.tools.catalog_limit', '工具目录上限（可选）')}
                <input type="number" min={1} max={500}
                  data-testid={`tool-catalog-limit-${binding.ontology_id}`}
                  disabled={!canEdit}
                  value={binding.tool_catalog_limit ?? ''}
                  onChange={e => {
                    const raw = e.target.value
                    onSetToolCatalogLimit(binding.ontology_id, raw === '' ? null : Number(raw))
                  }}
                  placeholder={t('agent.tools.catalog_limit_placeholder', '不限制')}
                  className="border rounded px-2 py-1 w-24" />
                <span className="text-gray-400">
                  {t('agent.tools.catalog_limit_note', '本体逻辑规则/动作过多时，按此数量截断，超出显式勾选的工具不受影响')}
                </span>
              </label>
              <label className="flex items-center gap-2 text-xs mb-3">
                {t('agent.tools.entity_search_depth', '实体搜索深度')}
                <input type="number" min={1} max={50}
                  data-testid={`entity-search-depth-${binding.ontology_id}`}
                  disabled={!canEdit}
                  value={binding.entity_search_depth ?? ''}
                  onChange={e => {
                    const raw = e.target.value
                    onSetEntitySearchDepth(binding.ontology_id, raw === '' ? null : Number(raw))
                  }}
                  placeholder={String(DEFAULT_ENTITY_SEARCH_DEPTH)}
                  className="border rounded px-2 py-1 w-24" />
                <span className="text-gray-400">
                  {t('agent.tools.entity_search_depth_note', '实体关系遍历工具单次调用最多可跳转的关系跳数，默认 10。数值越大搜索范围越广，但耗时更长、消耗上下文更多')}
                </span>
              </label>
              {TOOL_CATEGORIES.map(cat => {
                const catTools = tools.filter(d => categoryOf(d) === cat)
                if (catTools.length === 0) return null
                const catOn = cats.includes(cat)
                const key = `${binding.ontology_id}:${cat}`
                const isOpen = !!expanded[key]
                return (
                  <div key={cat} className="border-t first:border-t-0 py-1.5">
                    <button type="button" onClick={() => toggleExpanded(key)}
                      data-testid={`category-expand-${binding.ontology_id}-${cat}`}
                      className="flex items-center gap-1.5 text-xs text-gray-500 hover:text-gray-800 w-full text-left">
                      {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      {t(CATEGORY_LABELS[cat].label, CATEGORY_LABELS[cat].fallback)}
                      <span className="text-gray-400">({catTools.length})</span>
                    </button>
                    {isOpen && catTools.map(d => (
                      <label key={d.descriptor_id} className="flex items-start gap-2 py-1.5 pl-5 text-sm">
                        <input type="checkbox" disabled={!canEdit || !catOn}
                          checked={catOn && binding.selected_tools.includes(d.descriptor_id)}
                          onChange={e => onToggleTool(binding.ontology_id, d.descriptor_id, e.target.checked)}
                          className="mt-1" />
                        <span>
                          <span className="font-medium">
                            {d.source_kind === 'logic' || d.source_kind === 'action'
                              ? (d.name || `${d.source_kind}:${d.source_id.slice(0, 8)}`)
                              : t(TOOL_CAPABILITY_GROUPS[cat]?.label ?? 'agent.tools.tool_other',
                                 TOOL_CAPABILITY_GROUPS[cat]?.fallback ?? d.source_kind)}
                            {d.source_kind === 'mcp' && ` · ${d.source_id.slice(0, 8)}`}
                          </span>
                          <span className="text-xs text-gray-400 ml-2 font-mono">{d.capability}</span>
                        </span>
                      </label>
                    ))}
                  </div>
                )
              })}
              {tools.length === 0 && (
                <p className="text-xs text-gray-400">{t('agent.tools.no_tools', '该本体暂无可用工具（需先发布）')}</p>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
