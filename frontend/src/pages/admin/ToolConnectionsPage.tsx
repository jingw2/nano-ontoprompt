import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import {
  toolConnectionsApi, PROVIDER_KINDS, LIVE_PROVIDER_KINDS, type ToolProvider, type ToolConnection, type ToolConnectionVersion,
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
                <ConnectionRow key={conn.id} connection={conn} providerKind={provider.kind} />
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}

interface VersionFormValues {
  endpoint?: string
  audience?: string
  scopes_str?: string
  credential_reference?: string
  domains_str?: string
}

function ConnectionRow({ connection, providerKind }: { connection: ToolConnection; providerKind: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const [showCreateVersion, setShowCreateVersion] = useState(false)
  const [approvingVersion, setApprovingVersion] = useState<ToolConnectionVersion | null>(null)
  const [testResult, setTestResult] = useState<Record<string, { status: string; detail: string }>>({})
  const { register, handleSubmit, reset } = useForm<VersionFormValues>()

  const { data: versions, isLoading } = useQuery({
    queryKey: ['tool-connection-versions', connection.id],
    queryFn: () => toolConnectionsApi.listVersions(connection.id).then(res => res.items),
    enabled: expanded,
  })

  const createVersionMut = useMutation({
    mutationFn: (data: VersionFormValues) => toolConnectionsApi.createVersion({
      connection_id: connection.id,
      endpoint: data.endpoint || undefined,
      audience: data.audience || undefined,
      scopes: data.scopes_str ? data.scopes_str.split('\n').map(s => s.trim()).filter(Boolean) : undefined,
      credential_reference: data.credential_reference || undefined,
      allowlists: data.domains_str
        ? { domains: data.domains_str.split('\n').map(s => s.trim()).filter(Boolean) }
        : undefined,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tool-connection-versions', connection.id] })
      setShowCreateVersion(false)
      reset()
    },
  })

  const testMut = useMutation({
    mutationFn: (versionId: string) => toolConnectionsApi.testVersion(versionId),
    onSuccess: (res, versionId) => {
      setTestResult(prev => ({ ...prev, [versionId]: res }))
      qc.invalidateQueries({ queryKey: ['tool-connection-versions', connection.id] })
    },
  })

  const approveMut = useMutation({
    mutationFn: (versionId: string) => toolConnectionsApi.approveVersion(versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tool-connection-versions', connection.id] })
      setApprovingVersion(null)
    },
  })

  const activateMut = useMutation({
    mutationFn: (versionId: string) => toolConnectionsApi.activateVersion(connection.id, versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tool-connections'] })
      qc.invalidateQueries({ queryKey: ['tool-connection-versions', connection.id] })
    },
  })

  const canTest = (LIVE_PROVIDER_KINDS as readonly string[]).includes(providerKind)

  return (
    <li className="border rounded p-2 text-xs" data-testid={`connection-row-${connection.id}`}>
      <button type="button" onClick={() => setExpanded(e => !e)} className="w-full text-left flex items-center justify-between">
        <span>
          <span className="font-mono text-gray-400">{connection.id.slice(0, 8)}</span>
          <span className="ml-2">{t('toolConnections.active_version')}: {connection.active_version_id ? connection.active_version_id.slice(0, 8) : t('toolConnections.none')}</span>
        </span>
      </button>

      {expanded && (
        <div className="mt-2 border-t pt-2" data-testid={`connection-detail-${connection.id}`}>
          <div className="flex items-center justify-between mb-2">
            <h5 className="font-medium">{t('toolConnections.versions')}</h5>
            <button type="button" onClick={() => setShowCreateVersion(v => !v)}
              className="flex items-center gap-1 px-2 py-0.5 border rounded hover:bg-gray-50" data-testid={`create-version-${connection.id}`}>
              <Plus size={11} /> {t('toolConnections.create_version')}
            </button>
          </div>

          {showCreateVersion && (
            <form onSubmit={handleSubmit(d => createVersionMut.mutate(d))} className="space-y-2 mb-3 p-2 bg-gray-50 rounded">
              <input {...register('endpoint')} placeholder={t('toolConnections.endpoint')} className="w-full border rounded px-2 py-1" data-testid="version-endpoint-input" />
              <input {...register('audience')} placeholder={t('toolConnections.audience')} className="w-full border rounded px-2 py-1" />
              <textarea {...register('scopes_str')} placeholder={t('toolConnections.scopes')} rows={2} className="w-full border rounded px-2 py-1 font-mono" />
              <input {...register('credential_reference')} placeholder={t('toolConnections.credential_reference')} className="w-full border rounded px-2 py-1" />
              <textarea {...register('domains_str')} placeholder={t('toolConnections.allowlist_domains')} rows={2} className="w-full border rounded px-2 py-1 font-mono" />
              <button type="submit" disabled={createVersionMut.isPending} className="px-3 py-1 bg-black text-white rounded disabled:opacity-50" data-testid="submit-create-version">
                {t('toolConnections.save')}
              </button>
            </form>
          )}

          {isLoading ? (
            <p className="text-gray-400">{t('common.loading', '加载中…')}</p>
          ) : versions && versions.length === 0 ? (
            <p className="text-gray-400" data-testid={`versions-empty-${connection.id}`}>{t('toolConnections.empty_versions')}</p>
          ) : (
            <ul className="space-y-1.5">
              {(versions ?? []).map(v => (
                <li key={v.id} className="border rounded p-1.5" data-testid={`version-row-${v.id}`}>
                  <div className="flex items-center justify-between">
                    <span>
                      v{v.version_no} ·{' '}
                      <span className={v.approval_status === 'approved' ? 'text-green-600' : v.approval_status === 'rejected' ? 'text-red-600' : 'text-amber-600'}>
                        {t(`toolConnections.approval_${v.approval_status}`)}
                      </span>
                      {' · '}
                      <span className={v.health_status === 'healthy' ? 'text-green-600' : v.health_status === 'unhealthy' ? 'text-red-600' : 'text-gray-400'}>
                        {t(`toolConnections.health_${v.health_status}`)}
                      </span>
                      {connection.active_version_id === v.id && (
                        <span className="ml-1 bg-black text-white px-1.5 py-0.5 rounded text-[10px]">{t('toolConnections.activated')}</span>
                      )}
                    </span>
                    <span className="flex gap-1">
                      {canTest && v.approval_status === 'approved' && (
                        <button type="button" onClick={() => testMut.mutate(v.id)} disabled={testMut.isPending}
                          className="px-2 py-0.5 border rounded hover:bg-gray-50 disabled:opacity-50" data-testid={`test-version-${v.id}`}>
                          {t('toolConnections.test')}
                        </button>
                      )}
                      {v.approval_status === 'pending' && (
                        <button type="button" onClick={() => setApprovingVersion(v)}
                          className="px-2 py-0.5 border rounded hover:bg-gray-50 text-blue-600" data-testid={`approve-version-${v.id}`}>
                          {t('toolConnections.approve')}
                        </button>
                      )}
                      {v.approval_status === 'approved' && connection.active_version_id !== v.id && (
                        <button type="button" onClick={() => activateMut.mutate(v.id)} disabled={activateMut.isPending}
                          className="px-2 py-0.5 border rounded hover:bg-gray-50" data-testid={`activate-version-${v.id}`}>
                          {t('toolConnections.activate')}
                        </button>
                      )}
                    </span>
                  </div>
                  {testResult[v.id] && (
                    <p className={`mt-1 ${testResult[v.id].status === 'healthy' ? 'text-green-600' : 'text-amber-600'}`} data-testid={`test-result-${v.id}`}>
                      {testResult[v.id].detail}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {approvingVersion && (
        <VersionApprovalDialog
          version={approvingVersion}
          onCancel={() => setApprovingVersion(null)}
          onConfirm={() => approveMut.mutate(approvingVersion.id)}
          isPending={approveMut.isPending}
        />
      )}
    </li>
  )
}

function VersionApprovalDialog({
  version, onCancel, onConfirm, isPending,
}: {
  version: ToolConnectionVersion
  onCancel: () => void
  onConfirm: () => void
  isPending: boolean
}) {
  const { t } = useTranslation()
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50" onClick={onCancel}>
      <div className="bg-white rounded-lg shadow-lg p-6 w-[480px]" onClick={e => e.stopPropagation()} data-testid="approve-version-dialog">
        <h3 className="font-semibold mb-2">{t('toolConnections.approve_confirm_title')}</h3>
        <p className="text-sm text-gray-500 mb-3">{t('toolConnections.approve_confirm_body')}</p>
        <dl className="grid grid-cols-2 gap-1 text-xs text-gray-600 mb-4 bg-gray-50 rounded p-2">
          <dt>{t('toolConnections.endpoint')}</dt><dd className="font-mono break-all">{version.endpoint || '—'}</dd>
          <dt>{t('toolConnections.audience')}</dt><dd className="font-mono break-all">{version.audience || '—'}</dd>
          <dt>{t('toolConnections.scopes')}</dt><dd className="font-mono break-all">{version.scopes.join(', ') || '—'}</dd>
          <dt>{t('toolConnections.allowlist_domains')}</dt>
          <dd className="font-mono break-all">{((version.allowlists?.domains as string[] | undefined) ?? []).join(', ') || '—'}</dd>
        </dl>
        <div className="flex justify-end gap-3">
          <button type="button" onClick={onCancel} className="px-4 py-2 border rounded-lg text-sm">{t('toolConnections.cancel')}</button>
          <button type="button" onClick={onConfirm} disabled={isPending}
            className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50" data-testid="confirm-approve-version">
            {t('toolConnections.confirm')}
          </button>
        </div>
      </div>
    </div>
  )
}
