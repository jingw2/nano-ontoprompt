import { useTranslation } from 'react-i18next'

const COLOR: Record<string, string> = {
  draft: 'bg-gray-100 text-gray-600',
  creating: 'bg-blue-100 text-blue-700',
  created: 'bg-green-100 text-green-700',
  published: 'bg-emerald-100 text-emerald-700',
  archived: 'bg-yellow-100 text-yellow-700',
}

export default function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation()
  const label = t(`ontology.status_${status}`, status)
  return (
    <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium ${COLOR[status] ?? 'bg-gray-100'}`}>
      {label}
    </span>
  )
}
