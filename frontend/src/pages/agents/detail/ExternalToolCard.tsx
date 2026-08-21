import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentExternalToolsApi, type ExternalToolCatalogItem } from '@/api/agentExternalTools'
import { slugifyAlias } from './aliasUtils'

export interface BoundExternalTool {
  alias: string
  tool_connection_version_id: string
  provider_name: string
  provider_kind: string
}

interface Props {
  bindings: BoundExternalTool[]
  canEdit: boolean
  onBind: (item: ExternalToolCatalogItem, alias: string) => void | Promise<void>
  onUnbind: (alias: string) => void | Promise<void>
  bindError?: string
}

const KIND_LABELS: Record<string, { label: string; fallback: string }> = {
  search: { label: 'agent.tools.name_search', fallback: 'Search' },
  playwright: { label: 'agent.tools.name_playwright', fallback: 'Playwright' },
  external_mcp: { label: 'agent.tools.name_mcp_connections', fallback: 'MCP Connections' },
}

const LIVE_KINDS = ['search', 'playwright', 'external_mcp'] as const

export default function ExternalToolCard({ bindings, canEdit, onBind, onUnbind, bindError }: Props) {
  const { t } = useTranslation()
  const [catalog, setCatalog] = useState<ExternalToolCatalogItem[]>([])
  const [error, setError] = useState('')
  const [aliasDrafts, setAliasDrafts] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    agentExternalToolsApi.listCatalog()
      .then(res => { if (!cancelled) setCatalog(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_EXTERNAL_TOOLS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  const boundVersionIds = new Set(bindings.map(b => b.tool_connection_version_id))
  const aliases = bindings.map(b => b.alias)

  const draftFor = (item: ExternalToolCatalogItem) =>
    aliasDrafts[item.tool_connection_version_id] ?? slugifyAlias(item.provider_name, aliases)

  return (
    <div data-testid="external-tool-cards" className="space-y-4">
      {error && <p className="text-sm text-red-500">{error}</p>}
      {bindError && <p className="text-sm text-red-500">{bindError}</p>}
      {LIVE_KINDS.map(kind => {
        const kindCatalog = catalog.filter(i => i.provider_kind === kind)
        const kindBindings = bindings.filter(b => b.provider_kind === kind)
        return (
          <div key={kind} className="border rounded-lg p-4" data-testid={`external-kind-${kind}`}>
            <p className="text-sm font-medium mb-2">{t(KIND_LABELS[kind].label, KIND_LABELS[kind].fallback)}</p>
            {kindBindings.map(b => (
              <div key={b.alias} className="flex items-center justify-between py-1.5 text-sm">
                <span>{b.provider_name} · <span className="font-mono text-xs">{b.alias}</span></span>
                <button type="button" disabled={!canEdit} onClick={() => onUnbind(b.alias)}
                  className="px-2 py-1 text-xs rounded border hover:bg-gray-50 disabled:opacity-40">
                  {t('agent.tools.unbind', '解绑')}
                </button>
              </div>
            ))}
            {kindCatalog.filter(i => !boundVersionIds.has(i.tool_connection_version_id)).map(item => (
              <div key={item.tool_connection_version_id} className="flex items-center gap-2 py-1.5 text-sm">
                <span className="flex-1">{item.provider_name}</span>
                <input
                  data-testid={`alias-input-${item.tool_connection_version_id}`}
                  disabled={!canEdit}
                  value={draftFor(item)}
                  onChange={e => setAliasDrafts(prev => ({ ...prev, [item.tool_connection_version_id]: e.target.value }))}
                  onKeyDown={e => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      onBind(item, draftFor(item))
                    }
                  }}
                  className="border rounded px-2 py-1 text-xs font-mono w-32"
                />
                <button type="button" disabled={!canEdit}
                  data-testid={`bind-${item.tool_connection_version_id}`}
                  onClick={() => onBind(item, draftFor(item))}
                  className="px-2 py-1 text-xs rounded border hover:bg-gray-50 disabled:opacity-40">
                  {t('agent.tools.bind', '绑定')}
                </button>
              </div>
            ))}
            {kindCatalog.length === 0 && (
              <p className="text-xs text-gray-400">{t('agent.tools.no_external_connections', '暂无可绑定的已激活连接，请先在工具连接管理中配置并激活')}</p>
            )}
          </div>
        )
      })}
      <div className="border rounded-lg p-4 bg-gray-50 opacity-70" data-testid="external-tool-card">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">{t('agent.tools.name_signed_skills', 'Signed Skills')}</p>
          <span className="px-2 py-0.5 rounded text-xs bg-gray-200 text-gray-600">
            {t('agent.tools.available_later', 'Available later')}
          </span>
        </div>
        <p className="text-xs text-gray-500 mt-1">{t('agent.tools.external_later', '外部工具将在后续版本提供')}</p>
      </div>
    </div>
  )
}
