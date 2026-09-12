import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Play, Plus, GitBranch, CheckCircle, Clock, XCircle,
  ChevronDown, ChevronUp, Database, FileText, Layers,
  Scissors, FileJson, Cpu, BarChart3
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'
import type { TFunction } from 'i18next'

interface PipelineRun {
  id: string
  status: string
  started_at: string | null
  finished_at: string | null
}

interface Pipeline {
  id: string
  name: string
  route: string
  status: string
}

interface RunMeta {
  inferred_schema?: Record<string, unknown>
  dropped?: number
  rows_before?: number
  rows_after?: number
  wide_table_split?: {
    skipped?: boolean
    executed?: boolean
    suggested?: boolean
    col_count?: number
    tables?: Record<string, number>
    suggestion?: { split_config?: Record<string, unknown> }
  }
  json_flatten?: { rows_before?: number; rows_after?: number }
  document_to_md?: { processed?: number; strategy?: string }
  md_to_structured?: {
    skipped?: boolean
    success?: number
    processed?: number
    reason?: string
    method?: string
  }
}

interface RunDetail {
  id: string
  status: string
  stats: {
    rows_in: number
    rows_out: number
    meta: RunMeta
    curated_dataset_id: string | null
  } | null
  error_log: string | null
}

// ── 步骤卡片定义 ────────────────────────────────────────────────────────────

interface StepCard {
  key: string
  label: string
  icon: React.ReactNode
  status: 'done' | 'skipped' | 'failed' | 'pending'
  detail: string
}

function buildSteps(route: string, stats: RunDetail['stats'] | null, t: TFunction): StepCard[] {
  if (!stats) return []
  const meta = stats.meta || {}

  if (route === 'A') {
    const schema = meta.inferred_schema || {}
    const colCount = Object.keys(schema).length
    const dropped = meta.dropped ?? 0
    const split = meta.wide_table_split

    const steps: StepCard[] = [
      {
        key: 'schema',
        label: t('transformsTab.step_schema_inference'),
        icon: <Layers size={13} />,
        status: colCount > 0 ? 'done' : 'skipped',
        detail: colCount > 0
          ? t('transformsTab.schema_done_detail', { count: colCount, sample: Object.entries(schema).slice(0, 3).map(([k, v]) => `${k}(${v})`).join(', '), more: colCount > 3 ? '…' : '' })
          : t('transformsTab.no_schema_data'),
      },
      {
        key: 'clean',
        label: t('transformsTab.step_data_cleaning'),
        icon: <CheckCircle size={13} />,
        status: 'done',
        detail: t('transformsTab.clean_detail', { before: meta.rows_before ?? stats.rows_in, after: meta.rows_after ?? stats.rows_out, dropped }),
      },
    ]

    if (split && !split.skipped) {
      const tables = split.tables || {}
      steps.push({
        key: 'split',
        label: t('transformsTab.step_wide_table_split'),
        icon: <Scissors size={13} />,
        status: split.executed ? 'done' : split.suggested ? 'done' : 'skipped',
        detail: split.executed
          ? t('transformsTab.split_executed_detail', { count: Object.keys(tables).length, list: Object.entries(tables).map(([n, c]) => t('transformsTab.table_row_count', { name: n, count: c })).join(', ') })
          : split.suggested
          ? t('transformsTab.split_suggested_detail', { cols: Object.keys(split.suggestion?.split_config || {}).join(', ') })
          : t('transformsTab.split_skipped_detail', { count: split.col_count }),
      })
    }

    steps.push({
      key: 'output',
      label: t('transformsTab.step_output_curated'),
      icon: <Database size={13} />,
      status: stats.curated_dataset_id ? 'done' : 'failed',
      detail: stats.curated_dataset_id
        ? t('transformsTab.output_done_detail', { rows: stats.rows_out, id: stats.curated_dataset_id.slice(0, 8) })
        : t('transformsTab.output_failed_detail'),
    })

    return steps
  }

  if (route === 'B') {
    const flatten = meta.json_flatten || {}
    return [
      {
        key: 'parse',
        label: t('transformsTab.step_parse'),
        icon: <FileJson size={13} />,
        status: 'done',
        detail: t('transformsTab.parse_done_detail'),
      },
      {
        key: 'flatten',
        label: t('transformsTab.step_flatten'),
        icon: <Layers size={13} />,
        status: flatten.rows_after !== undefined ? 'done' : 'skipped',
        detail: flatten.rows_before !== undefined
          ? t('transformsTab.flatten_done_detail', { before: flatten.rows_before, after: flatten.rows_after })
          : t('transformsTab.not_executed'),
      },
      {
        key: 'clean',
        label: t('transformsTab.step_data_cleaning'),
        icon: <CheckCircle size={13} />,
        status: 'done',
        detail: t('transformsTab.clean_detail_b', { before: meta.rows_before ?? stats.rows_in, after: meta.rows_after ?? stats.rows_out, dropped: meta.dropped ?? 0 }),
      },
      {
        key: 'output',
        label: t('transformsTab.step_output_curated'),
        icon: <Database size={13} />,
        status: stats.curated_dataset_id ? 'done' : 'failed',
        detail: stats.curated_dataset_id
          ? t('transformsTab.output_done_detail', { rows: stats.rows_out, id: stats.curated_dataset_id.slice(0, 8) })
          : t('transformsTab.output_failed_serialization_detail'),
      },
    ]
  }

  if (route === 'C') {
    const doc = meta.document_to_md || {}
    const extract = meta.md_to_structured || {}
    return [
      {
        key: 'doc',
        label: t('transformsTab.step_doc_to_md'),
        icon: <FileText size={13} />,
        status: doc.processed! > 0 ? 'done' : 'skipped',
        detail: doc.processed! > 0
          ? t('transformsTab.doc_done_detail', { strategy: doc.strategy || 'markitdown', count: doc.processed })
          : t('transformsTab.doc_skipped_detail'),
      },
      {
        key: 'extract',
        label: t('transformsTab.step_llm_extract'),
        icon: <Cpu size={13} />,
        status: extract.skipped ? 'skipped' : extract.success! > 0 ? 'done' : 'failed',
        detail: extract.skipped
          ? t('transformsTab.extract_skipped_detail', { reason: extract.reason || t('transformsTab.extract_no_target_schema') })
          : extract.method
          ? t('transformsTab.extract_done_detail', { method: extract.method, success: extract.success, processed: extract.processed })
          : t('transformsTab.not_executed'),
      },
      {
        key: 'output',
        label: t('transformsTab.step_output_curated'),
        icon: <Database size={13} />,
        status: stats.curated_dataset_id ? 'done' : 'failed',
        detail: stats.curated_dataset_id
          ? t('transformsTab.output_done_detail', { rows: stats.rows_out, id: stats.curated_dataset_id.slice(0, 8) })
          : t('transformsTab.output_failed_detail'),
      },
    ]
  }

  return []
}

