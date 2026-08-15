import { useTranslation } from 'react-i18next'
import type { PublishedOntology } from '@/api/agentTools'

interface Props {
  ontology: PublishedOntology
  bound: boolean
  onToggle: (ontology: PublishedOntology, bind: boolean) => void
  onExpand?: (bound: boolean) => void
  disabled: boolean
}

export default function OntologyBindingCard({ ontology, bound, onToggle, onExpand, disabled }: Props) {
  const { t } = useTranslation()
  return (
    <div className="border rounded-lg p-4 flex items-center justify-between" data-testid="ontology-binding-card">
      <div>
        <p className="text-sm font-medium">{ontology.name}</p>
        <p className="text-xs text-gray-500 font-mono mt-0.5">{ontology.id}</p>
        <p className="text-xs text-gray-400 mt-1">{t('agent.tools.published_ontology', '已发布本体')}</p>
      </div>
      <button
        type="button"
        disabled={disabled}
        onClick={() => { onToggle(ontology, !bound); onExpand?.(!bound) }}
        className={`px-3 py-1.5 text-xs rounded-lg border ${bound ? 'bg-black text-white' : 'hover:bg-gray-50'} disabled:opacity-40`}
      >
        {bound ? t('agent.tools.bound', '已绑定') : t('agent.tools.bind', '绑定')}
      </button>
    </div>
  )
}
