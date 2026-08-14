import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { agentsListApi, type AgentListItem } from '@/api/agentsList'
import AgentFilters from './AgentFilters'

export default function AgentListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [items, setItems] = useState<AgentListItem[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [page, setPage] = useState(1)
  const [hasMore, setHasMore] = useState(false)
  const [archiving, setArchiving] = useState<string | null>(null)
  const [archiveError, setArchiveError] = useState('')

  const onFilterChange = useCallback((patch: { search?: string; status?: string }) => {
    if (patch.search !== undefined) setSearch(patch.search)
    if (patch.status !== undefined) setStatus(patch.status)
  }, [])

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => { if (!cancelled) { setItems(null); setLoadError('') } })
    agentsListApi.list({ search: search || undefined, status: status || undefined, page })
      .then(res => {
        if (cancelled) return
        setItems(Array.isArray(res.items) ? res.items : [])
        setHasMore(!!res.has_more)
      })
      .catch(() => { if (!cancelled) setLoadError('AGENTS_LOAD_FAILED') })
    return () => { cancelled = true }
  }, [search, status, page])

  const handleArchive = async (agent: AgentListItem) => {
    setArchiving(agent.agent_id)
    setArchiveError('')
    try {
      await agentsListApi.archive(agent.agent_id)
      setItems(prev => (prev ?? []).map(a => a.agent_id === agent.agent_id ? { ...a, status: 'archived' } : a))
    } catch {
      setArchiveError(t('agent.list.archive_failed', '归档失败'))
    } finally {
      setArchiving(null)
    }
  }

  if (items === null) {
    return <div className="p-6 text-gray-400">{t('common.loading', '加载中...')}</div>
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">{t('agent.list.title', 'Agents')}</h2>
        <button type="button" onClick={() => navigate('/agents/new')}
          className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800">
          {t('agent.list.create', '新建 Agent')}
        </button>
      </div>

      <AgentFilters search={search} status={status} onChange={onFilterChange} />

      {loadError === 'AGENTS_LOAD_FAILED' && <p className="text-sm text-red-500 mb-3">{t('agent.list.load_failed', '加载失败')}</p>}
      {archiveError && <p className="text-sm text-red-500 mb-3">{archiveError}</p>}

      {items.length === 0 ? (
        <div className="bg-white border rounded-lg p-10 text-center text-gray-400 text-sm">
          {t('agent.list.empty', '暂无 Agent')}
        </div>
      ) : (
        <div className="bg-white border rounded-lg overflow-hidden" data-testid="agent-list">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.name', '名称')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.version', '版本')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.status', '状态')}</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 font-medium">{t('agent.list.actions', '操作')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(a => (
                <tr key={a.agent_id} className="border-t align-top hover:bg-gray-50">
                  <td className="px-4 py-2">
                    <button type="button" onClick={() => navigate(`/agents/${a.agent_id}`)} className="text-blue-600 hover:underline">
                      {a.name ?? a.agent_id.slice(0, 8)}
                    </button>
                    <p className="text-xs text-gray-400 mt-0.5">{a.agent_id}</p>
                  </td>
                  <td className="px-4 py-2 text-gray-600">v{a.version_no ?? '—'}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs ${a.status === 'archived' ? 'bg-gray-100 text-gray-500' : 'bg-green-50 text-green-700'}`}>
                      {a.status === 'archived' ? t('agent.list.status_archived', '已归档') : t('agent.list.status_active', '活跃')}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {a.status !== 'archived' && (
                      <button type="button" disabled={archiving === a.agent_id} onClick={() => handleArchive(a)}
                        className="text-xs text-red-500 hover:underline disabled:opacity-50">
                        {t('agent.list.archive', '归档')}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {hasMore && (
        <div className="flex justify-center mt-4">
          <button type="button" onClick={() => setPage(p => p + 1)}
            className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
            {t('agent.list.load_more', '加载更多')}
          </button>
        </div>
      )}
    </div>
  )
}
