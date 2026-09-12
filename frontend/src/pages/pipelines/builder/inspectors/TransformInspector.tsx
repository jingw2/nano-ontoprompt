import { useState, useMemo, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Eye } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

const AVAILABLE_OPS = [
  { op: 'rename_columns', labelKey: 'transformInspector.op_rename_columns', path: 'A', enabled: true }, { op: 'drop_nulls', labelKey: 'transformInspector.op_drop_nulls', path: 'A', enabled: true },
  { op: 'fill_nulls', labelKey: 'transformInspector.op_fill_nulls', path: 'A', enabled: true }, { op: 'drop_duplicates', labelKey: 'transformInspector.op_drop_duplicates', path: 'A', enabled: true },
  { op: 'normalize_dates', labelKey: 'transformInspector.op_normalize_dates', path: 'A', enabled: true }, { op: 'select_columns', labelKey: 'transformInspector.op_select_columns', path: 'A', enabled: true },
  { op: 'filter_rows', labelKey: 'transformInspector.op_filter_rows', path: 'A', enabled: true }, { op: 'sort', labelKey: 'transformInspector.op_sort', path: 'A', enabled: true },
  { op: 'join', labelKey: 'transformInspector.op_join', path: 'A', enabled: false }, { op: 'aggregate', labelKey: 'transformInspector.op_aggregate', path: 'A', enabled: false },
  { op: 'group_by', labelKey: 'transformInspector.op_group_by', path: 'A', enabled: false }, { op: 'pivot', labelKey: 'transformInspector.op_pivot', path: 'A', enabled: false },
  { op: 'detect_wide_table', labelKey: 'transformInspector.op_detect_wide_table', path: 'WIDE', enabled: true }, { op: 'suggest_split', labelKey: 'transformInspector.op_suggest_split', path: 'WIDE', enabled: true },
  { op: 'apply_split', labelKey: 'transformInspector.op_apply_split', path: 'WIDE', enabled: true },
  { op: 'parse_json', labelKey: 'transformInspector.op_parse_json', path: 'B', enabled: true }, { op: 'parse_xml', labelKey: 'transformInspector.op_parse_xml', path: 'B', enabled: true },
  { op: 'flatten_json', labelKey: 'transformInspector.op_flatten_json', path: 'B', enabled: true }, { op: 'explode_array', labelKey: 'transformInspector.op_explode_array', path: 'B', enabled: true },
  { op: 'document_to_markdown', labelKey: 'transformInspector.op_document_to_markdown', path: 'C', enabled: true }, { op: 'ocr_extract', labelKey: 'transformInspector.op_ocr_extract', path: 'C', enabled: true },
  { op: 'vlm_extract', labelKey: 'transformInspector.op_vlm_extract', path: 'C', enabled: true }, { op: 'llm_structurize', labelKey: 'transformInspector.op_llm_structurize', path: 'C', enabled: true },
]

const PATH_OPS_MAP: Record<string, typeof AVAILABLE_OPS> = {
  auto: AVAILABLE_OPS, structured: AVAILABLE_OPS.filter(o => o.path === 'A'), semi_structured: AVAILABLE_OPS.filter(o => o.path === 'B'),
  unstructured: AVAILABLE_OPS.filter(o => o.path === 'C'), wide_table: AVAILABLE_OPS.filter(o => o.path === 'WIDE'),
}

