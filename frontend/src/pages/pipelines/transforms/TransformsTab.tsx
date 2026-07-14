import { useState, useEffect } from 'react'
import {
  Play, Plus, GitBranch, CheckCircle, Clock, XCircle,
  ChevronDown, ChevronUp, Database, FileText, Layers,
  Scissors, FileJson, Cpu, BarChart3
} from 'lucide-react'
import { apiClientV2 } from '@/api/client'

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

interface RunDetail {
  id: string
  status: string
  stats: {
    rows_in: number
    rows_out: number
    meta: Record<string, any>
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

function buildSteps(route: string, stats: RunDetail['stats'] | null): StepCard[] {
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
        label: 'Schema 推断',
        icon: <Layers size={13} />,
        status: colCount > 0 ? 'done' : 'skipped',
        detail: colCount > 0
          ? `${colCount} 列：${Object.entries(schema).slice(0, 3).map(([k, v]) => `${k}(${v})`).join(', ')}${colCount > 3 ? '…' : ''}`
          : '无 schema 数据',
      },
      {
        key: 'clean',
        label: '数据清洗',
        icon: <CheckCircle size={13} />,
        status: 'done',
        detail: `${meta.rows_before ?? stats.rows_in} → ${meta.rows_after ?? stats.rows_out} 行，去重丢弃 ${dropped} 行`,
      },
    ]

    if (split && !split.skipped) {
      const tables = split.tables || {}
      steps.push({
        key: 'split',
        label: '宽表拆分',
        icon: <Scissors size={13} />,
        status: split.executed ? 'done' : split.suggested ? 'done' : 'skipped',
        detail: split.executed
          ? `拆分为 ${Object.keys(tables).length} 张子表：${Object.entries(tables).map(([n, c]) => `${n}(${c}行)`).join(', ')}`
          : split.suggested
          ? `建议拆分（待用户确认）：${Object.keys(split.suggestion?.split_config || {}).join(', ')}`
          : `跳过（${split.col_count} 列 < 80列阈值）`,
      })
    }

    steps.push({
      key: 'output',
      label: '输出 Curated',
      icon: <Database size={13} />,
      status: stats.curated_dataset_id ? 'done' : 'failed',
      detail: stats.curated_dataset_id
        ? `${stats.rows_out} 行 → Curated Dataset (${stats.curated_dataset_id.slice(0, 8)})`
        : `生成失败`,
    })

