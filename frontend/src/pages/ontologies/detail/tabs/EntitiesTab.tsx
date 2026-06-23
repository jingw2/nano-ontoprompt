
import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import ConfidenceBar from '@/components/ConfidenceBar'
import { Pencil, Trash2, Plus, ArrowUp, ArrowDown, ArrowUpDown, Search } from 'lucide-react'
import ConfirmDialog from '@/components/ConfirmDialog'
import type { Entity } from '@/types/ontology'
import { parseEntityDisplay } from '@/utils/entityDisplay'

type SortKey = 'name_cn' | 'abbr' | 'name_en' | 'canonical_id' | 'type' | 'description' | 'confidence'

export default function EntitiesTab({ ontologyId }: { ontologyId: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [showCreate, setShowCreate] = useState(false)
  const [searchQ, setSearchQ] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<Entity | null>(null)
  const [sortKey, setSortKey] = useState<SortKey>('name_cn')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const { register, handleSubmit, reset } = useForm<Partial<Entity>>()

  const { data: entities = [], isLoading } = useQuery({
    queryKey: ['entities', ontologyId],
    queryFn: () => ontologyApi.listEntities(ontologyId) as any,
  })

  const entityList = entities as Entity[]

  const typeOptions = useMemo(() => {
    const types = new Set(entityList.map(e => e.type).filter(Boolean) as string[])
    return Array.from(types).sort((a, b) => a.localeCompare(b, 'zh-CN'))
  }, [entityList])

  const displayedEntities = useMemo(() => {
    let list = [...entityList]
    if (typeFilter) {
      list = list.filter(e => e.type === typeFilter)
    }
    list.sort((a, b) => {
      let cmp = 0
      if (sortKey === 'confidence') {
        cmp = (a.confidence ?? 0) - (b.confidence ?? 0)
      } else if (sortKey === 'abbr') {
        const av = a.name_abbr?.trim() || parseEntityDisplay(a).abbr
        const bv = b.name_abbr?.trim() || parseEntityDisplay(b).abbr
        cmp = av.localeCompare(bv, 'en', { sensitivity: 'base' })
      } else if (sortKey === 'name_cn') {
        cmp = parseEntityDisplay(a).labelCn.localeCompare(parseEntityDisplay(b).labelCn, 'zh-CN', { sensitivity: 'base' })
      } else if (sortKey === 'canonical_id') {
        cmp = (a.canonical_id ?? '').localeCompare(b.canonical_id ?? '', 'en', { sensitivity: 'base' })
      } else {
        const av = String(a[sortKey] ?? '')
        const bv = String(b[sortKey] ?? '')
        cmp = av.localeCompare(bv, 'zh-CN', { sensitivity: 'base' })
      }
      return sortDir === 'asc' ? cmp : -cmp
    })
    return list
  }, [entityList, typeFilter, sortKey, sortDir])

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir(key === 'confidence' ? 'desc' : 'asc')
    }
  }

  const SortIcon = ({ column }: { column: SortKey }) => {
    if (sortKey !== column) return <ArrowUpDown size={12} className="text-gray-300" />
    return sortDir === 'asc'
      ? <ArrowUp size={12} className="text-gray-700" />
      : <ArrowDown size={12} className="text-gray-700" />
  }

  const createMut = useMutation({
    mutationFn: (data: Partial<Entity>) => ontologyApi.createEntity(ontologyId, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['entities', ontologyId] }); qc.invalidateQueries({ queryKey: ['stats'] }); setShowCreate(false); reset() },
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => ontologyApi.deleteEntity(ontologyId, id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['entities', ontologyId] }); qc.invalidateQueries({ queryKey: ['stats'] }) },
  })

  const sortableColumns: { key: SortKey; label: string }[] = [
    { key: 'name_cn', label: t('entities.col_name_cn') },
    { key: 'abbr', label: t('entities.col_abbr') },
    { key: 'name_en', label: t('entities.col_name_en') },
    { key: 'canonical_id', label: 'Canonical ID' },
    { key: 'type', label: t('entities.col_type') },
    { key: 'description', label: t('entities.col_desc') },
    { key: 'confidence', label: t('entities.col_confidence') },
  ]

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-gray-600">
            <span>{t('entities.filter_type')}</span>
            <select
              value={typeFilter}
              onChange={e => setTypeFilter(e.target.value)}
              className="border rounded-lg px-2 py-1.5 text-sm min-w-[140px]"
            >
              <option value="">{t('entities.filter_all_types')}</option>
              {typeOptions.map(type => (
                <option key={type} value={type}>{type}</option>
              ))}
            </select>
          </label>
          {entityList.length > 0 && (
            <span className="text-xs text-gray-400">
              {t('entities.filtered_count', { count: displayedEntities.length, total: entityList.length })}
            </span>
          )}
        </div>
  const allTypes = useMemo(() => {
    const s = new Set<string>()
    ;(entities as Entity[]).forEach(e => { if (e.type) s.add(e.type) })
    return Array.from(s).sort()
  }, [entities])

  const filtered = useMemo(() => {
    const q = searchQ.trim().toLowerCase()
    return (entities as Entity[]).filter(e => {
      const matchQ = !q || e.name_cn?.toLowerCase().includes(q) || e.name_en?.toLowerCase().includes(q) || e.type?.toLowerCase().includes(q)
      const matchType = !typeFilter || e.type === typeFilter
      return matchQ && matchType
    })
  }, [entities, searchQ, typeFilter])

  return (
    <div className="space-y-4">
      {/* Search bar */}
      <div className="flex gap-2 items-center">
        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
          <input value={searchQ} onChange={e => setSearchQ(e.target.value)}
            placeholder="搜索名称 / 类型…"
            className="w-full border rounded-lg pl-8 pr-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black" />
        </div>
        <select value={typeFilter} onChange={e => setTypeFilter(e.target.value)}
          className="border rounded-lg px-3 py-2 text-sm text-gray-600">
          <option value="">全部类型</option>
          {allTypes.map(tp => <option key={tp} value={tp}>{tp}</option>)}
        </select>
      </div>
      <div className="flex justify-end">
        <button onClick={() => { setShowCreate(true); reset() }}
          className="flex items-center gap-2 px-3 py-2 bg-black text-white rounded-lg text-sm">
          <Plus size={14} /> {t('entities.add')}
        </button>
      </div>

      <div className="bg-white border rounded-lg overflow-hidden">
        {isLoading ? <p className="py-8 text-center text-gray-400">{t('common.loading')}</p> : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                {sortableColumns.map(col => (
                  <th key={col.key} className="px-4 py-3 text-left text-gray-500 text-xs font-medium">
                    <button
                      type="button"
                      onClick={() => toggleSort(col.key)}
                      className="inline-flex items-center gap-1 hover:text-gray-800"
                    >
                      {col.label}
                      <SortIcon column={col.key} />
                    </button>
                  </th>
                ))}
                <th className="px-4 py-3 text-left text-gray-500 text-xs font-medium">{t('entities.col_actions')}</th>
              </tr>
            </thead>
            <tbody>
              {displayedEntities.map(e => {
                const { labelCn, abbr } = parseEntityDisplay(e)
                const displayAbbr = e.name_abbr?.trim() || abbr
                return (
              {filtered.map(e => (
                <tr key={e.id} className="border-b hover:bg-gray-50 cursor-pointer"
                  onClick={() => navigate(`/ontologies/${ontologyId}/entities/${e.id}`)}>
                  <td className="px-4 py-3 font-medium">{labelCn}</td>
                  <td className="px-4 py-3 text-gray-500 font-mono text-xs">{displayAbbr || '—'}</td>
                  <td className="px-4 py-3 text-gray-500">{e.name_en || '—'}</td>
                  <td className="px-4 py-3 font-mono text-xs text-green-700">{e.canonical_id || '—'}</td>
                  <td className="px-4 py-3 text-gray-500">{e.type || '—'}</td>
                  <td className="px-4 py-3 text-gray-500 max-w-xs truncate">{e.description || '—'}</td>
                  <td className="px-4 py-3 w-32"><ConfidenceBar value={e.confidence} /></td>
                  <td className="px-4 py-3" onClick={ev => ev.stopPropagation()}>
                    <div className="flex items-center gap-3">
                      <button onClick={() => navigate(`/ontologies/${ontologyId}/entities/${e.id}`)} title={t('common.edit')} className="p-1.5 rounded text-blue-500 hover:bg-blue-50"><Pencil size={14} /></button>
                      <button onClick={() => setDeleteTarget(e)} title={t('common.delete')} className="p-1.5 rounded text-red-500 hover:bg-red-50"><Trash2 size={14} /></button>
                    </div>
                  </td>
                </tr>
              )})}
            </tbody>
          </table>
        )}
        {!isLoading && filtered.length === 0 && (
          <p className="text-center text-gray-400 py-8">{searchQ || typeFilter ? '无匹配结果' : t('entities.empty')}</p>
        )}
        {!isLoading && entityList.length > 0 && displayedEntities.length === 0 && (
          <p className="text-center text-gray-400 py-8">{t('entities.no_match')}</p>
        )}
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-lg p-6 w-96">
            <h3 className="font-semibold mb-4">{t('entities.add')}</h3>
            <form onSubmit={handleSubmit(data => createMut.mutate(data))} className="space-y-3">
              <input {...register('name_cn', { required: true })} placeholder={t('entities.ph_name_cn')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              <input {...register('name_abbr')} placeholder={t('entities.ph_abbr')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              <input {...register('name_en')} placeholder={t('entities.ph_name_en')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              <input {...register('snomed_id')} placeholder="SNOMED-CT ID（如 366979004）" className="w-full border rounded-lg px-3 py-2 text-sm font-mono" />
              <input {...register('canonical_id')} placeholder="Canonical ID（如 symptom:depressed_mood）" className="w-full border rounded-lg px-3 py-2 text-sm font-mono" />
              <input {...register('type')} placeholder={t('entities.ph_type')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              <textarea {...register('description')} placeholder={t('entities.ph_desc')} rows={2} className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
              <input {...register('confidence', { valueAsNumber: true })} type="number" step="0.01" min="0" max="1" placeholder={t('entities.ph_confidence')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => { setShowCreate(false); reset() }} className="px-4 py-2 border rounded-lg text-sm">{t('common.cancel')}</button>
                <button type="submit" className="px-4 py-2 bg-black text-white rounded-lg text-sm">{t('common.save')}</button>
              </div>
            </form>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!deleteTarget}
        title={t('entities.delete_title')}
        message={t('entities.delete_confirm', { name: deleteTarget?.name_cn ?? '' })}
        onConfirm={() => { if (deleteTarget) deleteMut.mutate(deleteTarget.id); setDeleteTarget(null) }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}
