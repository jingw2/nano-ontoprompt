import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import {
  toolConnectionsApi, PROVIDER_KINDS, type ToolProvider,
} from '@/api/toolConnections'
import { Plus, Loader2, X } from 'lucide-react'

interface ProviderFormValues {
  name: string
  kind: string
}

export default function ToolConnectionsPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [expandedProviderId, setExpandedProviderId] = useState<string | null>(null)
  const [showCreateProvider, setShowCreateProvider] = useState(false)
  const [createError, setCreateError] = useState('')
  const { register, handleSubmit, reset } = useForm<ProviderFormValues>({
    defaultValues: { kind: 'search' },
  })

  const { data: providers, isLoading, isError, refetch } = useQuery({
    queryKey: ['tool-providers'],
    queryFn: () => toolConnectionsApi.listProviders().then(res => res.items),
  })

  const createProviderMut = useMutation({
    mutationFn: (data: ProviderFormValues) => toolConnectionsApi.createProvider(data.name, data.kind),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tool-providers'] })
      setShowCreateProvider(false)
      reset({ kind: 'search' })
      setCreateError('')
    },
    onError: (err: unknown) => {
      const e = err as { detail?: string; message?: string }
      setCreateError(e?.detail || e?.message || t('toolConnections.load_failed'))
    },
  })

  if (isError) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600" role="alert" data-testid="tool-connections-error">
        <p>{t('toolConnections.load_failed')}</p>
        <button type="button" onClick={() => refetch()} className="mt-2 px-3 py-1 text-xs border border-red-300 rounded hover:bg-red-100">
          {t('toolConnections.retry')}
        </button>
      </div>
    )
  }

  return (
    <div data-testid="tool-connections-page">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold">{t('toolConnections.title')}</h2>
        <button type="button" onClick={() => setShowCreateProvider(true)}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm" data-testid="create-provider-button">
          <Plus size={14} /> {t('toolConnections.create_provider')}
        </button>
      </div>

      {isLoading ? (
        <p className="text-gray-400 text-sm" data-testid="tool-connections-loading">{t('common.loading', '加载中…')}</p>
      ) : providers && providers.length === 0 ? (
        <p className="text-sm text-gray-400" data-testid="tool-providers-empty">{t('toolConnections.empty_providers')}</p>
      ) : (
        <div className="grid gap-3" data-testid="tool-providers-list">
          {(providers ?? []).map(provider => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              expanded={expandedProviderId === provider.id}
              onToggle={() => setExpandedProviderId(prev => (prev === provider.id ? null : provider.id))}
            />
          ))}
        </div>
      )}

      {showCreateProvider && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={() => setShowCreateProvider(false)}>
          <div className="bg-white rounded-lg shadow-lg p-6 w-[420px]" onClick={e => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-4">
              <h3 className="font-semibold">{t('toolConnections.create_provider')}</h3>
              <button type="button" onClick={() => setShowCreateProvider(false)} className="text-gray-400 hover:text-black"><X size={16} /></button>
            </div>
            {createError && <div className="mb-3 p-2.5 bg-red-50 border border-red-200 rounded-lg text-red-600 text-sm">{createError}</div>}
            <form onSubmit={handleSubmit(d => createProviderMut.mutate(d))} className="space-y-3">
              <div>
                <label className="block text-sm font-medium mb-1">{t('toolConnections.provider_name')} *</label>
                <input {...register('name', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" data-testid="provider-name-input" />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">{t('toolConnections.provider_kind')} *</label>
                <select {...register('kind', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" data-testid="provider-kind-select">
                  {PROVIDER_KINDS.map(kind => <option key={kind} value={kind}>{kind}</option>)}
                </select>
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setShowCreateProvider(false)} className="px-4 py-2 border rounded-lg text-sm">{t('toolConnections.cancel')}</button>
                <button type="submit" disabled={createProviderMut.isPending} className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50" data-testid="submit-create-provider">
                  {createProviderMut.isPending && <Loader2 size={13} className="animate-spin" />}{t('toolConnections.save')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

function ProviderCard({ provider, expanded, onToggle }: { provider: ToolProvider; expanded: boolean; onToggle: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()

  const { data: connections, isLoading: connectionsLoading } = useQuery({
    queryKey: ['tool-connections', provider.id],
    queryFn: () => toolConnectionsApi.listConnections(provider.id).then(res => res.items),
    enabled: expanded,
  })

  const createConnectionMut = useMutation({
    mutationFn: () => toolConnectionsApi.createConnection(provider.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tool-connections', provider.id] })
    },
  })

  return (
    <div className="bg-white border rounded-lg p-4" data-testid={`provider-card-${provider.id}`}>
      <button type="button" onClick={onToggle} className="w-full text-left text-sm flex items-center justify-between">
        <span>
          <span className="font-semibold">{provider.name}</span>
          <span className="ml-2 text-xs text-gray-500">{provider.kind} · {provider.status}</span>
        </span>
      </button>

      {expanded && (
        <div className="mt-3 border-t pt-3" data-testid={`provider-detail-${provider.id}`}>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium">{t('toolConnections.connections')}</h4>
            <button type="button" onClick={() => createConnectionMut.mutate()}
              disabled={createConnectionMut.isPending}
              className="flex items-center gap-1 px-2.5 py-1 border rounded text-xs hover:bg-gray-50 disabled:opacity-50" data-testid={`create-connection-${provider.id}`}>
              <Plus size={12} /> {t('toolConnections.create_connection')}
            </button>
          </div>
          {connectionsLoading ? (
            <p className="text-xs text-gray-400">{t('common.loading', '加载中…')}</p>
          ) : connections && connections.length === 0 ? (
            <p className="text-xs text-gray-400" data-testid={`connections-empty-${provider.id}`}>{t('toolConnections.empty_connections')}</p>
          ) : (
            <ul className="space-y-2">
              {(connections ?? []).map(conn => (
                <li key={conn.id} className="border rounded p-2 text-xs" data-testid={`connection-row-${conn.id}`}>
                  <span className="font-mono text-gray-400">{conn.id.slice(0, 8)}</span>
                  <span className="ml-2">{t('toolConnections.active_version')}: {conn.active_version_id ? conn.active_version_id.slice(0, 8) : t('toolConnections.none')}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
