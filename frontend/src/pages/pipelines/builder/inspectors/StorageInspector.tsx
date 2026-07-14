import { useState, useEffect } from 'react'
import { HardDrive, Loader2 } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

export default function StorageInspector({ config, onChange, readOnly = false, pipelineId }: { config: Record<string, unknown>; onChange: (key: string, value: unknown) => void; readOnly?: boolean; pipelineId?: string }) {
  const schemaOn = config.schema_inference !== false
  const [runtimeData, setRuntimeData] = useState<{ columns: string[]; rows_in: number; sample: any } | null>(null)

  // Load runtime data when in read-only mode and pipeline has been run
  useEffect(() => {
    if (!readOnly || !pipelineId) return
    apiClientV2.get(`/pipelines/${pipelineId}/runs`).then((runs: any) => {
      const lastRun = Array.isArray(runs) && runs.length > 0 ? runs[runs.length - 1] : null
      if (!lastRun) return
      apiClientV2.get(`/pipelines/runs/${lastRun.id}`).then((detail: any) => {
        if (detail?.stats) {
          const meta = detail.stats.meta || {}
          const schema = meta.inferred_schema || {}
          const cols = Object.keys(schema)
          if (cols.length > 0) {
            setRuntimeData({ columns: cols, rows_in: detail.stats.rows_in, sample: schema })
          }
        }
      }).catch(() => {})
    }).catch(() => {})
  }, [pipelineId, readOnly])

  if (readOnly) {
    return (
      <div className="space-y-3">
        <div className="bg-emerald-50 border border-emerald-100 rounded-lg p-3 text-xs">
          <p className="text-emerald-700 font-medium mb-1">📦 存储配置</p>
          <p className="text-emerald-600">模式: {String(config.storage_mode || 'auto') === 'auto' ? '自动检测' : String(config.storage_mode)}</p>
          <p className="text-emerald-600">版本: {String(config.versioning || 'snapshot')}</p>
          <p className="text-emerald-600">Schema推断: {schemaOn ? '✅ 启用' : '关闭'}</p>
        </div>
        {runtimeData && (
          <div className="bg-white border rounded-lg p-3 text-xs">
            <p className="font-medium text-gray-700 mb-2">运行时检测结果</p>
            <p className="text-gray-500 mb-1">行数: {runtimeData.rows_in} · 列数: {runtimeData.columns.length}</p>
            <div className="space-y-0.5 max-h-40 overflow-y-auto">
              {runtimeData.columns.map((col: string, i: number) => (
                <div key={i} className="flex justify-between text-gray-600">
                  <span>{col}</span>
                  <span className="text-gray-400">{String((runtimeData.sample as any)[col] || '?')}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }
  return (
    <>
      <div><label className="text-xs text-gray-500 mb-1 block">存储模式</label><select value={String(config.storage_mode || 'auto')} onChange={e => onChange('storage_mode', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="auto">自动检测</option><option value="raw_dataset">Raw Dataset</option><option value="media_set">Media Set</option></select></div>
      <div><label className="text-xs text-gray-500 mb-1 block">版本化</label><select value={String(config.versioning || 'snapshot')} onChange={e => onChange('versioning', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="snapshot">SNAPSHOT</option><option value="append">APPEND</option></select></div>
      <div><label className="text-xs text-gray-500 mb-1 block">Schema 推断</label><label className="flex items-center gap-2 text-sm cursor-pointer"><input type="checkbox" checked={schemaOn} onChange={e => onChange('schema_inference', e.target.checked)} className="accent-black" /><span className="text-xs">自动推断 Schema</span></label></div>
      {schemaOn && (<div className="pl-3 border-l-2 border-gray-100 space-y-3">
        <div><label className="text-xs text-gray-500 mb-1 block">采样行数</label><input type="number" value={String(config.sample_size || 10000)} onChange={e => onChange('sample_size', parseInt(e.target.value) || 10000)} className="w-full border rounded-lg px-3 py-1.5 text-sm" /></div>
        <div><label className="text-xs text-gray-500 mb-1 block">列类型检测</label><select value={String(config.type_detection || 'auto')} onChange={e => onChange('type_detection', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="auto">自动</option><option value="strict">严格</option><option value="text_only">全文本</option></select></div>
      </div>)}
    </>
  )
}