export default function TransformInspector({ config, onChange, readOnly = false, pipelineId }: { config: Record<string, unknown>; onChange: (key: string, value: unknown) => void; nodeId?: string; readOnly?: boolean; pipelineId?: string }) {
  const { t } = useTranslation()
  const currentPath = String(config.path || 'auto')
  const steps = (config.steps || []) as Array<{ op: string; params?: Record<string, unknown> }>
  const [showCatalog, setShowCatalog] = useState(false)
  const [, setPreviewMap] = useState<Record<number, { loading: boolean; data?: unknown[]; error?: string }>>({})
  const filteredOps = useMemo(() => PATH_OPS_MAP[currentPath] || AVAILABLE_OPS, [currentPath])

  // Runtime stats (hooks must be at top level)
  const [runStats, setRunStats] = useState<{ rows_in: number; rows_out: number } | null>(null);
  useEffect(() => {
    if (!pipelineId || !readOnly) return;
    apiClientV2.get<{ id: string }[]>('/pipelines/' + pipelineId + '/runs').then(runs => {
      const last = Array.isArray(runs) && runs.length > 0 ? runs[runs.length - 1] : null;
      if (last) apiClientV2.get<{ stats?: { rows_in?: number; rows_out?: number } }>('/pipelines/runs/' + last.id).then(d => {
        if (d?.stats) setRunStats({ rows_in: d.stats.rows_in || 0, rows_out: d.stats.rows_out || 0 });
      }).catch(() => {});
    }).catch(() => {});
  }, [pipelineId, readOnly]);

  if (readOnly) {
    return (
      <div className="space-y-3">
        <div className="bg-amber-50 border border-amber-100 rounded-lg p-3 text-xs">
          <p className="text-amber-700 font-medium mb-1">⚙️ {t('transformInspector.config_title')}</p>
          <p className="text-amber-600">{t('transformInspector.path_label')}: {currentPath === 'auto' ? t('transformInspector.path_auto') : currentPath === 'structured' ? t('transformInspector.path_structured') : currentPath === 'semi_structured' ? t('transformInspector.path_semi_structured') : currentPath === 'unstructured' ? t('transformInspector.path_unstructured') : t('transformInspector.path_wide_table')}</p>
          <p className="text-amber-600">{t('transformInspector.engine_label')}: {String(config.engine || 'pandas')}</p>
          <p className="text-amber-600">{t('transformInspector.steps_label', { count: steps.length })}</p>
          {steps.length > 0 && (<div className="mt-1 space-y-0.5">{steps.map((s, i: number) => (<p key={i} className="text-amber-500">{i + 1}. {t(AVAILABLE_OPS.find(o => o.op === s.op)?.labelKey || '', s.op)}</p>))}</div>)}
        </div>
        {runStats && (
          <div className="bg-white border rounded-lg p-3 text-xs">
            <p className="font-medium text-gray-700 mb-2">{t('transformInspector.runtime_result_title')}</p>
            <p className="text-gray-500">{t('transformInspector.rows_in_label', { count: runStats.rows_in })}</p>
            <p className="text-gray-500">{t('transformInspector.rows_out_label', { count: runStats.rows_out })}</p>
            <p className="text-green-600 mt-1">{t('transformInspector.transform_success')}</p>
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      <div><label className="text-xs text-gray-500 mb-1 block">{t('transformInspector.process_path_label')}</label>
        <select value={currentPath} onChange={e => { const np = e.target.value; onChange('path', np); const v = PATH_OPS_MAP[np] || AVAILABLE_OPS; const vs = new Set(v.map(o => o.op)); onChange('steps', steps.filter(s => vs.has(s.op))) }} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="auto">{t('transformInspector.path_auto')}</option><option value="structured">{t('transformInspector.path_structured')}</option><option value="semi_structured">{t('transformInspector.path_semi_structured')}</option><option value="unstructured">{t('transformInspector.path_unstructured')}</option><option value="wide_table">{t('transformInspector.path_wide_table')}</option></select></div>
      <div><label className="text-xs text-gray-500 mb-1 block">{t('transformInspector.engine_field_label')}</label>
        <select value={String(config.engine || 'pandas')} onChange={e => onChange('engine', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm">
          <option value="pandas">pandas</option>{currentPath === 'A' && <option value="duckdb">DuckDB</option>}{(currentPath === 'C' || currentPath === 'auto') && (<><option value="llm">LLM</option><option value="vlm">VLM</option><option value="ocr">OCR</option></>)}</select></div>
      <div><div className="flex items-center justify-between mb-1"><label className="text-xs text-gray-500">{t('transformInspector.process_steps_label')}</label><button onClick={() => setShowCatalog(!showCatalog)} className="flex items-center gap-0.5 text-xs text-blue-500"><Plus size={11} />{t('transformInspector.add_button')}</button></div>
        {showCatalog && (<div className="border rounded-lg p-2 mb-2 max-h-40 overflow-y-auto space-y-0.5">{filteredOps.map(op => op.enabled ? (<button key={op.op} onClick={() => { onChange('steps', [...steps, { op: op.op, params: {} }]); setShowCatalog(false) }} className="w-full text-left text-xs px-2 py-1 rounded hover:bg-gray-50 flex items-center gap-2"><span className="text-gray-300 text-[10px]">{op.path}</span><span className="font-medium">{t(op.labelKey)}</span></button>) : (<div key={op.op} className="w-full text-left text-xs px-2 py-1 rounded flex items-center gap-2 opacity-50" title={t('transformInspector.coming_soon')}><span className="text-gray-300 text-[10px]">{op.path}</span><span className="text-gray-400">{t(op.labelKey)}</span><span className="ml-auto text-[9px] text-gray-400 border-dashed border rounded px-1">{t('transformInspector.coming_soon')}</span></div>))}</div>)}
        {steps.length === 0 ? <p className="text-xs text-gray-400 italic">{t('transformInspector.no_steps')}</p> : (<div className="space-y-1.5">{steps.map((step, i: number) => (
          <div key={i} className="border rounded-lg p-2 text-xs space-y-1">
            <div className="flex items-center justify-between"><span className="font-medium">{i + 1}. {t(AVAILABLE_OPS.find(o => o.op === step.op)?.labelKey || '', step.op)}</span><div className="flex gap-0.5"><button onClick={async () => { setPreviewMap(p => ({ ...p, [i]: { loading: true } })); try { const r = await apiClientV2.post<{ preview?: unknown[]; error?: string }>('/pipelines/preview-step', { op: step.op, params: step.params || {}, sample_data: [{ col: 's1' }, { col: 's2' }] }); setPreviewMap(p => ({ ...p, [i]: { loading: false, data: r.preview || [], error: r.error } })) } catch { setPreviewMap(p => ({ ...p, [i]: { loading: false, error: t('transformInspector.preview_failed') } })) } }} className="text-gray-400 hover:text-blue-500"><Eye size={11} /></button><button onClick={() => onChange('steps', steps.filter((_, j: number) => j !== i))} className="text-gray-400 hover:text-red-500"><Trash2 size={11} /></button></div></div>
          </div>
        ))}</div>)}
      </div>
    </>
  )
}
