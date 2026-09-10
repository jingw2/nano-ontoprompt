import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import StatusBadge from '@/components/StatusBadge'
import ConfirmDialog from '@/components/ConfirmDialog'
import type { OntologyListItem } from '@/types/ontology'
import { X, Plus } from 'lucide-react'

const EMPTY_ITEMS: OntologyListItem[] = []

function deleteFailureKey(detail: string | undefined): string {
  return detail === 'ONTOLOGY_BOUND_TO_AGENT' ? 'ontology.delete_failed_bound_agent'
    : detail === 'ONTOLOGY_HAS_DEPENDENT_DATA' ? 'ontology.delete_failed_dependent_data'
    : 'ontology.delete_failed_generic'
}

export default function OntologyListPage() {
  const [idFilter, setIdFilter] = useState('')
  const [nameFilter, setNameFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [bulkConfirming, setBulkConfirming] = useState(false)
  const [bulkDeleting, setBulkDeleting] = useState(false)
  const [bulkResult, setBulkResult] = useState<{ succeeded: number; failed: { name: string; reason: string }[] } | null>(null)
  const qc = useQueryClient()
  const navigate = useNavigate()
  const { t, i18n } = useTranslation()

  const { data, isLoading } = useQuery({
    queryKey: ['ontologies'],
    queryFn: () => ontologyApi.list({ page_size: 1000 }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => ontologyApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ontologies'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      setDeleteTarget(null)
      setDeleteError('')
    },
    onError: (err: unknown) => {
      const detail = (err as { detail?: string })?.detail
      setDeleteError(t(deleteFailureKey(detail)))
    },
  })

  const toggleSelected = (id: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const toggleSelectAll = (ids: string[]) => {
    setSelected(prev => (prev.size === ids.length ? new Set() : new Set(ids)))
  }

  const runBulkDelete = async () => {
    setBulkDeleting(true)
    const targets = allItems.filter(o => selected.has(o.id))
    const results = await Promise.allSettled(targets.map(o => ontologyApi.delete(o.id)))
    const failedIds = new Set<string>()
    const failed: { name: string; reason: string }[] = []
    results.forEach((res, i) => {
      if (res.status === 'rejected') {
        const detail = (res.reason as { detail?: string })?.detail
        failedIds.add(targets[i].id)
        failed.push({ name: targets[i].name, reason: t(deleteFailureKey(detail)) })
      }
    })
    qc.invalidateQueries({ queryKey: ['ontologies'] })
    qc.invalidateQueries({ queryKey: ['stats'] })
    setSelected(failedIds)
    setBulkResult({ succeeded: targets.length - failed.length, failed })
    setBulkDeleting(false)
    setBulkConfirming(false)
  }

  const allItems: OntologyListItem[] = data?.items ?? EMPTY_ITEMS

  const filteredItems = useMemo(() => {
    let list = allItems
    if (idFilter.trim())
      list = list.filter(o => o.id.toLowerCase().includes(idFilter.trim().toLowerCase()))
    if (nameFilter.trim())
      list = list.filter(o => o.name.toLowerCase().includes(nameFilter.trim().toLowerCase()))
    if (dateFrom)
      list = list.filter(o => new Date(o.created_at) >= new Date(dateFrom))
    if (dateTo)
      list = list.filter(o => new Date(o.created_at) <= new Date(dateTo + 'T23:59:59'))
    return list
  }, [allItems, idFilter, nameFilter, dateFrom, dateTo])

  const hasFilters = idFilter || nameFilter || dateFrom || dateTo

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold">{t('ontology.title')}</h2>
        <button onClick={() => navigate('/ontologies/new')}
          className="flex items-center gap-2 bg-black text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-800">
          <Plus size={14} /> {t('ontology.create')}
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 mb-4 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">ID</label>
          <input value={idFilter} onChange={e => setIdFilter(e.target.value)}
            placeholder={t('ontology.search_id')}
            className="border rounded-lg px-3 py-2 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('ontology.name')}</label>
          <input value={nameFilter} onChange={e => setNameFilter(e.target.value)}
            placeholder={t('ontology.filter_placeholder')}
            className="border rounded-lg px-3 py-2 text-sm w-48 focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('ontology.date_from')}</label>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('ontology.date_to')}</label>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        {hasFilters && (
          <button onClick={() => { setIdFilter(''); setNameFilter(''); setDateFrom(''); setDateTo('') }}
            className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 border rounded-lg hover:bg-gray-50 self-end">
            <X size={14} /> {t('ontology.clear_filter')}
          </button>
        )}
        {hasFilters && (
          <span className="text-xs text-gray-400 self-end pb-2">
            {t('ontology.count_summary', { filtered: filteredItems.length, total: allItems.length })}
          </span>
        )}
      </div>

      {selected.size > 0 && (
        <div className="flex items-center gap-3 mb-3 bg-red-50 border border-red-200 rounded-lg px-4 py-2">
          <span className="text-sm text-red-700">{t('ontology.selected_count', { count: selected.size })}</span>
          <button onClick={() => setBulkConfirming(true)}
            className="px-3 py-1.5 text-xs bg-red-600 text-white rounded-lg hover:bg-red-700">
            {t('ontology.bulk_delete')}
          </button>
          <button onClick={() => setSelected(new Set())}
            className="text-xs text-gray-500 hover:underline">
            {t('ontology.clear_selection')}
          </button>
        </div>
      )}

      {bulkResult && (
        <div className={`mb-3 rounded-lg px-4 py-2 text-sm ${bulkResult.failed.length ? 'bg-amber-50 border border-amber-200 text-amber-700' : 'bg-green-50 border border-green-200 text-green-700'}`}>
          <p>{t('ontology.bulk_delete_summary', { succeeded: bulkResult.succeeded, failed: bulkResult.failed.length })}</p>
          {bulkResult.failed.map(f => (
            <p key={f.name} className="text-xs mt-1">{f.name}: {f.reason}</p>
          ))}
          <button onClick={() => setBulkResult(null)} className="text-xs underline mt-1">{t('ontology.dismiss')}</button>
        </div>
      )}

      <div className="bg-white rounded-lg shadow overflow-hidden overflow-x-auto">
        <table className="w-full text-sm table-fixed">
          <colgroup>
            <col className="w-8" />
            <col className="w-20" />
            <col className="w-64" />
            <col className="w-20" />
            <col className="w-28" />
            <col className="w-16" />
            <col className="w-16" />
            <col className="w-20" />
            <col className="w-24" />
            <col className="w-28" />
          </colgroup>
          <thead className="bg-gray-50 border-b">
            <tr>
              <th className="px-4 py-3 text-left">
                <input type="checkbox"
                  checked={filteredItems.length > 0 && filteredItems.every(o => selected.has(o.id))}
                  onChange={() => toggleSelectAll(filteredItems.map(o => o.id))}
                  aria-label={t('ontology.select_all')} />
              </th>
              {['ID', t('ontology.name'), t('ontology.domain'), '构建方式', '实体', '关系', t('ontology.status'), t('ontology.created_at'), t('ontology.actions')].map(h => (
                <th key={h} className="px-4 py-3 text-left text-gray-500 font-medium text-xs">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={9} className="text-center py-8 text-gray-400">{t('common.loading')}</td></tr>
            ) : filteredItems.map((o) => (
              <tr key={o.id} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3">
                  <input type="checkbox" checked={selected.has(o.id)} onChange={() => toggleSelected(o.id)}
                    aria-label={t('ontology.select_row', { name: o.name })} />
                </td>
                <td className="px-4 py-3 font-mono text-xs text-gray-400" title={o.id}>{o.id.slice(0, 8)}</td>
                <td className="px-4 py-3 font-medium truncate" title={o.name}>{o.name}</td>
                <td className="px-4 py-3 text-gray-500 truncate" title={o.domain}>{o.domain}</td>
                <td className="px-4 py-3">
                  {o.build_mode === 'pipeline_mapping'
                    ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-blue-50 border border-blue-200 text-blue-700 whitespace-nowrap">🔄 Pipeline</span>
                    : <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-amber-50 border border-amber-200 text-amber-700 whitespace-nowrap">⚡ 简易LLM</span>
                  }
                </td>
                <td className="px-4 py-3 text-gray-500 font-mono text-xs">{o.entity_count ?? 0}</td>
                <td className="px-4 py-3 text-gray-500 font-mono text-xs">{o.relation_count ?? 0}</td>
                <td className="px-4 py-3"><StatusBadge status={o.status} /></td>
                <td className="px-4 py-3 text-gray-500 text-xs whitespace-nowrap">{new Date(o.created_at).toLocaleDateString(i18n.language === 'zh' ? 'zh-CN' : 'en-US')}</td>
                <td className="px-4 py-3 space-x-3 whitespace-nowrap">
                  <button onClick={() => navigate(`/ontologies/${o.id}`)}
                    className="text-blue-600 hover:underline text-xs">{t('ontology.view')}</button>
                  <button onClick={() => { setDeleteError(''); setDeleteTarget({ id: o.id, name: o.name }) }}
                    className="text-red-600 hover:underline text-xs">{t('ontology.delete')}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!isLoading && filteredItems.length === 0 && (
          <p className="text-center text-gray-400 py-8">
            {hasFilters ? t('ontology.no_match') : t('ontology.empty')}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        title={t('ontology.confirm_delete')}
        message={t('ontology.confirm_delete_msg', { name: deleteTarget?.name })}
        error={deleteError}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
        onCancel={() => { setDeleteTarget(null); setDeleteError('') }}
      />

      <ConfirmDialog
        open={bulkConfirming}
        title={t('ontology.confirm_bulk_delete')}
        message={t('ontology.confirm_bulk_delete_msg', { count: selected.size })}
        confirmLabel={bulkDeleting ? t('ontology.deleting') : t('ontology.confirm')}
        onConfirm={runBulkDelete}
        onCancel={() => setBulkConfirming(false)}
      />
    </div>
  )
}
