import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { promptApi } from '@/api/ontologies'
import ConfirmDialog from '@/components/ConfirmDialog'
import { DOMAINS } from '@/types/ontology'
import type { Prompt } from '@/types/ontology'
import { X } from 'lucide-react'

export default function PromptListPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [idFilter, setIdFilter] = useState('')
  const [nameFilter, setNameFilter] = useState('')
  const [domainFilter, setDomainFilter] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Prompt | null>(null)

  const { data: allPrompts = [], isLoading } = useQuery({
    queryKey: ['prompts'],
    queryFn: () => promptApi.list(),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => promptApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['prompts'] }); setDeleteTarget(null) },
  })

  const filtered = useMemo(() => {
    let list = allPrompts as Prompt[]
    if (idFilter.trim())
      list = list.filter(p => p.id.toLowerCase().includes(idFilter.trim().toLowerCase()))
    if (nameFilter.trim())
      list = list.filter(p => p.name.toLowerCase().includes(nameFilter.trim().toLowerCase()))
    if (domainFilter)
      list = list.filter(p => p.domain === domainFilter)
    if (dateFrom)
      list = list.filter(p => new Date(p.created_at) >= new Date(dateFrom))
    if (dateTo)
      list = list.filter(p => new Date(p.created_at) <= new Date(dateTo + 'T23:59:59'))
    return list
  }, [allPrompts, idFilter, nameFilter, domainFilter, dateFrom, dateTo])

  const hasFilters = idFilter || nameFilter || domainFilter || dateFrom || dateTo

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold">{t('prompt.title')}</h2>
        <button onClick={() => navigate('/prompts/create')}
          className="bg-black text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-gray-800">
          {t('prompt.create')}
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-2 mb-4 items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">ID</label>
          <input value={idFilter} onChange={e => setIdFilter(e.target.value)}
            placeholder={t('prompt.search_id')}
            className="border rounded-lg px-3 py-2 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('prompt.name')}</label>
          <input value={nameFilter} onChange={e => setNameFilter(e.target.value)}
            placeholder={t('prompt.search_name')}
            className="border rounded-lg px-3 py-2 text-sm w-44 focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('prompt.domain')}</label>
          <select value={domainFilter} onChange={e => setDomainFilter(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm">
            <option value="">{t('prompt.all_domains')}</option>
            {DOMAINS.map(d => <option key={d}>{d}</option>)}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('prompt.date_from')}</label>
          <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs text-gray-500">{t('prompt.date_to')}</label>
          <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)}
            className="border rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        {hasFilters && (
          <button onClick={() => { setIdFilter(''); setNameFilter(''); setDomainFilter(''); setDateFrom(''); setDateTo('') }}
            className="flex items-center gap-1 px-3 py-2 text-sm text-gray-500 border rounded-lg hover:bg-gray-50 self-end">
            <X size={14} /> {t('prompt.clear_filter')}
          </button>
        )}
        {hasFilters && (
          <span className="text-xs text-gray-400 self-end pb-2">
            {t('prompt.count_summary', { filtered: filtered.length, total: (allPrompts as Prompt[]).length })}
          </span>
        )}
      </div>

      <div className="bg-white border rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 border-b">
            <tr>
              {[t('prompt.col_id'), t('prompt.col_name'), t('prompt.col_domain'), t('prompt.col_version'), t('prompt.col_created'), t('prompt.col_actions')].map(h => (
                <th key={h} className="px-4 py-3 text-left text-gray-500 text-xs font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr><td colSpan={6} className="py-8 text-center text-gray-400">{t('common.loading')}</td></tr>
            ) : filtered.map(p => (
              <tr key={p.id} className="border-b hover:bg-gray-50">
                <td className="px-4 py-3 font-mono text-xs text-gray-400" title={p.id}>{p.id.slice(0, 8)}</td>
                <td className="px-4 py-3 font-medium">{p.name}</td>
                <td className="px-4 py-3 text-gray-500">{p.domain}</td>
                <td className="px-4 py-3 text-gray-500">{p.version}</td>
                <td className="px-4 py-3 text-gray-500 text-xs">{new Date(p.created_at).toLocaleDateString()}</td>
                <td className="px-4 py-3 space-x-3">
                  <button onClick={() => navigate(`/prompts/${p.id}`)} className="text-blue-600 hover:underline text-xs">{t('prompt.view_edit')}</button>
                  <button onClick={() => setDeleteTarget(p)} className="text-red-600 hover:underline text-xs">{t('prompt.delete')}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!isLoading && filtered.length === 0 && (
          <p className="text-center text-gray-400 py-8">
            {hasFilters ? t('prompt.no_match') : t('prompt.empty')}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        title={t('prompt.confirm_delete')}
        message={t('prompt.confirm_delete_msg', { name: deleteTarget?.name })}
        onConfirm={() => deleteTarget && deleteMut.mutate(deleteTarget.id)}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