    return steps
  }

  if (route === 'B') {
    const flatten = meta.json_flatten || {}
    return [
      {
        key: 'parse',
        label: 'JSON/XML 解析',
        icon: <FileJson size={13} />,
        status: 'done',
        detail: '检测嵌套结构，准备 Flatten',
      },
      {
        key: 'flatten',
        label: 'Flatten 摊平',
        icon: <Layers size={13} />,
        status: flatten.rows_after !== undefined ? 'done' : 'skipped',
        detail: flatten.rows_before !== undefined
          ? `${flatten.rows_before} → ${flatten.rows_after} 行（数组 explode）`
          : '未执行',
      },
      {
        key: 'clean',
        label: '数据清洗',
        icon: <CheckCircle size={13} />,
        status: 'done',
        detail: `${meta.rows_before ?? stats.rows_in} → ${meta.rows_after ?? stats.rows_out} 行，去重 ${meta.dropped ?? 0} 行`,
      },
      {
        key: 'output',
        label: '输出 Curated',
        icon: <Database size={13} />,
        status: stats.curated_dataset_id ? 'done' : 'failed',
        detail: stats.curated_dataset_id
          ? `${stats.rows_out} 行 → Curated Dataset (${stats.curated_dataset_id.slice(0, 8)})`
          : '生成失败（序列化错误）',
      },
    ]
  }

  if (route === 'C') {
    const doc = meta.document_to_md || {}
    const extract = meta.md_to_structured || {}
    return [
      {
        key: 'doc',
        label: '文档 → Markdown',
        icon: <FileText size={13} />,
        status: doc.processed > 0 ? 'done' : 'skipped',
        detail: doc.processed > 0
          ? `策略: ${doc.strategy || 'markitdown'}，处理 ${doc.processed} 个文档`
          : '未处理（无文档行）',
      },
      {
        key: 'extract',
        label: 'LLM 结构化提取',
        icon: <Cpu size={13} />,
        status: extract.skipped ? 'skipped' : extract.success > 0 ? 'done' : 'failed',
        detail: extract.skipped
          ? `已跳过：${extract.reason || '无 target_schema'}（旧版本）`
          : extract.method
          ? `方法: ${extract.method}，成功 ${extract.success}/${extract.processed} 行`
          : '未执行',
      },
      {
        key: 'output',
        label: '输出 Curated',
        icon: <Database size={13} />,
        status: stats.curated_dataset_id ? 'done' : 'failed',
        detail: stats.curated_dataset_id
          ? `${stats.rows_out} 行 → Curated Dataset (${stats.curated_dataset_id.slice(0, 8)})`
          : '生成失败',
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

const ROUTE_LABEL: Record<string, string> = {
  A: 'Route A · 结构化',
  B: 'Route B · 半结构化',
  C: 'Route C · 非结构化',
}

// ── 主组件 ──────────────────────────────────────────────────────────────────

export default function TransformsTab() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [runDetails, setRunDetails] = useState<Record<string, RunDetail | null>>({})

  const [showCreate, setShowCreate] = useState(false)
  const [datasets, setDatasets] = useState<Array<{id: string; name: string; kind: string}>>([])
  const [createName, setCreateName] = useState('')
  const [createDatasetId, setCreateDatasetId] = useState('')
  const [createRoute, setCreateRoute] = useState<'A'|'B'|'C'>('A')
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    apiClientV2.get('/pipelines')
      .then((res: any) => setPipelines(Array.isArray(res) ? res : res.data ?? []))
      .catch(() => setPipelines([]))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!showCreate) return
    apiClientV2.get('/datasets')
      .then((res: any) => setDatasets(Array.isArray(res) ? res.filter((d: any) => d.kind !== 'curated') : []))
      .catch(() => setDatasets([]))
  }, [showCreate])

  const handleCreate = async () => {
    if (!createName.trim()) { setCreateError('请填写管道名称'); return }
    if (!createDatasetId) { setCreateError('请选择数据源'); return }
    setCreating(true)
    try {
      await apiClientV2.post('/pipelines', { name: createName, source_dataset_id: createDatasetId, route: createRoute })
      setShowCreate(false)
      setCreateName(''); setCreateDatasetId(''); setCreateRoute('A'); setCreateError('')
      const res: any = await apiClientV2.get('/pipelines')
      setPipelines(Array.isArray(res) ? res : res.data ?? [])
    } catch (e: unknown) {
      const err = e as {detail?: string; message?: string}
      setCreateError(err?.detail || err?.message || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const loadRunDetail = async (plId: string) => {
    if (runDetails[plId] !== undefined) return
    try {
      const runs: any = await apiClientV2.get(`/pipelines/${plId}/runs`)
      const runsArr: PipelineRun[] = Array.isArray(runs) ? runs : runs.data ?? []
      const last = runsArr[runsArr.length - 1]
      if (!last) { setRunDetails(p => ({ ...p, [plId]: null })); return }
      const detail: any = await apiClientV2.get(`/pipelines/runs/${last.id}`)
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
    setRunDetails(p => ({ ...p, [id]: undefined as any }))
    try {
      await apiClientV2.post(`/pipelines/${id}/run-sync`)
      setPipelines(prev => prev.map(p => p.id === id ? { ...p, status: 'success' } : p))
      loadRunDetail(id)
    } catch {
      setPipelines(prev => prev.map(p => p.id === id ? { ...p, status: 'failed' } : p))
    } finally {
      setRunning(false as any)
      setRunning(null)
    }
  }

  if (loading) return <div className="text-gray-400 text-sm p-4">加载中...</div>

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-lg font-semibold">Transform 流水线</h2>
          <p className="text-xs text-gray-400 mt-0.5">三条处理路径：A 结构化 / B 半结构化 / C 非结构化文档</p>
        </div>
        <button
          onClick={() => { setShowCreate(true); setCreateError('') }}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white text-sm rounded-lg hover:bg-gray-800"
        >
          <Plus size={14} /> 新建流水线
        </button>
      </div>

      {pipelines.length === 0 ? (
        <div className="border-2 border-dashed rounded-xl p-10 text-center text-gray-400 space-y-2">
          <GitBranch size={28} className="mx-auto opacity-30" />
          <p className="text-sm">暂无流水线</p>
          <p className="text-xs">上传数据集后创建 Transform 流水线，支持结构化 / 半结构化 / 非结构化三条路径</p>
        </div>
      ) : (
        <div className="space-y-2">
          {pipelines.map(pl => {
            const isExpanded = expanded === pl.id
            const detail = runDetails[pl.id]
            const steps = isExpanded && detail ? buildSteps(pl.route, detail.stats) : []
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
                          {ROUTE_LABEL[pl.route] ?? `Route ${pl.route}`}
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-gray-400 font-mono">{pl.id.slice(0, 8)}</span>
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {detail?.stats && (
                      <span className="text-xs text-gray-500">
                        {detail.stats.rows_in}→{detail.stats.rows_out} 行
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
                      {isRunning ? '运行中' : '运行'}
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
                      <p className="text-xs text-gray-400">加载步骤详情中...</p>
                    ) : !detail.stats ? (
                      <p className="text-xs text-gray-400">
                        {detail.error_log ? `运行失败：${detail.error_log}` : '尚未运行，点击「运行」执行流水线'}
                      </p>
                    ) : (
                      <div className="space-y-2">
                        {/* 步骤时间线 */}
                        <div className="flex items-center gap-1 flex-wrap mb-3">
                          {buildSteps(pl.route, detail.stats).map((step, i, arr) => (
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
                          {buildSteps(pl.route, detail.stats).map(step => (
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
                              <BarChart3 size={12} /> 推断的列类型
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
            <h3 className="font-semibold mb-4">新建管道</h3>
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-500 mb-1">管道名称 *</label>
                <input
                  value={createName}
                  onChange={e => setCreateName(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                  placeholder="请输入管道名称"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">数据源 *</label>
                <select
                  value={createDatasetId}
                  onChange={e => setCreateDatasetId(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2 text-sm"
                >
                  <option value="">请选择数据集</option>
                  {datasets.map(d => (
                    <option key={d.id} value={d.id}>{d.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">处理路径 *</label>
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
                      {r === 'A' ? 'A · 结构化' : r === 'B' ? 'B · 半结构化' : 'C · 非结构化'}
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
                取消
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={creating}
                className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
              >
                {creating ? '创建中...' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
