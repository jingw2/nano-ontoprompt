import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { apiClient } from '@/api/client'
import { useTranslation } from 'react-i18next'
import StatusBadge from '@/components/StatusBadge'

interface RecentOntology {
  id: string
  name: string
  domain: string
  status: string
  entity_count: number
  logic_count: number
  action_count: number
  updated_at: string
}

interface Stats {
  ontology_count: number
  entity_count: number
  logic_count: number
  action_count: number
  recent_ontologies: RecentOntology[]
  domain_counts: Record<string, number>
  status_counts: Record<string, number>
}

const DOMAIN_COLORS: Record<string, string> = {
  '供应链': 'bg-blue-500',
  '医疗': 'bg-green-500',
  '财务': 'bg-yellow-500',
  '法律': 'bg-purple-500',
  '教育': 'bg-pink-500',
  '其他': 'bg-gray-400',
}

export default function OverviewPage() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { data, isLoading } = useQuery<Stats>({
    queryKey: ['stats'],
    queryFn: () => apiClient.get('/overview/stats') as any,
  })

  const cards: Array<{ key: 'ontology_count' | 'entity_count' | 'logic_count' | 'action_count'; label: string; color: string; icon: string }> = [
    { key: 'ontology_count', label: t('overview.ontology_count'), color: 'border-blue-200', icon: '🗂️' },
    { key: 'entity_count', label: t('overview.entity_count'), color: 'border-green-200', icon: '📦' },
    { key: 'logic_count', label: t('overview.logic_count'), color: 'border-purple-200', icon: '⚖️' },
    { key: 'action_count', label: t('overview.action_count'), color: 'border-orange-200', icon: '⚡' },
  ]

  if (isLoading) return <p className="text-gray-400 p-6">{t('common.loading')}</p>

  const domainEntries = Object.entries(data?.domain_counts ?? {}).sort((a, b) => b[1] - a[1])
  const maxDomainCount = Math.max(...domainEntries.map(([, v]) => v), 1)
  const statusEntries = Object.entries(data?.status_counts ?? {})
  const totalOntologies = data?.ontology_count ?? 0
  const locale = i18n.language === 'zh' ? 'zh-CN' : 'en-US'

  return (
    <div className="space-y-6">
      <h2 className="text-xl font-semibold">{t('overview.title')}</h2>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {cards.map(({ key, label, color, icon }) => {
          const value = data?.[key] ?? 0
          return (
          <div key={key} className={`bg-white rounded-xl border ${color} p-5`}>
            <div className="flex items-center justify-between mb-2">
              <p className="text-sm text-gray-500">{label}</p>
              <span className="text-xl">{icon}</span>
            </div>
            <p className="text-4xl font-bold">{value}</p>
          </div>
          )
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Recent ontologies */}
        <div className="lg:col-span-2 bg-white rounded-xl border p-5">
          <h3 className="font-semibold mb-4 text-gray-800">{t('overview.recent_updated')}</h3>
          {(data?.recent_ontologies ?? []).length === 0 ? (
            <p className="text-gray-400 text-sm py-6 text-center">{t('overview.empty')}</p>
          ) : (
            <div className="divide-y">
              {(data?.recent_ontologies ?? []).map(o => (
                <div
                  key={o.id}
                  className="py-3 flex items-center gap-3 cursor-pointer hover:bg-gray-50 -mx-2 px-2 rounded transition-colors"
                  onClick={() => navigate(`/ontologies/${o.id}`)}
                >
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm truncate">{o.name}</span>
                      <StatusBadge status={o.status} />
                    </div>
                    <p className="text-xs text-gray-400 mt-0.5">{o.domain}</p>
                  </div>
                  <div className="flex gap-3 text-xs text-gray-500 flex-shrink-0">
                    <span title={t('overview.entities_label')}>📦 {o.entity_count}</span>
                    <span title={t('overview.logic_label')}>⚖️ {o.logic_count}</span>
                    <span title={t('overview.actions_label')}>⚡ {o.action_count}</span>
                  </div>
                  <span className="text-xs text-gray-400 flex-shrink-0 hidden sm:block">
                    {o.updated_at ? new Date(o.updated_at).toLocaleDateString(locale) : '—'}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right column: domain distribution + status breakdown */}
        <div className="space-y-5">
          {/* Domain distribution */}
          <div className="bg-white rounded-xl border p-5">
            <h3 className="font-semibold mb-4 text-gray-800">{t('overview.domain_dist')}</h3>
            {domainEntries.length === 0 ? (
              <p className="text-gray-400 text-sm text-center py-4">{t('overview.no_data')}</p>
            ) : (
              <div className="space-y-2.5">
                {domainEntries.map(([domain, count]) => (
                  <div key={domain}>
                    <div className="flex items-center justify-between text-xs text-gray-600 mb-1">
                      <span>{domain}</span>
                      <span className="font-medium">{count}</span>
                    </div>
                    <div className="w-full bg-gray-100 rounded-full h-1.5">
                      <div
                        className={`h-1.5 rounded-full ${DOMAIN_COLORS[domain] ?? 'bg-gray-400'}`}
                        style={{ width: `${(count / maxDomainCount) * 100}%` }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Status breakdown */}
          {totalOntologies > 0 && (
            <div className="bg-white rounded-xl border p-5">
              <h3 className="font-semibold mb-4 text-gray-800">{t('overview.ont_status')}</h3>
              <div className="space-y-2">
                {statusEntries.map(([status, count]) => (
                  <div key={status} className="flex items-center justify-between text-sm">
                    <StatusBadge status={status} />
                    <span className="font-medium text-gray-700">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
