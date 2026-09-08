import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { agentsListApi, type AgentListItem } from '@/api/agentsList'
import AgentFilters, { type AgentFilterValues } from './AgentFilters'

const PAGE_SIZE = 50

export default function AgentListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const role = useAuthStore(s => s.user?.role)
  const canCreate = role === 'editor' || role === 'admin'

  const filters: AgentFilterValues = useMemo(() => ({
    id: searchParams.get('id') ?? '',
    name: searchParams.get('name') ?? '',
    createdFrom: searchParams.get('created_from') ?? '',
    createdTo: searchParams.get('created_before') ?? '',
  }), [searchParams])
  const cursor = searchParams.get('cursor') ?? ''

  const [items, setItems] = useState<AgentListItem[] | null>(null)
  const [loadError, setLoadError] = useState<{ message: string; correlation?: string } | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [cursorStack, setCursorStack] = useState<string[]>([])
  const [retryCount, setRetryCount] = useState(0)
  const [archiving, setArchiving] = useState<string | null>(null)
  const [archiveError, setArchiveError] = useState('')

  const applyFilters = useCallback((values: AgentFilterValues) => {
    const next = new URLSearchParams(searchParams)
    next.delete('cursor')
    const entries: [string, string][] = [
      ['id', values.id],
      ['name', values.name],
      ['created_from', values.createdFrom],
      ['created_before', values.createdTo],
    ]
    for (const [key, value] of entries) {
      if (value) next.set(key, value)
      else next.delete(key)
    }
    setCursorStack([])
    setSearchParams(next)
  }, [searchParams, setSearchParams])

  const clearFilters = useCallback(() => {
    setCursorStack([])
    setSearchParams(new URLSearchParams())
  }, [setSearchParams])

  const nextPage = useCallback(() => {
    if (!nextCursor) return
    setCursorStack(s => [...s, cursor])
    const next = new URLSearchParams(searchParams)
    next.set('cursor', nextCursor)
    setSearchParams(next)
  }, [nextCursor, cursor, searchParams, setSearchParams])

  const prevPage = useCallback(() => {
    if (cursorStack.length === 0) return
    const prev = cursorStack[cursorStack.length - 1]
    setCursorStack(s => s.slice(0, -1))
    const next = new URLSearchParams(searchParams)
    if (prev) next.set('cursor', prev)
    else next.delete('cursor')
    setSearchParams(next)
  }, [cursorStack, searchParams, setSearchParams])

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => { if (!cancelled) { setItems(null); setLoadError(null) } })
    agentsListApi.list({
      id: filters.id || undefined,
      name: filters.name || undefined,
      created_from: filters.createdFrom || undefined,
      created_before: filters.createdTo || undefined,
      cursor: cursor || undefined,
      limit: PAGE_SIZE,
    })
      .then(res => {
        if (cancelled) return
        setItems(Array.isArray(res.items) ? res.items : [])
        setNextCursor(res.next_cursor)
        setHasMore(!!res.has_more)
      })
      .catch(err => {
        if (cancelled) return
        setLoadError({
          message: 'AGENTS_LOAD_FAILED',
          correlation: err?.correlation_id ?? err?.error?.correlation_id ?? '',
        })
      })
    return () => { cancelled = true }
  }, [filters.id, filters.name, filters.createdFrom, filters.createdTo, cursor, retryCount])

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

  if (items === null && !loadError) {
    return (
      <div className="p-6" data-testid="agent-list-loading">
        <div className="flex items-center justify-between mb-4">
          <div className="h-6 w-32 bg-gray-200 animate-pulse rounded" />
          <div className="h-9 w-28 bg-gray-200 animate-pulse rounded" />
        </div>
        {[0, 1, 2].map(i => (
          <div key={i} className="h-12 bg-gray-100 animate-pulse rounded mb-2" />
        ))}
      </div>
    )
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">{t('agent.list.title', 'Agents')}</h2>
        {canCreate && (
          <button type="button" onClick={() => navigate('/agents/new')}
            className="bg-black text-white rounded-lg px-4 py-2 text-sm font-medium hover:bg-gray-800">
            {t('agent.list.create', '新建 Agent')}
          </button>
        )}
      </div>

      <AgentFilters values={filters} onApply={applyFilters} onClear={clearFilters} />

      {loadError && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-3 text-sm text-red-600" role="alert">
          <p>{t('agent.list.load_failed', '加载失败')}</p>
          {loadError.correlation && (
            <p className="text-xs text-red-400 mt-1">
              {t('agent.list.correlation', 'correlation')}: {loadError.correlation}
            </p>
          )}
          <button type="button" onClick={() => setRetryCount(c => c + 1)}
            className="mt-2 px-3 py-1 text-xs border border-red-300 rounded hover:bg-red-100">
            {t('agent.list.retry', 'Retry')}
          </button>
        </div>
      )}
      {archiveError && <p className="text-sm text-red-500 mb-3">{archiveError}</p>}

      {items !== null && items.length === 0 ? (
        <div className="bg-white border rounded-lg p-10 text-center text-gray-400 text-sm" data-testid="agent-list-empty">
          <p>{t('agent.list.empty_filtered', 'No Agents match these filters')}</p>
          <button type="button" onClick={clearFilters} className="mt-2 text-blue-600 hover:underline">
            {t('agent.list.clear_filters', 'Clear filters')}
          </button>
        </div>
      ) : items !== null ? (
        <div className="bg-white border rounded-lg overflow-hidden" data-testid="agent-list">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.id', 'ID')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.name', '名称')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.version', '版本')}</th>
                <th className="px-4 py-2 text-left text-xs text-gray-500 font-medium">{t('agent.list.status', '状态')}</th>
                <th className="px-4 py-2 text-right text-xs text-gray-500 font-medium">{t('agent.list.actions', '操作')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map(a => (
                <tr key={a.agent_id} className="border-t align-top hover:bg-gray-50">
                  <td className="px-4 py-2 font-mono text-xs text-gray-500">{a.agent_id}</td>
                  <td className="px-4 py-2">
                    <button type="button" onClick={() => navigate(`/agents/${a.agent_id}`)} className="text-blue-600 hover:underline">
                      {a.name ?? a.agent_id.slice(0, 8)}
                    </button>
                  </td>
                  <td className="px-4 py-2 text-gray-600">v{a.version_no ?? '—'}</td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded text-xs ${a.status === 'archived' ? 'bg-gray-100 text-gray-500' : 'bg-green-50 text-green-700'}`}>
                      {a.status === 'archived' ? t('agent.list.status_archived', '已归档') : t('agent.list.status_active', '活跃')}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right">
                    {a.status !== 'archived' && a.can_edit && (
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
      ) : null}

      {(hasMore || cursorStack.length > 0) && (
        <div className="flex justify-center gap-3 mt-4">
          {cursorStack.length > 0 && (
            <button type="button" onClick={prevPage}
              className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
              {t('agent.list.previous', '上一页')}
            </button>
          )}
          {hasMore && (
            <button type="button" onClick={nextPage}
              className="px-4 py-2 text-sm border rounded-lg hover:bg-gray-50">
              {t('agent.list.next', '下一页')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
