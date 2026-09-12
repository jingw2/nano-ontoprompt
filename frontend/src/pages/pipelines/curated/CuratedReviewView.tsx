/**
 * 数据集详细审核视图 — 行级编辑 + 批量审批
 */
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Save, CheckCircle, XCircle } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

interface RowEdit {
  row_pk: string
  field_name: string
  old_value: string
  new_value: string
}

interface Props {
  datasetId: string
  reviewId: string
  data: Record<string, string>[]
  onComplete: (status: 'approved' | 'rejected') => void
}

export default function CuratedReviewView({ reviewId, data, onComplete }: Props) {
  const { t } = useTranslation()
  const [rows, setRows] = useState<Array<Record<string, string | number>>>(data.map((r, i) => ({ ...r, __idx__: i })))
  const [pendingEdits, setPendingEdits] = useState<RowEdit[]>([])
  const [saving, setSaving] = useState(false)
  const [notes, setNotes] = useState('')

  const columns = data.length > 0 ? Object.keys(data[0]) : []

  const handleCellChange = (rowIdx: number, col: string, newVal: string) => {
    const row = rows[rowIdx]
    const rowPk = String(row.id ?? row.__idx__)
    const oldVal = String(row[col] ?? '')

    setRows(prev => prev.map((r, i) => i === rowIdx ? { ...r, [col]: newVal } : r))
    setPendingEdits(prev => {
      const filtered = prev.filter(e => !(e.row_pk === rowPk && e.field_name === col))
      if (newVal !== oldVal) {
        return [...filtered, { row_pk: rowPk, field_name: col, old_value: oldVal, new_value: newVal }]
      }
      return filtered
    })
  }

  const saveEdits = async () => {
    if (pendingEdits.length === 0) return
    setSaving(true)
    try {
      await apiClientV2.post(`/curated/reviews/${reviewId}/edits`, { edits: pendingEdits })
      setPendingEdits([])
    } finally {
      setSaving(false)
    }
  }

  const handleDecision = async (action: 'approve' | 'reject') => {
    if (pendingEdits.length > 0) await saveEdits()
    await apiClientV2.post(`/curated/reviews/${reviewId}/${action}?notes=${encodeURIComponent(notes)}`)
    onComplete(action === 'approve' ? 'approved' : 'rejected')
  }

  return (
    <div className="space-y-4">
      {/* 操作栏 */}
      <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
        <input
          value={notes}
          onChange={e => setNotes(e.target.value)}
          placeholder={t('curatedReview.notes_ph')}
          className="flex-1 border rounded px-3 py-1.5 text-sm"
        />
        {pendingEdits.length > 0 && (
          <button
            onClick={saveEdits}
            disabled={saving}
            className="flex items-center gap-1 px-3 py-1.5 text-sm border rounded hover:bg-gray-100 disabled:opacity-50"
          >
            <Save size={14} /> {t('curatedReview.save_edits', { count: pendingEdits.length })}
          </button>
        )}
        <button
          onClick={() => handleDecision('approve')}
          className="flex items-center gap-1 px-3 py-1.5 text-sm bg-green-600 text-white rounded hover:bg-green-700"
        >
          <CheckCircle size={14} /> {t('curatedReview.approve_all')}
        </button>
        <button
          onClick={() => handleDecision('reject')}
          className="flex items-center gap-1 px-3 py-1.5 text-sm bg-red-500 text-white rounded hover:bg-red-600"
        >
          <XCircle size={14} /> {t('curatedReview.reject')}
        </button>
      </div>

      {/* 可编辑数据表格 */}
      <div className="border rounded-lg overflow-auto max-h-96">
        <table className="w-full text-sm">
          <thead className="bg-gray-50 sticky top-0">
            <tr>
              {columns.map(col => (
                <th key={col} className="px-3 py-2 text-left text-xs text-gray-600 font-medium border-b">
                  {col}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map((row, rowIdx) => (
              <tr key={rowIdx} className="hover:bg-gray-50">
                {columns.map(col => {
                  const rowPk = String(row.id ?? rowIdx)
                  const isEdited = pendingEdits.some(e => e.row_pk === rowPk && e.field_name === col)
                  return (
                    <td key={col} className={`px-1 py-0.5 ${isEdited ? 'bg-yellow-50' : ''}`}>
                      <input
                        value={String(row[col] ?? '')}
                        onChange={e => handleCellChange(rowIdx, col, e.target.value)}
                        className={`w-full px-2 py-1 text-xs rounded border-0 focus:outline-none focus:ring-1 focus:ring-blue-300
                          ${isEdited ? 'bg-yellow-50 font-medium' : 'bg-transparent'}`}
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pendingEdits.length > 0 && (
        <p className="text-xs text-yellow-600">{t('curatedReview.unsaved_edits', { count: pendingEdits.length })}</p>
      )}
    </div>
  )
}
