import { useState, useMemo, useEffect } from 'react'
import { Plus, Trash2, Eye, Loader2, AlertTriangle, ExternalLink } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

const AVAILABLE_OPS = [
  { op: 'rename_columns', label: '重命名列', path: 'A', enabled: true }, { op: 'drop_nulls', label: '删除空值行', path: 'A', enabled: true },
  { op: 'fill_nulls', label: '填充空值', path: 'A', enabled: true }, { op: 'drop_duplicates', label: '去重', path: 'A', enabled: true },
  { op: 'normalize_dates', label: '日期标准化', path: 'A', enabled: true }, { op: 'select_columns', label: '选择列', path: 'A', enabled: true },
  { op: 'filter_rows', label: '过滤行', path: 'A', enabled: true }, { op: 'sort', label: '排序', path: 'A', enabled: true },
  { op: 'join', label: 'Join 关联', path: 'A', enabled: false }, { op: 'aggregate', label: 'Aggregate 聚合', path: 'A', enabled: false },
  { op: 'group_by', label: 'Group By 分组', path: 'A', enabled: false }, { op: 'pivot', label: 'Pivot 透视', path: 'A', enabled: false },
  { op: 'detect_wide_table', label: '检测宽表', path: 'WIDE', enabled: true }, { op: 'suggest_split', label: '建议拆分', path: 'WIDE', enabled: true },
  { op: 'apply_split', label: '执行拆分', path: 'WIDE', enabled: true },
  { op: 'parse_json', label: '解析 JSON', path: 'B', enabled: true }, { op: 'parse_xml', label: '解析 XML', path: 'B', enabled: true },
  { op: 'flatten_json', label: 'JSON Flatten', path: 'B', enabled: true }, { op: 'explode_array', label: '数组 Explode', path: 'B', enabled: true },
  { op: 'document_to_markdown', label: '文档转 Markdown', path: 'C', enabled: true }, { op: 'ocr_extract', label: 'OCR 文字提取', path: 'C', enabled: true },
  { op: 'vlm_extract', label: 'VLM 视觉提取', path: 'C', enabled: true }, { op: 'llm_structurize', label: 'LLM 结构化', path: 'C', enabled: true },
]

const PATH_OPS_MAP: Record<string, typeof AVAILABLE_OPS> = {
  auto: AVAILABLE_OPS, structured: AVAILABLE_OPS.filter(o => o.path === 'A'), semi_structured: AVAILABLE_OPS.filter(o => o.path === 'B'),
  unstructured: AVAILABLE_OPS.filter(o => o.path === 'C'), wide_table: AVAILABLE_OPS.filter(o => o.path === 'WIDE'),
}

