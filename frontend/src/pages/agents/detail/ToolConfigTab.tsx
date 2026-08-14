import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { agentToolsApi, type PublishedOntology, type ToolValidationResult } from '@/api/agentTools'
import OntologyBindingCard from './OntologyBindingCard'
import ExternalToolCard from './ExternalToolCard'
import CapabilityDrawer from './CapabilityDrawer'

interface Props {
  agentId: string
  canEdit: boolean
  onDirtyChange: (dirty: boolean) => void
}

export default function ToolConfigTab({ agentId, canEdit, onDirtyChange }: Props) {
  const { t } = useTranslation()
  const [ontologies, setOntologies] = useState<PublishedOntology[]>([])
  const [boundIds, setBoundIds] = useState<string[]>([])
  const [validation, setValidation] = useState<ToolValidationResult | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    agentToolsApi.listPublishedOntologies()
      .then(res => { if (!cancelled) setOntologies(Array.isArray(res.items) ? res.items : []) })
      .catch(() => { if (!cancelled) setError('AGENTS_TOOLS_CATALOG_FAILED') })
    return () => { cancelled = true }
  }, [])

  const toggle = useCallback((ontology: PublishedOntology, bind: boolean) => {
    setBoundIds(prev => bind ? [...prev, ontology.id] : prev.filter(id => id !== ontology.id))
  }, [])

  // validate the proposed binding set whenever it changes
  useEffect(() => {
    if (!canEdit || boundIds.length === 0) return
    let cancelled = false
    agentToolsApi.validateAgentTools(agentId, { ontology_ids: boundIds })
      .then(result => { if (!cancelled) setValidation(result) })
      .catch(() => { if (!cancelled) setError('AGENTS_TOOLS_VALIDATION_FAILED') })
    return () => { cancelled = true }
  }, [agentId, boundIds, canEdit])

  useEffect(() => {
    onDirtyChange(boundIds.length > 0)
  }, [boundIds, onDirtyChange])

  return (
    <div className="p-6 space-y-6" data-testid="tool-config-tab">
      <div>
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.ontology_bindings', '本体绑定')}</h3>
        {error && <p className="text-sm text-red-500 mb-2">{error}</p>}
        <div className="space-y-2">
          {ontologies.map(o => (
            <OntologyBindingCard key={o.id} ontology={o} bound={boundIds.includes(o.id)}
              onToggle={toggle} disabled={!canEdit} />
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

      <div>
        <h3 className="text-sm font-medium text-gray-700 mb-2">{t('agent.tools.external', '外部工具')}</h3>
        <ExternalToolCard />
      </div>

      <p className="text-xs text-gray-400">
        {t('agent.tools.next_turn_refresh', '绑定将在下一次 Turn 生效并刷新')}
      </p>

      <CapabilityDrawer open={drawerOpen} onClose={() => setDrawerOpen(false)}
        capabilities={validation?.capabilities ?? []} />
    </div>
  )
}
