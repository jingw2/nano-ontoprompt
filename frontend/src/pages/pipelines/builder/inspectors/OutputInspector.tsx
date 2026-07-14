import { useState, useEffect } from 'react'
import { Database, Loader2, X, Table2 } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

export default function OutputInspector({
  config, onChange, readOnly = false,
}: {
  config: Record<string, unknown>
  onChange: (key: string, value: unknown) => void
  readOnly?: boolean
}) {
  const curatedIds = ((config as any).curated_dataset_ids as string[] | undefined) || ((config as any).curated_dataset_id ? [(config as any).curated_dataset_id] : [])
  const [previews, setPreviews] = useState<Record<string, any[]>>({})
  const [previewLoading, setPreviewLoading] = useState(false)
  const [modalData, setModalData] = useState<{ title: string; rows: any[] } | null>(null)
  const [datasetInfo, setDatasetInfo] = useState<Record<string, { name: string; rows: number; version_no: number }>>({})

  useEffect(() => {
    if (curatedIds.length === 0) return
    setPreviewLoading(true)
    Promise.all(curatedIds.map(async id => {
      const info: any = await apiClientV2.get(`/curated/${id}`).catch(() => null)
      const versions: any = await apiClientV2.get(`/datasets/${id}/versions`).catch(() => null)
      const versionNo = versions?.[0]?.version_no || 1
      const rows: any = versions?.length
        ? await apiClientV2.get(`/datasets/${id}/versions/${versionNo}/preview?limit=10000`).catch(() => [])
        : []
      return { id, info, versionNo, rows: Array.isArray(rows) ? rows : [] }
    })).then(results => {
      setDatasetInfo(Object.fromEntries(results.map(r => [r.id, { name: r.info?.name || r.id, rows: r.info?.row_count || r.rows.length, version_no: r.versionNo }])))
      setPreviews(Object.fromEntries(results.map(r => [r.id, r.rows])))
    }).finally(() => setPreviewLoading(false))
  }, [curatedIds.join('|')])

  if (!readOnly) {
    return (
      <>
        <div><label className="text-xs text-gray-500 mb-1 block">输出类型</label><select value={String(config.dataset_type || 'curated_dataset')} onChange={e => onChange('dataset_type', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="curated_dataset">Curated Dataset</option></select></div>
        <div><label className="text-xs text-gray-500 mb-1 block">主键字段</label><input value={String((config.primary_key as string[])?.join(', ') || '')} onChange={e => onChange('primary_key', e.target.value.split(',').map(s => s.trim()))} placeholder="例：order_id" className="w-full border rounded-lg px-3 py-1.5 text-sm" /></div>
        <div><label className="text-xs text-gray-500 mb-1 block">需要审核</label><label className="flex items-center gap-2 text-sm cursor-pointer"><input type="checkbox" checked={config.review_required !== false} onChange={e => onChange('review_required', e.target.checked)} className="accent-black" /><span className="text-xs">输出后需要人工审核</span></label></div>
      </>
    )
  }

  if (curatedIds.length === 0) {
    return <div className="text-center py-8 text-gray-400 text-xs">尚未运行，暂无数据</div>
  }

  return (
    <div className="space-y-3">
      <div className="bg-green-50 border border-green-200 rounded-lg p-3">
        <div className="flex items-center gap-1.5 text-xs text-green-700 font-medium mb-1"><Database size={12} />Curated Dataset(s)</div>
        <p className="text-xs text-green-600">{curatedIds.length} 张结构化输出表</p>
      </div>

      {/* Loading */}
      {previewLoading && (
        <div className="flex items-center gap-1 text-xs text-gray-400 py-4 justify-center"><Loader2 size={12} className="animate-spin" />加载中...</div>
      )}

      {/* Table Card */}
      {!previewLoading && curatedIds.map(id => {
        const rows = previews[id] || []
        const info = datasetInfo[id]
        return (
        <div key={id}>
          <p className="text-xs text-gray-500 mb-2 font-medium">结构化数据表</p>
          <button
            onClick={() => setModalData({ title: info?.name || 'Curated Dataset', rows })}
            className="w-full flex items-center gap-3 p-3 rounded-lg border border-gray-200 hover:border-gray-400 hover:bg-gray-50 transition-colors text-left"
          >
            <div className="w-8 h-8 bg-green-100 rounded-lg flex items-center justify-center shrink-0">
              <Table2 size={14} className="text-green-600" />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-800 truncate">{info?.name || '数据集'}</p>
              <p className="text-xs text-gray-400">{rows.length} 行 · {rows.length > 0 ? Object.keys(rows[0]).length : 0} 列 · v{info?.version_no || 1}</p>
            </div>
          </button>
        </div>
      )})}

      {/* Empty state */}
      {!previewLoading && curatedIds.length > 0 && Object.values(previews).every(rows => rows.length === 0) && (
        <div className="text-center py-4 text-gray-400 text-xs">暂无数据</div>
      )}

      {/* Data Detail Modal */}
      {modalData && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-[60]" onClick={() => setModalData(null)}>
          <div className="bg-white rounded-xl shadow-lg w-[90vw] max-w-4xl max-h-[85vh] flex flex-col" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b shrink-0">
              <div>
                <h3 className="font-semibold text-sm">{modalData.title}</h3>
                <p className="text-xs text-gray-400 mt-0.5">{modalData.rows.length} 行</p>
              </div>
              <button onClick={() => setModalData(null)} className="text-gray-400 hover:text-black"><X size={18} /></button>
            </div>
            <div className="overflow-auto flex-1">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 sticky top-0">
                  <tr>
                    <th className="px-4 py-2 text-left text-xs font-medium text-gray-500 w-12">#</th>
                    {modalData.rows.length > 0 && Object.keys(modalData.rows[0]).map(col => (
                      <th key={col} className="px-4 py-2 text-left text-xs font-medium text-gray-500">{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {modalData.rows.map((row, i) => (
                    <tr key={i} className="hover:bg-gray-50">
                      <td className="px-4 py-2 text-gray-400 text-xs">{i + 1}</td>
                      {Object.keys(modalData.rows[0]).map(col => (
                        <td key={col} className="px-4 py-2 text-xs text-gray-700 max-w-xs truncate">{String(row[col] ?? '')}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
