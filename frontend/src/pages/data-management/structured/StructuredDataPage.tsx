import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Search, CheckCircle, AlertTriangle, Clock,
  ExternalLink, X, Loader2, Trash2,
} from 'lucide-react'
import pipelinesApi, { type Pipeline } from '@/api/v2/pipelines'
import curatedApi from '@/api/v2/curated'
import type { CuratedDataset } from '@/api/v2/curated'
import CuratedDetailPanel from './CuratedDetailPanel'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Row {
  pipelineId: string
  pipelineName: string
  domain: string
  curatedId: string
  curatedName: string
  curatedStatus: string
}

const STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={13} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={13} className="text-red-400" />
  return <Clock size={13} className="text-yellow-400" />
}

const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审核',
  approved:       '已审核',
  rejected:       '已拒绝',
}

const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  approved:       'bg-green-50 text-green-700 border-green-200',
  rejected:       'bg-red-50 text-red-600 border-red-200',
}

export default function StructuredDataPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [curated, setCurated] = useState<CuratedDataset[]>([])
  const [loading, setLoading] = useState(true)
  const [pipelineFilter, setPipelineFilter] = useState('')
  const [curatedFilter, setCuratedFilter] = useState('')

  // Panel state
  const [panelRow, setPanelRow] = useState<Row | null>(null)

  // Quick approve
  const [approvingId, setApprovingId] = useState<string | null>(null)

  // Quick delete
  const [deleteRow, setDeleteRow] = useState<Row | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = () => {
    setLoading(true)
    Promise.all([
      pipelinesApi.list(),
      curatedApi.list() as Promise<CuratedDataset[]>,
    ]).then(([pls, cur]) => {
      setPipelines(Array.isArray(pls) ? pls : [])
      setCurated(Array.isArray(cur) ? cur : [])
    }).catch(() => {}).finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  // Join pipelines with their curated datasets
  const allRows = useMemo<Row[]>(() => {
    const curatedById = new Map(curated.map(c => [c.id, c]))
    const rows: Row[] = []
    pipelines.forEach(pl => {
      const ids: string[] = pl.target_curated_ids ?? []
      if (ids.length > 0) {
        ids.forEach(cid => {
          const c = curatedById.get(cid)
          if (c) rows.push({
            pipelineId: pl.id, pipelineName: pl.name, domain: pl.domain || '通用',
            curatedId: c.id, curatedName: c.name, curatedStatus: c.status || 'pending_review',
          })
        })
      } else {
        const matched = curated.filter(c => c.name.startsWith(pl.name))
        if (matched.length > 0) {
          matched.forEach(c => rows.push({
            pipelineId: pl.id, pipelineName: pl.name, domain: pl.domain || '通用',
            curatedId: c.id, curatedName: c.name, curatedStatus: c.status || 'pending_review',
          }))
        } else {
          rows.push({
            pipelineId: pl.id, pipelineName: pl.name, domain: pl.domain || '通用',
            curatedId: '', curatedName: '—', curatedStatus: '',
          })
        }
      }
    })
    return rows
  }, [pipelines, curated])

  const filtered = useMemo(() => {
    const pq = pipelineFilter.toLowerCase()
    const cq = curatedFilter.toLowerCase()
    return allRows.filter(r => {
      if (pq && !r.pipelineName.toLowerCase().includes(pq) && !r.pipelineId.toLowerCase().includes(pq)) return false
      if (cq && !r.curatedName.toLowerCase().includes(cq) && !r.curatedId.toLowerCase().includes(cq)) return false
      return true
    })
  }, [allRows, pipelineFilter, curatedFilter])

  const handleStatusChange = (id: string, newStatus: string) => {
    setCurated(prev => prev.map(c => c.id === id ? { ...c, status: newStatus } : c))
    if (panelRow?.curatedId === id) setPanelRow(r => r ? { ...r, curatedStatus: newStatus } : r)
  }

  const handleDeleted = (id: string) => {
    setCurated(prev => prev.filter(c => c.id !== id))
    setPanelRow(null)
    setDeleteRow(null)
  }

  const handleQuickApprove = async (e: React.MouseEvent, row: Row) => {
    e.stopPropagation()
    if (!row.curatedId) return
    setApprovingId(row.curatedId)
    try {
      await curatedApi.approve(row.curatedId)
      handleStatusChange(row.curatedId, 'approved')
    } finally { setApprovingId(null) }
  }

  const handleQuickDelete = async () => {
    if (!deleteRow?.curatedId) return
    setDeleting(true)
    try {
      await curatedApi.delete(deleteRow.curatedId)
      handleDeleted(deleteRow.curatedId)
    } catch {
      setDeleting(false)
      setDeleteRow(null)
    }
  }

  if (loading) return <p className="text-gray-400 text-sm p-6">加载中...</p>

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold">{t('data.structured_title')}</h2>
        <p className="text-sm text-gray-400 mt-0.5">{t('data.structured_subtitle')}</p>
      </div>

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={pipelineFilter}
            onChange={e => setPipelineFilter(e.target.value)}
            placeholder={t('data.filter_pipeline')}
            className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-60"
          />
          {pipelineFilter && (
            <button onClick={() => setPipelineFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
              <X size={12} />
            </button>
          )}
        </div>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            value={curatedFilter}
            onChange={e => setCuratedFilter(e.target.value)}
            placeholder={t('data.filter_curated')}
            className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-60"
          />
          {curatedFilter && (
            <button onClick={() => setCuratedFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
              <X size={12} />
            </button>
          )}
        </div>
        <span className="text-xs text-gray-400 self-center">共 {filtered.length} 条</span>
      </div>

      {/* Table */}
      {allRows.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-12 text-center text-gray-400">
          <p className="text-sm font-medium">{t('data.empty')}</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="border rounded-xl p-8 text-center text-gray-400 text-sm">{t('data.no_match')}</div>
      ) : (
        <div className="border rounded-xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">{t('data.col_pipeline_id')}</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">{t('data.col_pipeline_name')}</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">{t('data.col_domain')}</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">{t('data.col_curated_name')}</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-600 text-xs">{t('data.col_curated_status')}</th>
                <th className="px-4 py-2.5 text-gray-600 text-xs text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {filtered.map((row, idx) => (
                <tr
                  key={`${row.pipelineId}-${row.curatedId}-${idx}`}
                  onClick={() => row.curatedId && setPanelRow(row)}
                  className={`transition-colors ${row.curatedId ? 'cursor-pointer hover:bg-gray-50' : 'opacity-60'}`}
                >
                  <td className="px-4 py-3 font-mono text-xs text-gray-400" title={row.pipelineId}>
                    {row.pipelineId.slice(0, 8)}
                  </td>
                  <td className="px-4 py-3 font-medium text-gray-800 max-w-[160px] truncate">
                    {row.pipelineName}
                  </td>
                  <td className="px-4 py-3 text-xs text-gray-500">{row.domain}</td>
                  <td className="px-4 py-3 text-xs text-gray-700 max-w-[200px] truncate" title={row.curatedName}>
                    {row.curatedName}
                  </td>
                  <td className="px-4 py-3">
                    {row.curatedStatus ? (
                      <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[row.curatedStatus] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                        {STATUS_ICON(row.curatedStatus)}
                        {STATUS_LABEL[row.curatedStatus] || row.curatedStatus}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-300">—</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-right" onClick={e => e.stopPropagation()}>
                    <div className="flex items-center gap-1 justify-end">
                      {/* 快捷批准 */}
                      {row.curatedId && row.curatedStatus !== 'approved' && (
                        <button
                          onClick={e => handleQuickApprove(e, row)}
                          disabled={approvingId === row.curatedId}
                          className="p-1.5 rounded hover:bg-green-50 text-gray-400 hover:text-green-600 disabled:opacity-50"
                          title="批准"
                        >
                          {approvingId === row.curatedId
                            ? <Loader2 size={13} className="animate-spin" />
                            : <CheckCircle size={13} />}
                        </button>
                      )}

                      {/* 打开 Pipeline */}
                      <button
                        onClick={() => navigate(`/data/pipelines/${row.pipelineId}`)}
                        className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-black"
                        title="打开管道"
                      >
                        <ExternalLink size={13} />
                      </button>

                      {/* 快捷删除 */}
                      {row.curatedId && (
                        <button
                          onClick={() => setDeleteRow(row)}
                          className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                          title="删除"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Detail panel */}
      {panelRow && (
        <CuratedDetailPanel
          datasetId={panelRow.curatedId}
          datasetName={panelRow.curatedName}
          datasetStatus={panelRow.curatedStatus}
          pipelineName={panelRow.pipelineName}
          onClose={() => setPanelRow(null)}
          onStatusChange={handleStatusChange}
          onDeleted={handleDeleted}
        />
      )}

      {/* Quick delete confirm */}
      <ConfirmDialog
        open={!!deleteRow}
        title="删除数据集"
        message={`确认删除「${deleteRow?.curatedName}」？此操作不可撤销。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={handleQuickDelete}
        onCancel={() => setDeleteRow(null)}
      />
    </div>
  )
}
