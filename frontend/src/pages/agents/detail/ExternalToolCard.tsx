import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { agentExternalToolsApi, type ExternalToolCatalogItem } from '@/api/agentExternalTools'
import { agentSkillsApi, type SkillCatalogItem } from '@/api/agentSkills'
import { slugifyAlias } from './aliasUtils'

export interface BoundExternalTool {
  alias: string
  tool_connection_version_id: string
  provider_name: string
  provider_kind: string
}

export interface BoundSkill {
  alias: string
  skill_version_id: string
  package_name: string
}

interface Props {
  bindings: BoundExternalTool[]
  canEdit: boolean
  onBind: (item: ExternalToolCatalogItem, alias: string) => void | Promise<void>
  onUnbind: (alias: string) => void | Promise<void>
  bindError?: string
  skillBindings: BoundSkill[]
  onBindSkill: (item: SkillCatalogItem, alias: string) => void | Promise<void>
  onUnbindSkill: (alias: string) => void | Promise<void>
  skillBindError?: string
}

const KIND_LABELS: Record<string, { label: string; fallback: string }> = {
  search: { label: 'agent.tools.name_search', fallback: 'Search' },
  playwright: { label: 'agent.tools.name_playwright', fallback: 'Playwright' },
  external_mcp: { label: 'agent.tools.name_mcp_connections', fallback: 'MCP Connections' },
  browser_use: { label: 'agent.tools.name_browser_use', fallback: 'Browser Use' },
}

/** One flat pool spanning every already-configured external tool AND every
 * approved Skill — the whole point is a single "add" affordance instead of
 * separate per-kind sections, so the option's own label carries its kind. */
type CatalogOption =
  | { key: string; source: 'external'; label: string; item: ExternalToolCatalogItem }
  | { key: string; source: 'skill'; label: string; item: SkillCatalogItem }

