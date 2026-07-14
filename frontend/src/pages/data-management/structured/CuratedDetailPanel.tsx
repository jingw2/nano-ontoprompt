import { useEffect, useRef, useState } from 'react'
import {
  X, CheckCircle, AlertTriangle, Clock,
  Save, Trash2, Loader2, Pencil,
} from 'lucide-react'
import curatedApi from '@/api/v2/curated'
import ConfirmDialog from '@/components/ConfirmDialog'

interface Props {
  datasetId: string
  datasetName: string
  datasetStatus: string
  pipelineName: string
  onClose: () => void
  onStatusChange: (id: string, status: string) => void
  onDeleted: (id: string) => void
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

const STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={13} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={13} className="text-red-400" />
  return <Clock size={13} className="text-yellow-400" />
}

type CellKey = `${number}::${string}`

export default function CuratedDetailPanel({
  datasetId, datasetName, datasetStatus, pipelineName,
  onClose, onStatusChange, onDeleted,
}: Props) {
  const [rows, setRows] = useState<Record<string, string>[]>([])
  const [cols, setCols] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [status, setStatus] = useState(datasetStatus)

  const [editingCell, setEditingCell] = useState<CellKey | null>(null)
  const [pendingEdits, setPendingEdits] = useState<Map<CellKey, { old: string; val: string }>>(new Map())
  const [saving, setSaving] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const editInputRef = useRef<HTMLInputElement>(null)

  const [approving, setApproving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)

  useEffect(() => {
    setLoading(true)
    setLoadError('')
    curatedApi.preview(datasetId, 500)
      .then(res => {
        const data = (res as any).rows ?? res ?? []
        const rowArr = Array.isArray(data) ? data : []
        setRows(rowArr)
        setCols(rowArr.length > 0 ? Object.keys(rowArr[0]) : [])
      })
      .catch(() => setLoadError('数据加载失败'))
      .finally(() => setLoading(false))
  }, [datasetId])

  useEffect(() => {
    if (editingCell) editInputRef.current?.focus()
  }, [editingCell])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape' && !editingCell) onClose() }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose, editingCell])

  const cellVal = (rowIdx: number, col: string) => {
    const key: CellKey = `${rowIdx}::${col}`
    return pendingEdits.get(key)?.val ?? String(rows[rowIdx]?.[col] ?? '')
  }

  const startEdit = (rowIdx: number, col: string) => {
    setEditingCell(`${rowIdx}::${col}`)
    setSaveMsg('')
  }

  const commitEdit = (rowIdx: number, col: string, newVal: string) => {
    const key: CellKey = `${rowIdx}::${col}`
    const oldVal = String(rows[rowIdx]?.[col] ?? '')
    if (newVal === oldVal) {
      setPendingEdits(prev => { const m = new Map(prev); m.delete(key); return m })
    } else {
      setPendingEdits(prev => new Map(prev).set(key, { old: oldVal, val: newVal }))
    }
    setEditingCell(null)
  }

  const handleSaveEdits = async () => {
    if (pendingEdits.size === 0) return
    setSaving(true)
    setSaveMsg('')
    try {
      const session = await curatedApi.startReview(datasetId)
      const reviewId = (session as any).review_id ?? (session as any).data?.review_id
      const edits = Array.from(pendingEdits.entries()).map(([key, { old: oldVal, val: newVal }]) => {
        const [rowIdxStr, col] = key.split('::')
        const rowIdx = Number(rowIdxStr)
        const pkCol = cols[0] ?? 'row'
        return { row_pk: String(rows[rowIdx]?.[pkCol] ?? rowIdx), field_name: col, old_value: oldVal, new_value: newVal }
      })
      await curatedApi.saveEdits(reviewId, edits)
      setRows(prev => {
        const updated = prev.map(r => ({ ...r }))
        pendingEdits.forEach(({ val }, key) => {
          const [rowIdxStr, col] = key.split('::')
          updated[Number(rowIdxStr)][col] = val
        })
        return updated
      })
      setPendingEdits(new Map())
      setSaveMsg(`已保存 ${edits.length} 处修改`)
    } catch {
      setSaveMsg('保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    try {
      await curatedApi.approve(datasetId)
      setStatus('approved')
      onStatusChange(datasetId, 'approved')
    } finally { setApproving(false) }
  }

  const handleReject = async () => {
    setApproving(true)
    try {
      await curatedApi.reject(datasetId)
      setStatus('rejected')
      onStatusChange(datasetId, 'rejected')
    } finally { setApproving(false) }
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await curatedApi.delete(datasetId)
      onDeleted(datasetId)
    } catch {
      setDeleting(false)
      setShowDeleteConfirm(false)
    }
  }

  const hasPending = pendingEdits.size > 0

  return (
    <>
      {/* Full-screen modal overlay */}
      <div
        className="fixed inset-0 bg-black/50 z-40 flex items-center justify-center p-6"
        onClick={onClose}
      >
        {/* Modal window */}
        <div
          className="bg-white rounded-xl shadow-2xl z-50 flex flex-col w-full max-w-5xl"
          style={{ maxHeight: 'calc(100vh - 3rem)' }}
          onClick={e => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-start justify-between px-6 py-4 border-b shrink-0">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="font-semibold text-base truncate">{datasetName}</h2>
                <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[status] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                  {STATUS_ICON(status)}
                  {STATUS_LABEL[status] || status}
                </span>
              </div>
              <p className="text-xs text-gray-400 mt-0.5">来自管道：{pipelineName}</p>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-black ml-4 shrink-0"
            >
              <X size={16} />
            </button>
          </div>

          {/* Action bar */}
          <div className="flex items-center gap-2 px-6 py-3 border-b bg-gray-50 shrink-0 flex-wrap">
            {status !== 'approved' && (
              <button
                onClick={handleApprove}
                disabled={approving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
              >
                {approving ? <Loader2 size={12} className="animate-spin" /> : <CheckCircle size={12} />}
                批准
              </button>
            )}
            {status !== 'rejected' && (
              <button
                onClick={handleReject}
                disabled={approving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 text-red-600 rounded-lg hover:bg-red-50 disabled:opacity-50"
              >
                {approving ? <Loader2 size={12} className="animate-spin" /> : <AlertTriangle size={12} />}
                拒绝
              </button>
            )}

            <div className="flex-1" />

            {hasPending && (
              <span className="text-xs text-amber-600 flex items-center gap-1">
                <Pencil size={11} /> {pendingEdits.size} 处未保存
              </span>
            )}
            {saveMsg && (
              <span className={`text-xs ${saveMsg.includes('失败') ? 'text-red-500' : 'text-green-600'}`}>
                {saveMsg}
              </span>
            )}
            <button
              onClick={handleSaveEdits}
              disabled={!hasPending || saving}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border rounded-lg hover:bg-gray-100 disabled:opacity-40"
            >
              {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
              保存编辑
            </button>
            <button
              onClick={() => setShowDeleteConfirm(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs border border-red-200 text-red-500 rounded-lg hover:bg-red-50"
            >
              <Trash2 size={12} /> 删除
            </button>
          </div>

          {/* Data table */}
          <div className="flex-1 overflow-auto">
            {loading ? (
              <div className="flex items-center justify-center h-48 text-gray-400 text-sm gap-2">
                <Loader2 size={16} className="animate-spin" /> 加载中...
              </div>
            ) : loadError ? (
              <div className="p-6 text-sm text-red-400">{loadError}</div>
            ) : rows.length === 0 ? (
              <div className="p-8 text-center text-sm text-gray-400">暂无数据行</div>
            ) : (
              <>
                <p className="px-6 py-2 text-xs text-gray-400 bg-gray-50 border-b shrink-0">
                  共 {rows.length} 行 · 双击单元格可编辑
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs min-w-max">
                    <thead className="bg-gray-50 border-b sticky top-0">
                      <tr>
                        <th className="px-4 py-2 text-gray-400 font-normal text-left w-12">#</th>
                        {cols.map(col => (
                          <th key={col} className="px-4 py-2 text-left font-medium text-gray-600 whitespace-nowrap">
                            {col}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row, rowIdx) => (
                        <tr key={rowIdx} className="border-b hover:bg-blue-50/30 transition-colors">
                          <td className="px-4 py-2 text-gray-300 tabular-nums select-none">{rowIdx + 1}</td>
                          {cols.map(col => {
                            const key: CellKey = `${rowIdx}::${col}`
                            const isEditing = editingCell === key
                            const isModified = pendingEdits.has(key)
                            const val = cellVal(rowIdx, col)

                            return (
                              <td
                                key={col}
                                className={`px-4 py-2 max-w-[220px] ${isModified ? 'bg-amber-50' : ''}`}
                                onDoubleClick={() => startEdit(rowIdx, col)}
                              >
                                {isEditing ? (
                                  <input
                                    ref={editInputRef}
                                    defaultValue={val}
                                    onBlur={e => commitEdit(rowIdx, col, e.target.value)}
                                    onKeyDown={e => {
                                      if (e.key === 'Enter') commitEdit(rowIdx, col, e.currentTarget.value)
                                      if (e.key === 'Escape') setEditingCell(null)
                                    }}
                                    className="w-full border border-blue-400 rounded px-1.5 py-0.5 outline-none bg-white text-xs min-w-[80px]"
                                    onClick={e => e.stopPropagation()}
                                  />
                                ) : (
                                  <span
                                    className={`block truncate cursor-default ${isModified ? 'text-amber-700 font-medium' : 'text-gray-700'}`}
                                    title={val}
                                  >
                                    {val || <span className="text-gray-300">—</span>}
                                  </span>
                                )}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <ConfirmDialog
        open={showDeleteConfirm}
        title="删除数据集"
        message={`确认删除「${datasetName}」？此操作不可撤销，数据将永久删除。`}
        confirmLabel={deleting ? '删除中...' : '确认删除'}
        onConfirm={handleDelete}
        onCancel={() => setShowDeleteConfirm(false)}
      />
    </>
  )
}