// ── 状态样式 ────────────────────────────────────────────────────────────────

const STEP_STATUS_STYLE: Record<string, string> = {
  done:    'bg-green-50 border-green-200 text-green-700',
  skipped: 'bg-gray-50 border-gray-200 text-gray-500',
  failed:  'bg-red-50 border-red-200 text-red-600',
  pending: 'bg-blue-50 border-blue-200 text-blue-600',
}

const STEP_STATUS_ICON: Record<string, React.ReactNode> = {
  done:    <CheckCircle size={12} className="text-green-500 flex-shrink-0" />,
  skipped: <Clock size={12} className="text-gray-400 flex-shrink-0" />,
  failed:  <XCircle size={12} className="text-red-400 flex-shrink-0" />,
  pending: <Clock size={12} className="text-blue-400 flex-shrink-0" />,
}

const ROUTE_STYLE: Record<string, string> = {
  A: 'bg-blue-50 text-blue-700 border-blue-200',
  B: 'bg-amber-50 text-amber-700 border-amber-200',
  C: 'bg-purple-50 text-purple-700 border-purple-200',
}

const ROUTE_LABEL_KEY: Record<string, string> = {
  A: 'transformsTab.route_a_label',
  B: 'transformsTab.route_b_label',
  C: 'transformsTab.route_c_label',
}

// ── 主组件 ──────────────────────────────────────────────────────────────────

