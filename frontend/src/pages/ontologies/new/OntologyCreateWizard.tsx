import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi } from '@/api/ontologies'
import { apiClientV2 } from '@/api/client'
import { DOMAINS } from '@/types/ontology'
import { Zap, GitBranch, ArrowLeft, ArrowRight, Loader2, CheckSquare, Square, CheckCircle, XCircle } from 'lucide-react'

type Mode = 'simple_llm' | 'pipeline_mapping'
type Step = 'select_mode' | 'fill_info' | 'select_datasets' | 'mapping_config' | 'building'

interface CuratedDataset {
  id: string; name: string; status: string; row_count: number | null; quality_score: number | null
  columns?: string[]
  sample_rows?: Record<string, unknown>[]
}
interface MappingSuggestion {
  entity_class: string; entity_class_cn: string; primary_key_column: string
  field_mappings: { column_name: string; property_name: string }[]
}

const BUILD_PHASES = [
  { key: 'entity', label: '① Entity Type 识别', icon: '🧩' },
  { key: 'property', label: '② Property Mapping', icon: '📋' },
  { key: 'relation', label: '③ Relation 推断', icon: '🔗' },
  { key: 'logic', label: '④ Logic Discovery', icon: '⚖️' },
  { key: 'action', label: '⑤ Action Discovery', icon: '⚡' },
  { key: 'review', label: '⑥ Human Review', icon: '👁️' },
  { key: 'neo4j', label: '⑦ 写入 Neo4j', icon: '🕸️' },
  { key: 'chroma', label: '⑧ 写入 ChromaDB', icon: '📊' },
  { key: 'publish', label: '⑨ 发布 Logic/Actions', icon: '🚀' },
]

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

  const [datasets, setDatasets] = useState<CuratedDataset[]>([])
  const [selectedDatasetIds, setSelectedDatasetIds] = useState<Set<string>>(new Set())
  const [datasetsLoading, setDatasetsLoading] = useState(false)
  const [createdOntologyId, setCreatedOntologyId] = useState<string | null>(null)
  const [suggestions, setSuggestions] = useState<Record<string, MappingSuggestion>>({})
  const [suggestionsLoading, setSuggestionsLoading] = useState(false)

  const [currentPhase, setCurrentPhase] = useState(0)
  const [phaseStatus, setPhaseStatus] = useState<string[]>(Array(9).fill('pending'))
  const [buildResult, setBuildResult] = useState<any>(null)
  const [buildError, setBuildError] = useState('')
  const [readyForReview, setReadyForReview] = useState(false)

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

  useEffect(() => {
    if (step !== 'select_datasets') return
    setDatasetsLoading(true)
    apiClientV2.get('/curated')
      .then((res: any) => setDatasets(Array.isArray(res) ? res : []))
      .catch(() => setDatasets([]))
      .finally(() => setDatasetsLoading(false))
  }, [step])

  const toggleDataset = (id: string) => {
    setSelectedDatasetIds(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  const handleGetSuggestions = async () => {
    if (!createdOntologyId || selectedDatasetIds.size === 0) return
    setSuggestionsLoading(true)
    const next: Record<string, MappingSuggestion> = {}
    for (const dsId of selectedDatasetIds) {
      const ds = datasets.find(d => d.id === dsId)
      if (!ds) continue
      try {
        const res: any = await apiClientV2.post(`/ontologies/${createdOntologyId}/mappings/suggest`, {
          dataset_name: ds.name, columns: ds.columns || [], sample_rows: ds.sample_rows || [], ontology_domain: domain,
        })
        next[dsId] = res
      } catch {
        next[dsId] = { entity_class: ds.name, entity_class_cn: ds.name, primary_key_column: 'id', field_mappings: [] }
      }
    }
    setSuggestions(next); setSuggestionsLoading(false); setStep('mapping_config')
  }

  const handleStartBuild = async () => {
    if (!createdOntologyId) return
    setStep('building'); setPhaseStatus(Array(9).fill('pending')); setReadyForReview(false)

    for (const [dsId, sug] of Object.entries(suggestions)) {
      const fmap: Record<string, string> = { __primary_key__: sug.primary_key_column }
      for (const fm of sug.field_mappings) fmap[fm.column_name] = fm.property_name
      await apiClientV2.post(`/ontologies/${createdOntologyId}/mappings`, {
        curated_dataset_id: dsId, entity_class: sug.entity_class, field_mapping: fmap, confidence: 1.0,
      }).catch(() => {})
    }

    // 阶段 1-2 (快速完成)
    setPhaseStatus(['running', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending']); await sleep(400)
    setPhaseStatus(['done', 'running', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending']); await sleep(400)
    setPhaseStatus(['done', 'done', 'running', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending']); setCurrentPhase(2)

    // 阶段 3: build-all (Relation + Neo4j + ChromaDB)
    try {
      setPhaseStatus(['done', 'done', 'running', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending'])
      const res: any = await apiClientV2.post(`/ontologies/${createdOntologyId}/mappings/build-all`)
      setBuildResult(res)
      await sleep(300)
      setPhaseStatus(['done', 'done', 'done', 'running', 'pending', 'pending', 'pending', 'pending', 'pending']); setCurrentPhase(3); await sleep(500)
      setPhaseStatus(['done', 'done', 'done', 'done', 'running', 'pending', 'pending', 'pending', 'pending']); setCurrentPhase(4); await sleep(500)
      setPhaseStatus(['done', 'done', 'done', 'done', 'done', 'running', 'done', 'done', 'pending']); setCurrentPhase(5)
      setReadyForReview(true)
    } catch (e: any) {
      setPhaseStatus(['done', 'done', 'failed', 'idle', 'idle', 'idle', 'idle', 'idle', 'idle'])
      setBuildError(e?.message || e?.detail || '构建失败')
    }
  }

  function sleep(ms: number) { return new Promise(r => setTimeout(r, ms)) }

  if (step === 'select_mode') {
    return (
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
  }

  if (step === 'fill_info') {
    return (
      <div className="max-w-xl">
        <button onClick={() => setStep('select_mode')} className="flex items-center gap-1.5 text-sm text-gray-500 hover:text-black mb-6">
          <ArrowLeft size={14} /> 返回选择方式
        </button>
        <h2 className="text-xl font-semibold mb-1">新建本体</h2>
        <p className="text-sm text-gray-400 mb-2">{mode === 'simple_llm' ? '⚡ 简易 LLM 提取' : '🔄 Pipeline Mapping'}</p>
        {mode === 'pipeline_mapping' && (
          <div className="flex gap-2 mb-6 text-xs">
            {['基本信息', '选择数据集', 'Mapping 配置'].map((s, i) => (
              <div key={s} className="flex items-center gap-1.5">
                <span className={`w-5 h-5 rounded-full flex items-center justify-center font-medium ${i === 0 ? 'bg-black text-white' : 'bg-gray-100 text-gray-400'}`}>{i + 1}</span>
                <span className={i === 0 ? 'text-black' : 'text-gray-400'}>{s}</span>
                {i < 2 && <span className="text-gray-300 mx-1">›</span>}
              </div>
            ))}
          </div>
        )}
        <div className="bg-white rounded-xl border p-6 space-y-4">
          <div><label className="block text-xs font-medium text-gray-600 mb-1">名称 *</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="本体名称" className="w-full border rounded-lg px-3 py-2 text-sm" /></div>
          <div><label className="block text-xs font-medium text-gray-600 mb-1">领域 *</label>
            <select value={domain} onChange={e => setDomain(e.target.value)} className="w-full border rounded-lg px-3 py-2 text-sm">{DOMAINS.map(d => <option key={d}>{d}</option>)}</select></div>
          <div><label className="block text-xs font-medium text-gray-600 mb-1">描述（可选）</label>
            <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={2} placeholder="简要描述本体用途" className="w-full border rounded-lg px-3 py-2 text-sm resize-none" /></div>
          {error && <p className="text-red-500 text-xs">{error}</p>}
          <div className="flex justify-between pt-2">
            <button onClick={() => setStep('select_mode')} className="px-4 py-2 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">上一步</button>
            <button onClick={() => createMut.mutate()} disabled={!name || createMut.isPending}
              className="px-5 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2">
              {createMut.isPending && <Loader2 size={14} className="animate-spin" />}{mode === 'pipeline_mapping' ? '下一步' : '创建本体'}
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (step === 'select_datasets') {
    return (
      <div>
        <h2 className="text-xl font-semibold mb-1">选择数据集</h2>
        <p className="text-sm text-gray-400 mb-4">🔄 Pipeline Mapping</p>
        <div className="flex gap-2 mb-6 text-xs">
          {['基本信息', '选择数据集', 'Mapping 配置'].map((s, i) => (
            <div key={s} className="flex items-center gap-1.5">
              <span className={`w-5 h-5 rounded-full flex items-center justify-center font-medium ${i === 0 ? 'bg-green-500 text-white' : i === 1 ? 'bg-black text-white' : 'bg-gray-100 text-gray-400'}`}>{i + 1}</span>
              <span className={i === 1 ? 'text-black' : 'text-gray-400'}>{s}</span>
              {i < 2 && <span className="text-gray-300 mx-1">›</span>}
            </div>
          ))}
        </div>
        <div className="bg-white rounded-xl border p-6">
          <p className="text-sm font-medium text-gray-700 mb-3">选择已审批的 Curated Datasets</p>
          {datasetsLoading ? (
            <div className="flex items-center gap-2 text-gray-400 py-8 justify-center"><Loader2 size={16} className="animate-spin" /> 加载中...</div>
          ) : datasets.length === 0 ? (
            <div className="text-center py-8 text-gray-400">
              <p className="text-sm">暂无 Curated Dataset</p>
              <p className="text-xs mt-1">请先在 Pipeline 中运行管道生成数据集并审批</p>
              <button onClick={() => navigate('/pipelines')} className="text-xs text-blue-600 hover:underline mt-2 inline-block">→ 前往 Pipeline 列表</button>
            </div>
          ) : (
            <div className="space-y-2">
              {datasets.filter(d => d.status === 'approved' || d.status === 'active').map(ds => (
                <button key={ds.id} onClick={() => toggleDataset(ds.id)}
                  className={`w-full text-left flex items-center gap-3 p-3 rounded-lg border transition-all ${selectedDatasetIds.has(ds.id) ? 'border-black bg-gray-50' : 'border-gray-200 hover:border-gray-400'}`}>
                  {selectedDatasetIds.has(ds.id) ? <CheckSquare size={16} className="text-black flex-shrink-0" /> : <Square size={16} className="text-gray-300 flex-shrink-0" />}
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{ds.name}</span>
                      <span className="text-xs px-1.5 py-0.5 rounded border border-green-200 bg-green-50 text-green-700">已审批</span>
                    </div>
                    <div className="text-xs text-gray-400 mt-0.5">
                      {ds.row_count != null ? `${ds.row_count.toLocaleString()} 行` : ''}
                      {ds.quality_score != null ? ` · 质量 ${(ds.quality_score * 100).toFixed(0)}%` : ''}
                    </div>
                  </div>
                </button>
              ))}
            </div>
          )}
          <div className="flex justify-between pt-4 border-t mt-4">
            <span className="text-xs text-gray-400 self-center">已选 {selectedDatasetIds.size} 个数据集</span>
            <button onClick={handleGetSuggestions} disabled={selectedDatasetIds.size === 0 || suggestionsLoading}
              className="px-5 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2">
              {suggestionsLoading && <Loader2 size={14} className="animate-spin" />}下一步：Mapping 配置
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (step === 'mapping_config') {
    return (
      <div>
        <h2 className="text-xl font-semibold mb-1">Mapping 配置</h2>
        <p className="text-sm text-gray-400 mb-4">🔄 Pipeline Mapping — LLM 辅助建议，可修改后确认</p>
        <div className="flex gap-2 mb-6 text-xs">
          {['基本信息', '选择数据集', 'Mapping 配置'].map((s, i) => (
            <div key={s} className="flex items-center gap-1.5">
              <span className={`w-5 h-5 rounded-full flex items-center justify-center font-medium ${i < 2 ? 'bg-green-500 text-white' : 'bg-black text-white'}`}>{i + 1}</span>
              <span className={i === 2 ? 'text-black' : 'text-gray-400'}>{s}</span>
              {i < 2 && <span className="text-gray-300 mx-1">›</span>}
            </div>
          ))}
        </div>
        <div className="space-y-4">
          {[...selectedDatasetIds].map(dsId => {
            const ds = datasets.find(d => d.id === dsId)
            const sug = suggestions[dsId]
            if (!sug || !ds) return null
            return (
              <div key={dsId} className="bg-white rounded-xl border p-5">
                <div className="flex items-center justify-between mb-3">
                  <div><p className="text-sm font-medium">{ds.name}</p><p className="text-xs text-gray-400 mt-0.5">→ Entity Type</p></div>
                  <div className="text-right">
                    <input value={sug.entity_class} onChange={e => setSuggestions(prev => ({ ...prev, [dsId]: { ...sug, entity_class: e.target.value } }))}
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
                    <p className="text-blue-600 mt-1">基于外键、值模式和手动 Link Mapping 生成，并推断 cardinality。</p>
                  </div>
                  <div className="bg-amber-50 border border-amber-100 rounded-lg p-2">
                    <p className="font-medium text-amber-700">Logic Discovery</p>
                    <p className="text-amber-600 mt-1">从 mapping、schema 质量、状态列和关系生成 draft 规则。</p>
                  </div>
                  <div className="bg-purple-50 border border-purple-100 rounded-lg p-2">
                    <p className="font-medium text-purple-700">Action Discovery</p>
                    <p className="text-purple-600 mt-1">从 Object Type、Link Type、Review、Repair 和 Writeback 生成 draft 动作。</p>
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
  }

  if (step === 'building') {
    const pct = Math.round(currentPhase / 9 * 100)
    return (
      <div className="max-w-xl mx-auto py-8">
        <h2 className="text-xl font-semibold mb-2 text-center">Ontology Mapping 进行中</h2>
        <p className="text-sm text-gray-400 text-center mb-6">{createdOntologyId ? createdOntologyId.slice(0, 8) : ''}</p>
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
              <div key={phase.key} className={`flex items-center gap-3 p-3.5 rounded-xl border transition-colors ${
                st === 'done' ? 'bg-green-50 border-green-200' :
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
                {st === 'done' && <span className="text-green-500 text-xs font-medium">✅</span>}
              </div>
            )
          })}
        </div>
        {buildError && (
          <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-xs text-red-600">{buildError}</p>
            <button onClick={() => navigate(`/ontologies/${createdOntologyId}?tab=info`)} className="text-xs text-blue-600 hover:underline mt-2">查看本体</button>
          </div>
        )}
        {readyForReview && (
          <div className="mt-4 p-4 bg-white border rounded-xl">
            <p className="text-sm font-medium text-gray-800">已生成待审核定义</p>
            <p className="text-xs text-gray-500 mt-1">
              Object / Link 已写入，Logic 与 Actions 以 draft 状态生成。请进入详情页审核启用状态，再分别发布 Logic / Actions。
            </p>
            {buildResult && (
              <div className="grid grid-cols-4 gap-2 mt-3 text-xs">
                <div className="bg-gray-50 rounded p-2"><p className="text-gray-400">Objects</p><p className="font-semibold">{buildResult.total_entities || 0}</p></div>
                <div className="bg-gray-50 rounded p-2"><p className="text-gray-400">Links</p><p className="font-semibold">{buildResult.total_relations || 0}</p></div>
                <div className="bg-gray-50 rounded p-2"><p className="text-gray-400">Logic</p><p className="font-semibold">{buildResult.total_logic || 0}</p></div>
                <div className="bg-gray-50 rounded p-2"><p className="text-gray-400">Actions</p><p className="font-semibold">{buildResult.total_actions || 0}</p></div>
              </div>
            )}
            <div className="flex gap-2 mt-4">
              <button onClick={() => navigate(`/ontologies/${createdOntologyId}?tab=logic`)} className="px-4 py-2 bg-black text-white rounded-lg text-sm">审核 Logic</button>
              <button onClick={() => navigate(`/ontologies/${createdOntologyId}?tab=actions`)} className="px-4 py-2 border rounded-lg text-sm">审核 Actions</button>
            </div>
          </div>
        )}
      </div>
    )
  }

  return null
}
