import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { apiClientV2 } from '@/api/client'

export default function StorageInspector({ config, onChange, readOnly = false, pipelineId }: { config: Record<string, unknown>; onChange: (key: string, value: unknown) => void; readOnly?: boolean; pipelineId?: string }) {
  const { t } = useTranslation()
  const schemaOn = config.schema_inference !== false
  const [runtimeData, setRuntimeData] = useState<{ columns: string[]; rows_in: number; sample: Record<string, unknown> } | null>(null)

  // Load runtime data when in read-only mode and pipeline has been run
  useEffect(() => {
    if (!readOnly || !pipelineId) return
    apiClientV2.get<{ id: string }[]>(`/pipelines/${pipelineId}/runs`).then(runs => {
      const lastRun = Array.isArray(runs) && runs.length > 0 ? runs[runs.length - 1] : null
      if (!lastRun) return
      apiClientV2.get<{
        stats?: {
          meta?: { inferred_schema?: Record<string, unknown> }
          rows_in: number
        }
      }>(`/pipelines/runs/${lastRun.id}`).then(detail => {
        if (detail?.stats) {
          const meta = detail.stats.meta || ({} as { inferred_schema?: Record<string, unknown> })
          const schema = meta.inferred_schema || ({} as Record<string, unknown>)
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
          <p className="text-emerald-700 font-medium mb-1">📦 {t('storageInspector.config_title')}</p>
          <p className="text-emerald-600">{t('storageInspector.mode_label')}: {String(config.storage_mode || 'auto') === 'auto' ? t('storageInspector.mode_auto') : String(config.storage_mode)}</p>
          <p className="text-emerald-600">{t('storageInspector.version_label')}: {String(config.versioning || 'snapshot')}</p>
          <p className="text-emerald-600">{t('storageInspector.schema_inference_label')}: {schemaOn ? t('storageInspector.schema_enabled') : t('storageInspector.schema_disabled')}</p>
        </div>
        {runtimeData && (
          <div className="bg-white border rounded-lg p-3 text-xs">
            <p className="font-medium text-gray-700 mb-2">{t('storageInspector.runtime_detection_title')}</p>
            <p className="text-gray-500 mb-1">{t('storageInspector.rows_cols_summary', { rows: runtimeData.rows_in, cols: runtimeData.columns.length })}</p>
            <div className="space-y-0.5 max-h-40 overflow-y-auto">
              {runtimeData.columns.map((col: string, i: number) => (
                <div key={i} className="flex justify-between text-gray-600">
                  <span>{col}</span>
                  <span className="text-gray-400">{String(runtimeData.sample[col] || '?')}</span>
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
      <div><label className="text-xs text-gray-500 mb-1 block">{t('storageInspector.storage_mode_label')}</label><select value={String(config.storage_mode || 'auto')} onChange={e => onChange('storage_mode', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="auto">{t('storageInspector.mode_auto')}</option><option value="raw_dataset">Raw Dataset</option><option value="media_set">Media Set</option></select></div>
      <div><label className="text-xs text-gray-500 mb-1 block">{t('storageInspector.versioning_label')}</label><select value={String(config.versioning || 'snapshot')} onChange={e => onChange('versioning', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="snapshot">SNAPSHOT</option><option value="append">APPEND</option></select></div>
      <div><label className="text-xs text-gray-500 mb-1 block">{t('storageInspector.schema_inference_full_label')}</label><label className="flex items-center gap-2 text-sm cursor-pointer"><input type="checkbox" checked={schemaOn} onChange={e => onChange('schema_inference', e.target.checked)} className="accent-black" /><span className="text-xs">{t('storageInspector.schema_inference_hint')}</span></label></div>
      {schemaOn && (<div className="pl-3 border-l-2 border-gray-100 space-y-3">
        <div><label className="text-xs text-gray-500 mb-1 block">{t('storageInspector.sample_size_label')}</label><input type="number" value={String(config.sample_size || 10000)} onChange={e => onChange('sample_size', parseInt(e.target.value) || 10000)} className="w-full border rounded-lg px-3 py-1.5 text-sm" /></div>
        <div><label className="text-xs text-gray-500 mb-1 block">{t('storageInspector.type_detection_label')}</label><select value={String(config.type_detection || 'auto')} onChange={e => onChange('type_detection', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="auto">{t('storageInspector.type_detection_auto')}</option><option value="strict">{t('storageInspector.type_detection_strict')}</option><option value="text_only">{t('storageInspector.type_detection_text_only')}</option></select></div>
      </div>)}
    </>
  )
}