export default function TransformsTab() {
  const { t } = useTranslation()
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [runDetails, setRunDetails] = useState<Record<string, RunDetail | null | undefined>>({})

  const [showCreate, setShowCreate] = useState(false)
  const [datasets, setDatasets] = useState<Array<{id: string; name: string; kind: string}>>([])
  const [createName, setCreateName] = useState('')
  const [createDatasetId, setCreateDatasetId] = useState('')
  const [createRoute, setCreateRoute] = useState<'A'|'B'|'C'>('A')
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    apiClientV2.get<{ data?: Pipeline[] } | Pipeline[]>('/pipelines')
      .then(res => setPipelines(Array.isArray(res) ? res : res.data ?? []))
      .catch(() => setPipelines([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!showCreate) return
    apiClientV2.get<Array<{ id: string; name: string; kind: string }>>('/datasets')
      .then(res => setDatasets(Array.isArray(res) ? res.filter(d => d.kind !== 'curated') : []))
      .catch(() => setDatasets([]))
  }, [showCreate])

  const handleCreate = async () => {
    if (!createName.trim()) { setCreateError(t('transformsTab.name_required_error')); return }
    if (!createDatasetId) { setCreateError(t('transformsTab.source_required_error')); return }
    setCreating(true)
    try {
      await apiClientV2.post('/pipelines', { name: createName, source_dataset_id: createDatasetId, route: createRoute })
      setShowCreate(false)
      setCreateName(''); setCreateDatasetId(''); setCreateRoute('A'); setCreateError('')
      const res = await apiClientV2.get<{ data?: Pipeline[] } | Pipeline[]>('/pipelines')
      setPipelines(Array.isArray(res) ? res : res.data ?? [])
    } catch (e: unknown) {
      const err = e as {detail?: string; message?: string}
      setCreateError(err?.detail || err?.message || t('transformsTab.create_failed'))
    } finally {
      setCreating(false)
    }
  }

  const loadRunDetail = async (plId: string) => {
    if (runDetails[plId] !== undefined) return
    try {
      const runs = await apiClientV2.get<{ data?: PipelineRun[] } | PipelineRun[]>(`/pipelines/${plId}/runs`)
      const runsArr: PipelineRun[] = Array.isArray(runs) ? runs : runs.data ?? []
      const last = runsArr[runsArr.length - 1]
      if (!last) { setRunDetails(p => ({ ...p, [plId]: null })); return }
      const detail = await apiClientV2.get<RunDetail & { data?: RunDetail }>(`/pipelines/runs/${last.id}`)
      setRunDetails(p => ({ ...p, [plId]: detail.data ?? detail }))
    } catch {
      setRunDetails(p => ({ ...p, [plId]: null }))
    }
  }

  const toggleExpand = (id: string) => {
    const next = expanded === id ? null : id
    setExpanded(next)
    if (next) loadRunDetail(next)
  }

  const handleRun = async (id: string) => {
    setRunning(id)
    setRunDetails(p => ({ ...p, [id]: undefined }))
    try {
      await apiClientV2.post(`/pipelines/${id}/run-sync`)
      setPipelines(prev => prev.map(p => p.id === id ? { ...p, status: 'success' } : p))
      loadRunDetail(id)
    } catch {
      setPipelines(prev => prev.map(p => p.id === id ? { ...p, status: 'failed' } : p))
    } finally {
      setRunning(false as unknown as string | null)
      setRunning(null)
    }
  }

  if (loading) return <div className="text-gray-400 text-sm p-4">{t('common.loading')}</div>

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-lg font-semibold">{t('transformsTab.title')}</h2>
          <p className="text-xs text-gray-400 mt-0.5">{t('transformsTab.subtitle')}</p>
        </div>
        <button
          onClick={() => { setShowCreate(true); setCreateError('') }}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white text-sm rounded-lg hover:bg-gray-800"
        >
          <Plus size={14} /> {t('transformsTab.new_pipeline')}
        </button>
      </div>

      {pipelines.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-10 text-center text-gray-400 space-y-2">
          <GitBranch size={28} className="mx-auto opacity-30" />
          <p className="text-sm">{t('transformsTab.empty')}</p>
          <p className="text-xs">{t('transformsTab.empty_hint')}</p>
        </div>
      ) : (
        <div className="space-y-2">
          {pipelines.map(pl => {
            const isExpanded = expanded === pl.id
            const detail = runDetails[pl.id]
            const isRunning = running === pl.id

            return (
              <div key={pl.id} className="border rounded-xl overflow-hidden bg-white">
                {/* 流水线标题行 */}
                <div className="p-4 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-medium text-sm truncate">{pl.name}</span>
                      {pl.route && (
                        <span className={`text-xs px-1.5 py-0.5 rounded border flex-shrink-0 ${ROUTE_STYLE[pl.route] ?? 'bg-gray-50 text-gray-600 border-gray-200'}`}>
                          {ROUTE_LABEL_KEY[pl.route] ? t(ROUTE_LABEL_KEY[pl.route]) : `Route ${pl.route}`}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-gray-400 font-mono">{pl.id.slice(0, 8)}</span>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {detail?.stats && (
                      <span className="text-xs text-gray-500">
                        {t('transformsTab.rows_arrow', { in: detail.stats.rows_in, out: detail.stats.rows_out })}
                      </span>
                    )}
                    <span className={`text-xs ${pl.status === 'success' ? 'text-green-600' : pl.status === 'failed' ? 'text-red-500' : 'text-gray-400'}`}>
                      {pl.status === 'success' ? '✅' : pl.status === 'failed' ? '❌' : '⏳'}
                    </span>
                    <button
                      onClick={() => handleRun(pl.id)}
                      disabled={!!isRunning}
                      className="flex items-center gap-1 px-2.5 py-1 text-xs bg-gray-100 rounded-lg hover:bg-gray-200 disabled:opacity-50"
                    >
                      <Play size={11} className={isRunning ? 'animate-pulse' : ''} />
                      {isRunning ? t('transformsTab.run_running') : t('pipelineBuilder.run')}
                    </button>
                    <button
                      onClick={() => toggleExpand(pl.id)}
                      className="p-1 rounded hover:bg-gray-100 text-gray-500"
                    >
                      {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                    </button>
                  </div>
                </div>

                {/* 步骤卡片展开区 */}
                {isExpanded && (
                  <div className="border-t bg-gray-50 px-4 py-3">
                    {!detail ? (
                      <p className="text-xs text-gray-400">{t('transformsTab.loading_steps')}</p>
                    ) : !detail.stats ? (
                      <p className="text-xs text-gray-400">
                        {detail.error_log ? t('transformsTab.run_failed_prefix', { error: detail.error_log }) : t('transformsTab.not_run_yet')}
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {/* 步骤时间线 */}
                        <div className="flex items-center gap-1 flex-wrap mb-3">
                          {buildSteps(pl.route, detail.stats, t).map((step, i, arr) => (
                            <div key={step.key} className="flex items-center gap-1">
                              <span className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded border ${STEP_STATUS_STYLE[step.status]}`}>
                                {step.icon}
                                {step.label}
                              </span>
                              {i < arr.length - 1 && (
                                <span className="text-gray-300 text-xs">→</span>
                              )}
                            </div>
                          ))}
                        </div>

                        {/* 步骤详情列表 */}
                        <div className="space-y-1.5">
                          {buildSteps(pl.route, detail.stats, t).map(step => (
                            <div key={step.key} className={`flex items-start gap-2 text-xs rounded-lg px-3 py-2 border ${STEP_STATUS_STYLE[step.status]}`}>
                              <div className="flex items-center gap-1.5 w-32 flex-shrink-0 font-medium">
                                {STEP_STATUS_ICON[step.status]}
                                {step.label}
                              </div>
                              <span className="text-gray-600 flex-1">{step.detail}</span>
                            </div>
                          ))}
                        </div>

                        {/* Schema 详情（Route A）*/}
                        {pl.route === 'A' && detail.stats.meta?.inferred_schema && (
                          <div className="mt-2 border rounded-lg overflow-hidden">
                            <div className="bg-gray-100 px-3 py-1.5 text-xs font-medium text-gray-600 flex items-center gap-1.5">
                              <BarChart3 size={12} /> {t('transformsTab.inferred_col_types')}
                            </div>
                            <div className="flex flex-wrap gap-1.5 p-2">
                              {Object.entries(detail.stats.meta.inferred_schema).slice(0, 12).map(([col, type]) => (
                                <span key={col} className="text-xs bg-white border rounded px-2 py-0.5">
                                  <span className="font-medium">{col}</span>
                                  <span className="text-gray-400 ml-1">({String(type)})</span>
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {showCreate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg shadow-lg p-6 w-[440px]">
            <h3 className="font-semibold mb-4">{t('transformsTab.create_title')}</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('transformsTab.name_label')} *</label>
                <input
                  value={createName}
                  onChange={e => setCreateName(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                  placeholder={t('transformsTab.name_ph')}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('transformsTab.source_label')} *</label>
                <select
                  value={createDatasetId}
                  onChange={e => setCreateDatasetId(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                >
                  <option value="">{t('transformsTab.source_ph')}</option>
                  {datasets.map(d => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('transformsTab.path_label')} *</label>
                <div className="flex gap-2">
                  {(['A', 'B', 'C'] as const).map(r => (
                    <button
                      key={r}
                      type="button"
                      onClick={() => setCreateRoute(r)}
                      className={`flex-1 text-xs px-3 py-2 rounded-lg border transition-colors ${
                        createRoute === r ? 'bg-black text-white border-black' : 'border-gray-200 text-gray-600 hover:bg-gray-50'
                      }`}
                    >
                      {r === 'A' ? t('transformsTab.path_a') : r === 'B' ? t('transformsTab.path_b') : t('transformsTab.path_c')}
                    </button>
                  ))}
                </div>
              </div>
              {createError && <p className="text-xs text-red-500">{createError}</p>}
            </div>
            <div className="flex justify-end gap-3 mt-4">
              <button
                type="button"
                onClick={() => { setShowCreate(false); setCreateError('') }}
                className="px-4 py-2 border rounded-lg text-sm"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={creating}
                className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
              >
                {creating ? t('transformsTab.creating') : t('common.create')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
