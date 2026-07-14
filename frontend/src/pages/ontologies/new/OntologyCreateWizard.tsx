import { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { apiClientV2 } from '@/api/client'
import pipelinesApi, { type Pipeline } from '@/api/v2/pipelines'
import curatedApi from '@/api/v2/curated'
import { DOMAINS } from '@/types/ontology'
import {
  Zap, GitBranch, ArrowLeft, ArrowRight, Loader2,
  CheckSquare, Square, CheckCircle, XCircle,
  Search, X, Clock, AlertTriangle,
} from 'lucide-react'

type Mode = 'simple_llm' | 'pipeline_mapping'
type Step = 'select_mode' | 'fill_info' | 'select_datasets' | 'approve_data' | 'mapping_config' | 'building'

interface CuratedDataset {
  id: string; name: string; status: string
  row_count: number | null; quality_score: number | null
  columns?: string[]; sample_rows?: Record<string, unknown>[]
}
interface MappingSuggestion {
  entity_class: string; entity_class_cn: string; primary_key_column: string
  field_mappings: { column_name: string; property_name: string }[]
}
interface DatasetRow {
  pipelineId: string; pipelineName: string; domain: string
  curatedId: string; curatedName: string; curatedStatus: string
  rowCount: number | null; qualityScore: number | null
}

// Human Review 已移除 — 用户可在本体详情页自行修改
const BUILD_PHASES = [
  { key: 'entity',    label: '① Entity Type 识别',  icon: '🧩' },
  { key: 'property',  label: '② Property Mapping',   icon: '📋' },
  { key: 'relation',  label: '③ Relation 推断',      icon: '🔗' },
  { key: 'logic',     label: '④ Logic Discovery',    icon: '⚖️' },
  { key: 'action',    label: '⑤ Action Discovery',   icon: '⚡' },
  { key: 'neo4j',     label: '⑥ 写入 Neo4j',         icon: '🕸️' },
  { key: 'chroma',    label: '⑦ 写入 ChromaDB',      icon: '📊' },
  { key: 'publish',   label: '⑧ 完成',               icon: '🚀' },
]

const STATUS_ICON = (status: string) => {
  if (status === 'approved') return <CheckCircle size={12} className="text-green-500" />
  if (status === 'rejected') return <AlertTriangle size={12} className="text-red-400" />
  return <Clock size={12} className="text-yellow-400" />
}
const STATUS_LABEL: Record<string, string> = {
  pending_review: '待审核', approved: '已审核', rejected: '已拒绝',
}
const STATUS_STYLE: Record<string, string> = {
  pending_review: 'bg-yellow-50 text-yellow-700 border-yellow-200',
  approved: 'bg-green-50 text-green-700 border-green-200',
  rejected: 'bg-red-50 text-red-600 border-red-200',
}

function StepIndicator({ current }: { current: 0 | 1 | 2 }) {
  const labels = ['基本信息', '选择数据集', 'Mapping 配置']
  return (
    <div className="flex gap-2 mb-6 text-xs">
      {labels.map((s, i) => (
        <div key={s} className="flex items-center gap-1.5">
          <span className={`w-5 h-5 rounded-full flex items-center justify-center font-medium
            ${i < current ? 'bg-green-500 text-white' : i === current ? 'bg-black text-white' : 'bg-gray-100 text-gray-400'}`}>
            {i + 1}
          </span>
          <span className={i === current ? 'text-black' : 'text-gray-400'}>{s}</span>
          {i < 2 && <span className="text-gray-300 mx-1">›</span>}
        </div>
      ))}
    </div>
  )
}

function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)) }