export default function TransformInspector({ config, onChange, readOnly = false, pipelineId }: { config: Record<string, unknown>; onChange: (key: string, value: unknown) => void; nodeId?: string; readOnly?: boolean; pipelineId?: string }) {
  const currentPath = String(config.path || 'auto')
  const steps = (config.steps || []) as Array<{ op: string; params?: Record<string, unknown> }>
  const [showCatalog, setShowCatalog] = useState(false)
  const [previewMap, setPreviewMap] = useState<Record<number, { loading: boolean; data?: any[]; error?: string }>>({})
  const [models, setModels] = useState<Array<{ id: string; name: string; provider: string; models: string[] }>>([])
  const filteredOps = useMemo(() => PATH_OPS_MAP[currentPath] || AVAILABLE_OPS, [currentPath])
  useEffect(() => { apiClientV2.get('/models').then((r: any) => setModels(Array.isArray(r) ? r : r?.data ?? [])).catch(() => {}) }, [])

  // Runtime stats (hooks must be at top level)
  const [runStats, setRunStats] = useState<{ rows_in: number; rows_out: number } | null>(null);
  useEffect(() => {
    if (!pipelineId || !readOnly) return;
    apiClientV2.get('/pipelines/' + pipelineId + '/runs').then((runs: any) => {
      const last = Array.isArray(runs) && runs.length > 0 ? runs[runs.length - 1] : null;
      if (last) apiClientV2.get('/pipelines/runs/' + last.id).then((d: any) => {
        if (d?.stats) setRunStats({ rows_in: d.stats.rows_in || 0, rows_out: d.stats.rows_out || 0 });
      }).catch(() => {});
    }).catch(() => {});
  }, [pipelineId, readOnly]);

  if (readOnly) {
    return (
      <div className="space-y-3">
        <div className="bg-amber-50 border border-amber-100 rounded-lg p-3 text-xs">
          <p className="text-amber-700 font-medium mb-1">⚙️ 转换配置</p>
          <p className="text-amber-600">路径: {currentPath === 'auto' ? '自动检测' : currentPath === 'structured' ? 'Path A · 结构化' : currentPath === 'semi_structured' ? 'Path B · 半结构化' : currentPath === 'unstructured' ? 'Path C · 非结构化' : '宽表拆分'}</p>
          <p className="text-amber-600">引擎: {String(config.engine || 'pandas')}</p>
          <p className="text-amber-600">步骤: {steps.length} 个</p>
          {steps.length > 0 && (<div className="mt-1 space-y-0.5">{steps.map((s: any, i: number) => (<p key={i} className="text-amber-500">{i + 1}. {AVAILABLE_OPS.find(o => o.op === s.op)?.label || s.op}</p>))}</div>)}
        </div>
        {runStats && (
          <div className="bg-white border rounded-lg p-3 text-xs">
            <p className="font-medium text-gray-700 mb-2">运行时执行结果</p>
            <p className="text-gray-500">输入行数: {runStats.rows_in}</p>
            <p className="text-gray-500">输出行数: {runStats.rows_out}</p>
            <p className="text-green-600 mt-1">转换执行成功</p>
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      <div><label className="text-xs text-gray-500 mb-1 block">处理路径</label>
        <select value={currentPath} onChange={e => { const np = e.target.value; onChange('path', np); const v = PATH_OPS_MAP[np] || AVAILABLE_OPS; const vs = new Set(v.map(o => o.op)); onChange('steps', steps.filter(s => vs.has(s.op))) }} className="w-full border rounded-lg px-3 py-1.5 text-sm"><option value="auto">自动检测</option><option value="structured">Path A · 结构化</option><option value="semi_structured">Path B · 半结构化</option><option value="unstructured">Path C · 非结构化</option><option value="wide_table">宽表拆分</option></select></div>
      <div><label className="text-xs text-gray-500 mb-1 block">引擎</label>
        <select value={String(config.engine || 'pandas')} onChange={e => onChange('engine', e.target.value)} className="w-full border rounded-lg px-3 py-1.5 text-sm">
          <option value="pandas">pandas</option>{currentPath === 'A' && <option value="duckdb">DuckDB</option>}{(currentPath === 'C' || currentPath === 'auto') && (<><option value="llm">LLM</option><option value="vlm">VLM</option><option value="ocr">OCR</option></>)}</select></div>
      <div><div className="flex items-center justify-between mb-1"><label className="text-xs text-gray-500">处理步骤</label><button onClick={() => setShowCatalog(!showCatalog)} className="flex items-center gap-0.5 text-xs text-blue-500"><Plus size={11} />添加</button></div>
        {showCatalog && (<div className="border rounded-lg p-2 mb-2 max-h-40 overflow-y-auto space-y-0.5">{filteredOps.map(op => op.enabled ? (<button key={op.op} onClick={() => { onChange('steps', [...steps, { op: op.op, params: {} }]); setShowCatalog(false) }} className="w-full text-left text-xs px-2 py-1 rounded hover:bg-gray-50 flex items-center gap-2"><span className="text-gray-300 text-[10px]">{op.path}</span><span className="font-medium">{op.label}</span></button>) : (<div key={op.op} className="w-full text-left text-xs px-2 py-1 rounded flex items-center gap-2 opacity-50" title="即将推出"><span className="text-gray-300 text-[10px]">{op.path}</span><span className="text-gray-400">{op.label}</span><span className="ml-auto text-[9px] text-gray-400 border-dashed border rounded px-1">即将推出</span></div>))}</div>)}
        {steps.length === 0 ? <p className="text-xs text-gray-400 italic">暂无步骤</p> : (<div className="space-y-1.5">{steps.map((step: any, i: number) => (
          <div key={i} className="border rounded-lg p-2 text-xs space-y-1">
            <div className="flex items-center justify-between"><span className="font-medium">{i + 1}. {AVAILABLE_OPS.find(o => o.op === step.op)?.label || step.op}</span><div className="flex gap-0.5"><button onClick={async () => { setPreviewMap(p => ({ ...p, [i]: { loading: true } })); try { const r: any = await apiClientV2.post('/pipelines/preview-step', { op: step.op, params: step.params || {}, sample_data: [{ col: 's1' }, { col: 's2' }] }); setPreviewMap(p => ({ ...p, [i]: { loading: false, data: r.preview || [], error: r.error } })) } catch { setPreviewMap(p => ({ ...p, [i]: { loading: false, error: '失败' } })) } }} className="text-gray-400 hover:text-blue-500"><Eye size={11} /></button><button onClick={() => onChange('steps', steps.filter((_: any, j: number) => j !== i))} className="text-gray-400 hover:text-red-500"><Trash2 size={11} /></button></div></div>
          </div>
        ))}</div>)}
      </div>
    </>
  )
}
