import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

interface Props {
  search: string
  status: string
  onChange: (patch: { search?: string; status?: string }) => void
}

const STATUS_OPTIONS = [
  { value: '', labelKey: 'agent.list.status_all' },
  { value: 'active', labelKey: 'agent.list.status_active' },
  { value: 'archived', labelKey: 'agent.list.status_archived' },
]

export default function AgentFilters({ search, status, onChange }: Props) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState(search)

  useEffect(() => {
    const timer = setTimeout(() => {
      if (draft !== search) onChange({ search: draft })
    }, 250)
    return () => clearTimeout(timer)
  }, [draft, search, onChange])

  return (
    <div className="flex items-center gap-3 flex-wrap mb-4" data-testid="agent-filters">
      <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        placeholder={t('agent.list.search_placeholder', '搜索 Agent 名称…')}
        className="w-64 border rounded-lg px-3 py-2 text-sm"
      />
      <select
        value={status}
        onChange={e => onChange({ status: e.target.value })}
        className="border rounded-lg px-3 py-2 text-sm"
      >
        {STATUS_OPTIONS.map(o => (
          <option key={o.value} value={o.value}>{t(o.labelKey, o.value || '全部')}</option>
        ))}
      </select>
    </div>
  )
}