export default function ExternalToolCard({
  bindings, canEdit, onBind, onUnbind, bindError,
  skillBindings, onBindSkill, onUnbindSkill, skillBindError,
}: Props) {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<ExternalToolCatalogItem[]>([])
  const [error, setError] = useState('')
  const [skillCatalog, setSkillCatalog] = useState<SkillCatalogItem[]>([])
  const [skillError, setSkillError] = useState('')

  const [adding, setAdding] = useState(false)
  const [selectedKey, setSelectedKey] = useState('')
  const [alias, setAlias] = useState('')

  useEffect(() => {
    let cancelled = false
    agentExternalToolsApi.listCatalog()
      .then(res => { if (!cancelled) setCatalog(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_EXTERNAL_TOOLS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    agentSkillsApi.listCatalog()
      .then(res => { if (!cancelled) setSkillCatalog(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setSkillError('AGENTS_SKILLS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  const boundVersionIds = new Set(bindings.map(b => b.tool_connection_version_id))
  const boundSkillVersionIds = new Set(skillBindings.map(b => b.skill_version_id))
  const aliases = [...bindings.map(b => b.alias), ...skillBindings.map(b => b.alias)]

  const kindLabel = (kind: string) => t(KIND_LABELS[kind]?.label ?? '', KIND_LABELS[kind]?.fallback ?? kind)

  const options: CatalogOption[] = [
    ...catalog.filter(i => !boundVersionIds.has(i.tool_connection_version_id)).map(item => ({
      key: `external:${item.tool_connection_version_id}`, source: 'external' as const,
      label: `${item.provider_name} (${kindLabel(item.provider_kind)})`, item,
    })),
    ...skillCatalog.filter(i => !boundSkillVersionIds.has(i.skill_version_id)).map(item => ({
      key: `skill:${item.skill_version_id}`, source: 'skill' as const,
      label: `${item.package_name} (${t('agent.tools.name_signed_skills', 'Signed Skill')})`, item,
    })),
  ]

  const optionName = (option: CatalogOption) =>
    option.source === 'external' ? option.item.provider_name : option.item.package_name

  const closeAdd = () => { setAdding(false); setSelectedKey(''); setAlias('') }

  const openAdd = () => {
    const first = options[0]
    setAdding(true)
    setSelectedKey(first?.key ?? '')
    setAlias(first ? slugifyAlias(optionName(first), aliases) : '')
  }

  const confirmAdd = () => {
    const option = options.find(o => o.key === selectedKey)
    if (!option) return
    if (option.source === 'external') onBind(option.item, alias)
    else onBindSkill(option.item, alias)
    closeAdd()
  }

  const boundRows = [
    ...bindings.map(b => ({ alias: b.alias, label: `${b.provider_name} (${kindLabel(b.provider_kind)})`, unbind: () => onUnbind(b.alias) })),
    ...skillBindings.map(b => ({ alias: b.alias, label: `${b.package_name} (${t('agent.tools.name_signed_skills', 'Signed Skill')})`, unbind: () => onUnbindSkill(b.alias) })),
  ]

  return (
    <div data-testid="external-tool-cards" className="border rounded-lg p-4 space-y-1.5">
      {error && <p className="text-sm text-red-500">{error}</p>}
      {skillError && <p className="text-sm text-red-500">{skillError}</p>}
      {bindError && <p className="text-sm text-red-500">{bindError}</p>}
      {skillBindError && <p className="text-sm text-red-500">{skillBindError}</p>}
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium">{t('agent.tools.external', '外部工具')}</p>
        {canEdit && options.length > 0 && (
          <button type="button" onClick={() => (adding ? closeAdd() : openAdd())}
            data-testid="add-external-tool"
            className="flex items-center gap-1 px-2 py-0.5 text-xs border rounded hover:bg-gray-50">
            <Plus size={11} /> {t('agent.tools.add', 'Add')}
          </button>
        )}
      </div>
      {boundRows.map(row => (
        <div key={row.alias} className="flex items-center justify-between py-1.5 text-sm">
          <span>{row.label} · <span className="font-mono text-xs">{row.alias}</span></span>
          <button type="button" disabled={!canEdit} onClick={row.unbind}
            className="px-2 py-1 text-xs rounded border hover:bg-gray-50 disabled:opacity-40">
            {t('agent.tools.unbind', '解绑')}
          </button>
        </div>
      ))}
      {adding && (
        <div className="flex items-center gap-2 py-1.5 px-2 bg-gray-50 rounded">
          <select data-testid="add-tool-select" value={selectedKey}
            onChange={e => {
              setSelectedKey(e.target.value)
              const option = options.find(o => o.key === e.target.value)
              if (option) setAlias(slugifyAlias(optionName(option), aliases))
            }}
            className="border rounded px-2 py-1 text-xs flex-1 bg-white">
            {options.map(option => <option key={option.key} value={option.key}>{option.label}</option>)}
          </select>
          <input data-testid="add-tool-alias" value={alias}
            onChange={e => setAlias(e.target.value)}
            onKeyDown={e => {
              // this row can live inside AgentCreateWizard's outer <form> —
              // Enter must confirm the add, never bubble into a full submit
              if (e.key !== 'Enter') return
              e.preventDefault()
              confirmAdd()
            }}
            className="border rounded px-2 py-1 text-xs font-mono w-28" />
          <button type="button" data-testid="confirm-add-tool" onClick={confirmAdd}
            className="px-2 py-1 text-xs rounded border hover:bg-gray-50">
            {t('agent.tools.confirm', 'Confirm')}
          </button>
        </div>
      )}
      {boundRows.length === 0 && !adding && (
        <p className="text-xs text-gray-400" data-testid="external-tools-empty">
          {options.length === 0
            ? t('agent.tools.no_external_connections', '暂无可绑定的已激活连接，请先在工具连接管理中配置并激活')
            : t('agent.tools.no_bound_external_tools', '暂无已绑定的外部工具')}
        </p>
      )}
    </div>
  )
}
