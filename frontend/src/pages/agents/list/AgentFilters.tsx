import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

export interface AgentFilterValues {
  id: string
  name: string
  createdFrom: string
  createdTo: string
}

interface Props {
  values: AgentFilterValues
  onApply: (values: AgentFilterValues) => void
  onClear: () => void
}

export default function AgentFilters({ values, onApply, onClear }: Props) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(values)

  // keep local drafts in sync when the URL-preserved filters change
  useEffect(() => {
    void Promise.resolve().then(() => setDraft(values))
  }, [values])

  const dirty = draft.id !== values.id || draft.name !== values.name
    || draft.createdFrom !== values.createdFrom || draft.createdTo !== values.createdTo

  return (
    <div className="flex items-center gap-3 flex-wrap mb-4" data-testid="agent-filters">
      <input
        value={draft.id}
        onChange={e => setDraft(d => ({ ...d, id: e.target.value }))}
        placeholder={t('agent.list.id_filter', 'ID')}
        aria-label={t('agent.list.id_filter', 'ID')}
        className="w-56 border rounded-lg px-3 py-2 text-sm"
      />
      <input
        value={draft.name}
        onChange={e => setDraft(d => ({ ...d, name: e.target.value }))}
        placeholder={t('agent.list.name_filter', 'Name')}
        aria-label={t('agent.list.name_filter', 'Name')}
        className="w-56 border rounded-lg px-3 py-2 text-sm"
      />
      <input
        value={draft.createdFrom}
        onChange={e => setDraft(d => ({ ...d, createdFrom: e.target.value }))}
        placeholder={t('agent.list.created_from_placeholder', '2026-08-01T00:00:00Z')}
        aria-label={t('agent.list.created_from', 'Created from')}
        className="w-56 border rounded-lg px-3 py-2 text-sm"
      />
      <input
        value={draft.createdTo}
        onChange={e => setDraft(d => ({ ...d, createdTo: e.target.value }))}
        placeholder={t('agent.list.created_to_placeholder', '2026-08-31T23:59:59Z')}
        aria-label={t('agent.list.created_to', 'Created to')}
        className="w-56 border rounded-lg px-3 py-2 text-sm"
      />
      <button
        type="button"
        onClick={() => dirty && onApply(draft)}
        className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50 disabled:opacity-40"
        disabled={!dirty}
      >
        {t('agent.list.filter', 'Filter')}
      </button>
      <button
        type="button"
        onClick={onClear}
        className="px-4 py-2 text-sm text-gray-500 hover:text-black"
      >
        {t('agent.list.clear_filters', 'Clear filters')}
      </button>
    </div>
  )
}
