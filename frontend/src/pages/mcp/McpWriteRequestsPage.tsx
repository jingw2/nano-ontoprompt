import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { mcpWriteRequestsApi, type McpWriteRequestItem } from '@/api/mcpWriteRequests'

export default function McpWriteRequestsPage() {
  const { t } = useTranslation()
  const [items, setItems] = useState<McpWriteRequestItem[] | null>(null)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [resolving, setResolving] = useState(false)

  const load = useCallback(() => {
    void Promise.resolve().then(() => {
      setItems(null)
      setError('')
    })
    mcpWriteRequestsApi.list()
      .then(res => setItems(Array.isArray(res.items) ? res.items : []))
      .catch(() => setError(t('mcp.load_failed')))
  }, [t])

  useEffect(() => {
    load()
  }, [load])

  const resolve = async (id: string, decision: 'approve' | 'reject') => {
    if (resolving) return
    setResolving(true)
    try {
      await (decision === 'approve' ? mcpWriteRequestsApi.approve(id) : mcpWriteRequestsApi.reject(id))
      setSelectedId(null)
      load()
    } catch {
      load()
    } finally {
      setResolving(false)
    }
  }

  if (error) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600" role="alert" data-testid="mcp-write-requests-error">
        <p>{error}</p>
        <button type="button" onClick={load} className="mt-2 px-3 py-1 text-xs border border-red-300 rounded hover:bg-red-100">
          {t('mcp.retry')}
        </button>
      </div>
    )
  }
  if (items === null) {
    return <div className="p-6 text-gray-400" data-testid="mcp-write-requests-loading">{t('common.loading', '加载中…')}</div>
  }

  return (
    <div data-testid="mcp-write-requests-page">
      <h2 className="text-base font-medium mb-3">{t('mcp.title')}</h2>
      {items.length === 0 ? (
        <p className="text-sm text-gray-400" data-testid="mcp-write-requests-empty">{t('mcp.empty')}</p>
      ) : (
        <ul className="space-y-2" data-testid="mcp-write-requests-list">
          {items.map(item => (
            <li key={item.id} className="border rounded-lg p-3" data-testid={`mcp-write-request-${item.id}`}>
              <button type="button" onClick={() => setSelectedId(prev => (prev === item.id ? null : item.id))} className="w-full text-left text-sm">
                <span className="font-mono text-xs text-gray-400">{item.id.slice(0, 8)}</span>
                <span className="ml-2">{item.descriptor_id}</span>
                <span className="ml-2 text-xs text-gray-500">status={item.status}</span>
              </button>
              {selectedId === item.id && (
                <div className="mt-3 border-t pt-3 text-xs space-y-2" data-testid="mcp-write-request-detail">
                  <dl className="grid grid-cols-2 gap-1 text-gray-600">
                    <dt>{t('mcp.ontology')}</dt><dd className="font-mono">{item.ontology_id}</dd>
                    <dt>{t('mcp.parameters')}</dt><dd className="font-mono break-all">{JSON.stringify(item.parameters)}</dd>
                  </dl>
                  <div className="flex gap-2">
                    <button type="button" disabled={resolving} onClick={() => resolve(item.id, 'approve')}
                      className="px-3 py-1 text-xs bg-black text-white rounded disabled:opacity-40" data-testid="mcp-write-request-approve">
                      {t('mcp.approve')}
                    </button>
                    <button type="button" disabled={resolving} onClick={() => resolve(item.id, 'reject')}
                      className="px-3 py-1 text-xs border rounded disabled:opacity-40" data-testid="mcp-write-request-reject">
                      {t('mcp.reject')}
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