export default function OntologyCreateWizard() {
  const navigate = useNavigate()
  const { t } = useTranslation()
  const qc = useQueryClient()

  const [step, setStep] = useState<Step>('select_mode')
  const [mode, setMode] = useState<Mode>('simple_llm')
  const [name, setName] = useState('')
  const [domain, setDomain] = useState(DOMAINS[0])
  const [desc, setDesc] = useState('')
  const [error, setError] = useState('')

  // Dataset selection state
  const [pipelines, setPipelines] = useState<Pipeline[]>([])
  const [datasets, setDatasets] = useState<CuratedDataset[]>([])
  const [datasetsLoading, setDatasetsLoading] = useState(false)
  const [pipelineFilter, setPipelineFilter] = useState('')
  const [curatedFilter, setCuratedFilter] = useState('')
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<Set<string>>(new Set())

  // Approve step state
  const [approvingId, setApprovingId] = useState<string | null>(null)

  const [createdOntologyId, setCreatedOntologyId] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Record<string, MappingSuggestion>>({})
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  // Building state
  const [currentPhase, setCurrentPhase] = useState(0)
  const [phaseStatus, setPhaseStatus] = useState<string[]>(Array(BUILD_PHASES.length).fill('pending'))
  const [buildResult, setBuildResult] = useState<any>(null)
  const [buildError, setBuildError] = useState('')
  const [buildDone, setBuildDone] = useState(false)

  const loadDatasets = () => {
    setDatasetsLoading(true)
    Promise.all([
      pipelinesApi.list(),
      curatedApi.list() as Promise<CuratedDataset[]>,
    ]).then(([pls, cur]) => {
      setPipelines(Array.isArray(pls) ? pls : [])
      setDatasets(Array.isArray(cur) ? cur : [])
    }).catch(() => {}).finally(() => setDatasetsLoading(false))
  }

  useEffect(() => {
    if (step === 'select_datasets' || step === 'approve_data') loadDatasets()
  }, [step])

  // Join pipelines with curated datasets
  const datasetRows = useMemo<DatasetRow[]>(() => {
    const curatedById = new Map(datasets.map(c => [c.id, c]))
    const rows: DatasetRow[] = []
    pipelines.forEach(pl => {
      const ids: string[] = pl.target_curated_ids ?? []
      const matched = ids.length > 0
        ? ids.map(id => curatedById.get(id)).filter(Boolean) as CuratedDataset[]
        : datasets.filter(c => c.name.startsWith(pl.name))
      matched.forEach(c => rows.push({
        pipelineId: pl.id, pipelineName: pl.name, domain: pl.domain || '通用',
        curatedId: c.id, curatedName: c.name, curatedStatus: c.status || 'pending_review',
        rowCount: c.row_count, qualityScore: c.quality_score,
      }))
    })
    // Also include curated datasets not linked to any pipeline
    const linked = new Set(rows.map(r => r.curatedId))
    datasets.forEach(c => {
      if (!linked.has(c.id)) rows.push({
        pipelineId: '', pipelineName: '（未关联管道）', domain: '—',
        curatedId: c.id, curatedName: c.name, curatedStatus: c.status || 'pending_review',
        rowCount: c.row_count, qualityScore: c.quality_score,
      })
    })
    return rows
  }, [pipelines, datasets])

  const approvedRows = useMemo(
    () => datasetRows.filter(r => r.curatedStatus === 'approved' || r.curatedStatus === 'active'),
    [datasetRows]
  )
  const pendingRows = useMemo(
    () => datasetRows.filter(r => r.curatedStatus !== 'approved' && r.curatedStatus !== 'active' && r.curatedStatus !== 'rejected'),
    [datasetRows]
  )

  const filteredApproved = useMemo(() => {
    const pq = pipelineFilter.toLowerCase()
    const cq = curatedFilter.toLowerCase()
    return approvedRows.filter(r => {
      if (pq && !r.pipelineName.toLowerCase().includes(pq) && !r.pipelineId.toLowerCase().includes(pq)) return false
      if (cq && !r.curatedName.toLowerCase().includes(cq) && !r.curatedId.toLowerCase().includes(cq)) return false
      return true
    })
  }, [approvedRows, pipelineFilter, curatedFilter])

  const toggleDataset = (id: string) => {
    setSelectedDatasetIds(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  const allFilteredSelected =
    filteredApproved.length > 0 && filteredApproved.every(r => selectedDatasetIds.has(r.curatedId))

  const toggleSelectAll = () => {
    if (allFilteredSelected) {
      // 取消选中当前筛选结果
      setSelectedDatasetIds(prev => {
        const n = new Set(prev)
        filteredApproved.forEach(r => n.delete(r.curatedId))
        return n
      })
    } else {
      // 全选当前筛选结果
      setSelectedDatasetIds(prev => {
        const n = new Set(prev)
        filteredApproved.forEach(r => n.add(r.curatedId))
        return n
      })
    }
  }

  const handleApprove = async (id: string) => {
    setApprovingId(id)
    try {
      await curatedApi.approve(id)
      setDatasets(prev => prev.map(d => d.id === id ? { ...d, status: 'approved' } : d))
    } finally { setApprovingId(null) }
  }

  const handleGetSuggestions = async () => {
    if (!createdOntologyId || selectedDatasetIds.size === 0) return
    setSuggestionsLoading(true)
    // 并行发起所有 suggest 请求，不再串行等待
    const entries = [...selectedDatasetIds]
      .map(dsId => ({ dsId, ds: datasets.find(d => d.id === dsId) }))
      .filter(({ ds }) => !!ds) as { dsId: string; ds: CuratedDataset }[]

    const results = await Promise.allSettled(
      entries.map(({ dsId, ds }) =>
        apiClientV2.post(`/ontologies/${createdOntologyId}/mappings/suggest`, {
          dataset_name: ds.name, columns: ds.columns || [], sample_rows: ds.sample_rows || [], ontology_domain: domain,
        }).then(res => ({ dsId, res }))
      )
    )

    const next: Record<string, MappingSuggestion> = {}
    results.forEach((result, i) => {
      const { dsId, ds } = entries[i]
      if (result.status === 'fulfilled') {
        next[dsId] = result.value.res as MappingSuggestion
      } else {
        next[dsId] = { entity_class: ds.name, entity_class_cn: ds.name, primary_key_column: 'id', field_mappings: [] }
      }
    })
    setSuggestions(next); setSuggestionsLoading(false); setStep('mapping_config')
  }

  const handleStartBuild = async () => {
    if (!createdOntologyId) return
    setStep('building')
    setPhaseStatus(Array(BUILD_PHASES.length).fill('pending'))
    setBuildDone(false)

    for (const [dsId, sug] of Object.entries(suggestions)) {
      const fmap: Record<string, string> = { __primary_key__: sug.primary_key_column }
      for (const fm of sug.field_mappings) fmap[fm.column_name] = fm.property_name
      await apiClientV2.post(`/ontologies/${createdOntologyId}/mappings`, {
        curated_dataset_id: dsId, entity_class: sug.entity_class, field_mapping: fmap, confidence: 1.0,
      }).catch(() => {})
    }

    const mark = (idx: number, status: string) =>
      setPhaseStatus(prev => { const a = [...prev]; a[idx] = status; return a })

    mark(0, 'running'); await sleep(400)
    mark(0, 'done'); mark(1, 'running'); await sleep(400)
    mark(1, 'done'); mark(2, 'running'); setCurrentPhase(2)

    try {
      const res: any = await apiClientV2.post(`/ontologies/${createdOntologyId}/mappings/build-all`)
      setBuildResult(res)
      mark(2, 'done'); mark(3, 'running'); setCurrentPhase(3); await sleep(500)
      mark(3, 'done'); mark(4, 'running'); setCurrentPhase(4); await sleep(500)
      mark(4, 'done'); mark(5, 'running'); setCurrentPhase(5); await sleep(400)
      mark(5, 'done'); mark(6, 'running'); await sleep(300)
      mark(6, 'done'); mark(7, 'running'); await sleep(300)
      mark(7, 'done'); setCurrentPhase(8)
      setBuildDone(true)
    } catch (e: any) {
      mark(2, 'failed')
      setBuildError(e?.message || e?.detail || '构建失败')
    }
  }

  const createMut = useMutation({
    mutationFn: () => ontologyApi.create({ name, domain, description: desc, build_mode: mode }),
    onSuccess: (res: any) => {
      qc.invalidateQueries({ queryKey: ['ontologies'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      if (mode === 'simple_llm') navigate(`/ontologies/${res.id}?tab=files`)
      else { setCreatedOntologyId(res.id); setStep('select_datasets') }
    },
    onError: (e: any) => { setError(e?.message || e?.detail?.message || '创建失败') },
  })

  // ── Steps ──────────────────────────────────────────────────────────

  if (step === 'select_mode') return (
    <div>
      <button onClick={() => navigate('/ontologies')} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-black mb-6">
        <ArrowLeft size={14} /> {t('ontology.back')}
      </button>
      <h2 className="text-xl font-semibold mb-2">新建本体</h2>
      <p className="text-sm text-gray-500 mb-8">选择构建方式</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-3xl">
        <button onClick={() => { setMode('simple_llm'); setStep('fill_info') }}
          className="group text-left p-6 rounded-xl border-2 transition-all hover:border-black hover:shadow-md border-gray-200">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 bg-amber-100 rounded-lg flex items-center justify-center"><Zap size={20} className="text-amber-600" /></div>
            <span className="font-semibold">简易 LLM 提取</span>
          </div>
          <p className="text-sm text-gray-600 mb-4">上传文件，选择模型和提示词，LLM 一键提取。</p>
          <ul className="text-xs text-gray-500 space-y-1"><li>✓ 快速原型验证</li><li>✓ 少量文档</li><li>✓ 探索性分析</li></ul>
          <div className="mt-4 flex items-center gap-1 text-sm font-medium text-black">选择此方式 <ArrowRight size={14} /></div>
        </button>
        <button onClick={() => { setMode('pipeline_mapping'); setStep('fill_info') }}
          className="group text-left p-6 rounded-xl border-2 transition-all hover:border-black hover:shadow-md border-gray-200">
          <div className="flex items-center gap-3 mb-3">
            <div className="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center"><GitBranch size={20} className="text-blue-600" /></div>
            <span className="font-semibold">Pipeline Mapping</span>
          </div>
          <p className="text-sm text-gray-600 mb-4">从已审批的 Curated Datasets 映射生成本体。</p>
          <ul className="text-xs text-gray-500 space-y-1"><li>✓ 结构化/半结构化数据</li><li>✓ 精细化建模</li><li>✓ 企业级大规模数据</li></ul>
          <div className="mt-4 flex items-center gap-1 text-sm font-medium text-black">选择此方式 <ArrowRight size={14} /></div>
        </button>
      </div>
    </div>
  )

  if (step === 'fill_info') return (
    <div className="max-w-xl">
      <button onClick={() => setStep('select_mode')} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-black mb-6">
        <ArrowLeft size={14} /> 返回选择方式
      </button>
      <h2 className="text-xl font-semibold mb-1">新建本体</h2>
      <p className="text-sm text-gray-400 mb-2">{mode === 'simple_llm' ? '⚡ 简易 LLM 提取' : '🔄 Pipeline Mapping'}</p>
      {mode === 'pipeline_mapping' && <StepIndicator current={0} />}
      <div className="bg-white rounded-xl border p-6 space-y-4">
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">名称 *</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="本体名称" className="w-full border rounded-lg px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">领域 *</label>
          <select value={domain} onChange={e => setDomain(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm">
            {DOMAINS.map(d => <option key={d}>{d}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-600 mb-1">描述（可选）</label>
          <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={2} placeholder="简要描述本体用途"
            className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
        </div>
        {error && <p className="text-red-500 text-xs">{error}</p>}
        <div className="flex justify-between pt-2">
          <button onClick={() => setStep('select_mode')} className="px-4 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">上一步</button>
          <button onClick={() => createMut.mutate()} disabled={!name || createMut.isPending}
            className="px-5 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2">
            {createMut.isPending && <Loader2 size={14} className="animate-spin" />}
            {mode === 'pipeline_mapping' ? '下一步' : '创建本体'}
          </button>
        </div>
      </div>
    </div>
  )

  if (step === 'select_datasets') return (
    <div>
      <h2 className="text-xl font-semibold mb-1">选择数据集</h2>
      <p className="text-sm text-gray-400 mb-4">🔄 Pipeline Mapping</p>
      <StepIndicator current={1} />

      <div className="bg-white rounded-xl border p-6">
        {datasetsLoading ? (
          <div className="flex items-center gap-2 text-gray-400 py-10 justify-center">
            <Loader2 size={16} className="animate-spin" /> 加载中...
          </div>
        ) : approvedRows.length === 0 ? (
          /* ── 无已审批数据 ── */
          <div className="py-8 text-center space-y-3">
            <p className="text-sm text-gray-600 font-medium">暂无已审批的结构化数据</p>
            <p className="text-xs text-gray-400">请先在数据管理中运行 Pipeline 并审批生成的结构化数据，再回到此步骤。</p>
            {pendingRows.length > 0 && (
              <p className="text-xs text-amber-600">当前有 {pendingRows.length} 条待审批数据</p>
            )}
            <button
              onClick={() => setStep('approve_data')}
              className="inline-flex items-center gap-1.5 px-4 py-2 bg-black text-white text-sm rounded-lg hover:bg-gray-800"
            >
              <CheckCircle size={14} /> 前往审批结构化数据
            </button>
          </div>
        ) : (
          /* ── 有已审批数据，展示表格 ── */
          <>
            <div className="flex items-center justify-between mb-4">
              <p className="text-sm font-medium text-gray-700">选择已审批的 Curated Datasets</p>
              {pendingRows.length > 0 && (
                <button
                  onClick={() => setStep('approve_data')}
                  className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-800 border border-blue-200 rounded-lg px-2.5 py-1 hover:bg-blue-50"
                >
                  <Clock size={12} /> {pendingRows.length} 条待审批 → 前往审批
                </button>
              )}
            </div>

            {/* Filters */}
            <div className="flex gap-2 mb-3 flex-wrap">
              <div className="relative">
                <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
                <input value={pipelineFilter} onChange={e => setPipelineFilter(e.target.value)}
                  placeholder="按管道 ID / 名称筛选" className="pl-7 pr-6 py-1.5 border rounded-lg text-xs w-48" />
                {pipelineFilter && <button onClick={() => setPipelineFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400"><X size={11} /></button>}
              </div>
              <div className="relative">
                <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
                <input value={curatedFilter} onChange={e => setCuratedFilter(e.target.value)}
                  placeholder="按数据集名称 / ID 筛选" className="pl-7 pr-6 py-1.5 border rounded-lg text-xs w-48" />
                {curatedFilter && <button onClick={() => setCuratedFilter('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400"><X size={11} /></button>}
              </div>
              <span className="text-xs text-gray-400 self-center">共 {filteredApproved.length} 条</span>
            </div>

            {/* Table */}
            {filteredApproved.length === 0 ? (
              <p className="text-sm text-gray-400 py-4 text-center">没有匹配的记录</p>
            ) : (
              <div className="border rounded-xl overflow-hidden mb-4">
                <table className="w-full text-xs">
                  <thead className="bg-gray-50 border-b">
                    <tr>
                      <th className="w-8 px-3 py-2">
                        <button onClick={toggleSelectAll} className="flex items-center justify-center mx-auto">
                          {allFilteredSelected
                            ? <CheckSquare size={14} className="text-black" />
                            : <Square size={14} className="text-gray-300" />}
                        </button>
                      </th>
                      <th className="text-left px-3 py-2 font-medium text-gray-500">管道 ID</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-500">管道名称</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-500">业务域</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-500">结构数据集名称</th>
                      <th className="text-left px-3 py-2 font-medium text-gray-500">行数</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y">
                    {filteredApproved.map((row, idx) => {
                      const selected = selectedDatasetIds.has(row.curatedId)
                      return (
                        <tr
                          key={`${row.curatedId}-${idx}`}
                          onClick={() => toggleDataset(row.curatedId)}
                          className={`cursor-pointer transition-colors ${selected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                        >
                          <td className="px-3 py-2.5 text-center">
                            {selected
                              ? <CheckSquare size={14} className="text-black mx-auto" />
                              : <Square size={14} className="text-gray-300 mx-auto" />}
                          </td>
                          <td className="px-3 py-2.5 font-mono text-gray-400" title={row.pipelineId}>
                            {row.pipelineId ? row.pipelineId.slice(0, 8) : '—'}
                          </td>
                          <td className="px-3 py-2.5 text-gray-700 max-w-[140px] truncate">{row.pipelineName}</td>
                          <td className="px-3 py-2.5 text-gray-500">{row.domain}</td>
                          <td className="px-3 py-2.5 font-medium text-gray-800 max-w-[180px] truncate" title={row.curatedName}>
                            {row.curatedName}
                          </td>
                          <td className="px-3 py-2.5 text-gray-400 tabular-nums">
                            {row.rowCount != null ? row.rowCount.toLocaleString() : '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}

        <div className="flex justify-between pt-2 border-t mt-2">
          <span className="text-xs text-gray-400 self-center">已选 {selectedDatasetIds.size} 个数据集</span>
          <button
            onClick={handleGetSuggestions}
            disabled={selectedDatasetIds.size === 0 || suggestionsLoading}
            className="px-5 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2"
          >
            {suggestionsLoading && <Loader2 size={14} className="animate-spin" />}
            下一步：Mapping 配置
          </button>
        </div>
      </div>
    </div>
  )

  if (step === 'approve_data') return (
    <div>
      <button
        onClick={() => { loadDatasets(); setStep('select_datasets') }}
        className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-black mb-5"
      >
        <ArrowLeft size={14} /> 返回选择数据集
      </button>
      <h2 className="text-xl font-semibold mb-1">审批结构化数据</h2>
      <p className="text-sm text-gray-400 mb-5">批准数据后，可返回上一步继续选择数据集进行 Mapping。</p>

      {datasetsLoading ? (
        <div className="flex items-center gap-2 text-gray-400 py-10"><Loader2 size={16} className="animate-spin" /> 加载中...</div>
      ) : datasetRows.length === 0 ? (
        <div className="bg-white border rounded-xl p-10 text-center text-gray-400">
          <p className="text-sm">暂无结构化数据</p>
          <p className="text-xs mt-1">请先在数据管理中运行 Pipeline 生成数据集</p>
          <button onClick={() => navigate('/data/pipelines')} className="text-xs text-blue-600 hover:underline mt-3 inline-block">→ 前往数据管道</button>
        </div>
      ) : (
        <div className="bg-white border rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium text-gray-500 text-xs">管道名称</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-500 text-xs">业务域</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-500 text-xs">结构数据集名称</th>
                <th className="text-left px-4 py-2.5 font-medium text-gray-500 text-xs">状态</th>
                <th className="px-4 py-2.5 text-xs text-right">操作</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {datasetRows.map((row, idx) => (
                <tr key={`${row.curatedId}-${idx}`} className="hover:bg-gray-50">
                  <td className="px-4 py-3 text-gray-700 max-w-[160px] truncate">{row.pipelineName}</td>
                  <td className="px-4 py-3 text-xs text-gray-500">{row.domain}</td>
                  <td className="px-4 py-3 text-sm font-medium text-gray-800 max-w-[200px] truncate" title={row.curatedName}>
                    {row.curatedName}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded border ${STATUS_STYLE[row.curatedStatus] || 'bg-gray-100 text-gray-600 border-gray-200'}`}>
                      {STATUS_ICON(row.curatedStatus)}
                      {STATUS_LABEL[row.curatedStatus] || row.curatedStatus}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-right">
                    {(row.curatedStatus === 'pending_review' || !row.curatedStatus) && (
                      <button
                        onClick={() => handleApprove(row.curatedId)}
                        disabled={approvingId === row.curatedId}
                        className="inline-flex items-center gap-1 px-3 py-1 text-xs bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50"
                      >
                        {approvingId === row.curatedId ? <Loader2 size={11} className="animate-spin" /> : <CheckCircle size={11} />}
                        批准
                      </button>
                    )}
                    {row.curatedStatus === 'approved' && (
                      <span className="text-xs text-green-600 font-medium">✓ 已审批</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 flex justify-end">
        <button
          onClick={() => { loadDatasets(); setStep('select_datasets') }}
          className="px-5 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-2"
        >
          <ArrowLeft size={14} /> 返回选择数据集
        </button>
      </div>
    </div>
  )

  if (step === 'mapping_config') return (
    <div>
      <h2 className="text-xl font-semibold mb-1">Mapping 配置</h2>
      <p className="text-sm text-gray-400 mb-4">🔄 Pipeline Mapping — LLM 辅助建议，可修改后确认</p>
      <StepIndicator current={2} />
      <div className="space-y-4">
        {[...selectedDatasetIds].map(dsId => {
          const ds = datasets.find(d => d.id === dsId)
          const sug = suggestions[dsId]
          if (!sug || !ds) return null
          return (
            <div key={dsId} className="bg-white rounded-xl border p-5">
              <div className="flex items-center justify-between mb-3">
                <div>
                  <p className="text-sm font-medium">{ds.name}</p>
                  <p className="text-xs text-gray-400 mt-0.5">→ Entity Type</p>
                </div>
                <div className="text-right">
                  <input value={sug.entity_class}
                    onChange={e => setSuggestions(prev => ({ ...prev, [dsId]: { ...sug, entity_class: e.target.value } }))}
                    className="border rounded px-2 py-1 text-sm w-40 text-right" />
                  {sug.entity_class_cn && <p className="text-xs text-gray-400 mt-0.5">{sug.entity_class_cn}</p>}
                </div>
              </div>
              {sug.field_mappings.length > 0 && (
                <div className="border-t pt-3 space-y-1">
                  <p className="text-xs text-gray-500 mb-2">字段映射（列名 → 属性名）</p>
                  {sug.field_mappings.slice(0, 6).map((fm, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <span className="font-mono text-gray-500 w-32 truncate">{fm.column_name}</span>
                      <span className="text-gray-300">→</span>
                      <span className="font-mono text-gray-700">{fm.property_name}</span>
                    </div>
                  ))}
                  {sug.field_mappings.length > 6 && <p className="text-xs text-gray-400">+ {sug.field_mappings.length - 6} 个字段...</p>}
                </div>
              )}
              <div className="border-t pt-3 mt-3 grid grid-cols-1 md:grid-cols-3 gap-2 text-xs">
                <div className="bg-blue-50 border border-blue-100 rounded-lg p-2">
                  <p className="font-medium text-blue-700">Link Type 推断</p>
                  <p className="text-blue-600 mt-1">基于外键、值模式和 Link Mapping 生成，并推断 cardinality。</p>
                </div>
                <div className="bg-amber-50 border border-amber-100 rounded-lg p-2">
                  <p className="font-medium text-amber-700">Logic Discovery</p>
                  <p className="text-amber-600 mt-1">从 mapping、schema 质量、状态列和关系生成 draft 规则。</p>
                </div>
                <div className="bg-purple-50 border border-purple-100 rounded-lg p-2">
                  <p className="font-medium text-purple-700">Action Discovery</p>
                  <p className="text-purple-600 mt-1">从 Object Type、Link Type、Review 和 Writeback 生成 draft 动作。</p>
                </div>
              </div>
            </div>
          )
        })}
        <div className="flex justify-between">
          <button onClick={() => setStep('select_datasets')} className="px-4 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
            <ArrowLeft size={14} className="inline mr-1" /> 上一步
          </button>
          <button onClick={handleStartBuild} className="px-6 py-2 bg-black text-white rounded-lg text-sm flex items-center gap-2 hover:bg-gray-800">
            <Zap size={14} /> 开始构建
          </button>
        </div>
      </div>
    </div>
  )

  if (step === 'building') {
    const pct = Math.round(currentPhase / BUILD_PHASES.length * 100)
    return (
      <div className="max-w-xl mx-auto py-8">
        <h2 className="text-xl font-semibold mb-2 text-center">Ontology Mapping 进行中</h2>
        <p className="text-sm text-gray-400 text-center mb-6">{createdOntologyId?.slice(0, 8)}</p>
        <div className="mb-6">
          <div className="flex justify-between text-xs text-gray-500 mb-1"><span>进度</span><span>{pct}%</span></div>
          <div className="w-full bg-gray-100 rounded-full h-2.5">
            <div className="bg-black h-2.5 rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
          </div>
        </div>
        <div className="space-y-2">
          {BUILD_PHASES.map((phase, i) => {
            const st = phaseStatus[i]
            return (
              <div key={phase.key} className={`flex items-center gap-3 p-3.5 rounded-xl border transition-colors
                ${st === 'done' ? 'bg-green-50 border-green-200' :
                  st === 'running' ? 'bg-blue-50 border-blue-200' :
                  st === 'failed' ? 'bg-red-50 border-red-200' : 'bg-gray-50 border-gray-100 text-gray-400'}`}>
                <div className="w-7 h-7 rounded-full flex items-center justify-center text-sm">
                  {st === 'done' ? <CheckCircle size={18} className="text-green-500" /> :
                   st === 'running' ? <Loader2 size={16} className="text-blue-500 animate-spin" /> :
                   st === 'failed' ? <XCircle size={16} className="text-red-500" /> :
                   <span className="text-gray-300 text-xs">{phase.icon}</span>}
                </div>
                <div className="flex-1">
                  <p className={`text-sm font-medium ${st === 'done' ? 'text-green-700' : st === 'running' ? 'text-blue-700' : ''}`}>{phase.label}</p>
                  <p className="text-xs text-gray-400">{st === 'done' ? '完成' : st === 'running' ? '进行中...' : st === 'failed' ? '失败' : '等待中'}</p>
                </div>
                {st === 'done' && <span className="text-green-500 text-xs">✅</span>}
              </div>
            )
          })}
        </div>

        {buildError && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-xs text-red-600">{buildError}</p>
            <button onClick={() => navigate(`/ontologies/${createdOntologyId}`)} className="text-xs text-blue-600 hover:underline mt-2">查看本体</button>
          </div>
        )}

        {buildDone && (
          <div className="mt-5 p-5 bg-white border rounded-xl">
            <p className="text-sm font-semibold text-gray-800 mb-1">🎉 构建完成</p>
            <p className="text-xs text-gray-500 mb-3">
              实体、关系、Logic 与 Actions 已生成。你可以在本体详情页自由查看和修改每一项。
            </p>
            {buildResult && (
              <div className="grid grid-cols-4 gap-2 mb-4 text-xs">
                <div className="bg-gray-50 rounded-lg p-2 text-center"><p className="text-gray-400">实体</p><p className="font-bold text-base">{buildResult.total_entities || 0}</p></div>
                <div className="bg-gray-50 rounded-lg p-2 text-center"><p className="text-gray-400">关系</p><p className="font-bold text-base">{buildResult.total_relations || 0}</p></div>
                <div className="bg-gray-50 rounded-lg p-2 text-center"><p className="text-gray-400">逻辑规则</p><p className="font-bold text-base">{buildResult.total_logic || 0}</p></div>
                <div className="bg-gray-50 rounded-lg p-2 text-center"><p className="text-gray-400">动作</p><p className="font-bold text-base">{buildResult.total_actions || 0}</p></div>
              </div>
            )}
            <button
              onClick={() => navigate(`/ontologies/${createdOntologyId}`)}
              className="w-full px-4 py-2.5 bg-black text-white rounded-lg text-sm font-medium hover:bg-gray-800 flex items-center justify-center gap-2"
            >
              进入本体详情 <ArrowRight size={14} />
            </button>
          </div>
        )}
      </div>
    )
  }

  return null
}
