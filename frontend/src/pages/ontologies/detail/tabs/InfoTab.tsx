import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { ontologyApi, promptApi, modelApi } from '@/api/ontologies'
import { CheckCircle, XCircle, Loader2, ChevronRight, AlertTriangle, AlertCircle, Info } from 'lucide-react'
import type { OntologyDetail } from '@/types/ontology'
import { loadRuleStates, getActiveConstraints } from '@/utils/extractionRules'

const SEVERITY_CONFIG = {
  fatal:   { label: 'FATAL',   bg: 'bg-red-50',    border: 'border-red-200',   text: 'text-red-700',   icon: XCircle },
  error:   { label: 'ERROR',   bg: 'bg-red-50',    border: 'border-red-200',   text: 'text-red-600',   icon: AlertCircle },
  warning: { label: 'WARNING', bg: 'bg-amber-50',  border: 'border-amber-200', text: 'text-amber-700', icon: AlertTriangle },
  info:    { label: 'INFO',    bg: 'bg-blue-50',   border: 'border-blue-200',  text: 'text-blue-700',  icon: Info },
}

function ValidationReportCard({ report }: { report: any }) {
  const { t } = useTranslation()
  if (!report) return null
  const bySeverity = report.by_severity ?? {}
  const allEmpty = Object.values(bySeverity).every((arr: any) => arr.length === 0)
  const overallOk = !report.has_fatal && !report.has_errors

  return (
    <div className={`bg-white rounded-xl border p-6 ${report.has_fatal ? 'border-red-300' : report.has_errors ? 'border-amber-300' : 'border-green-200'}`}>
      <div className="flex items-center gap-2 mb-4">
        <h3 className="font-semibold">{t('extract.quality_report')}</h3>
        {overallOk && !allEmpty ? (
          <span className="ml-auto text-xs bg-green-50 border border-green-200 text-green-700 px-2 py-0.5 rounded-full flex items-center gap-1">
            <CheckCircle size={11} /> {t('extract.quality_pass')}
          </span>
        ) : overallOk && allEmpty ? (
          <span className="ml-auto text-xs bg-green-50 border border-green-200 text-green-700 px-2 py-0.5 rounded-full flex items-center gap-1">
            <CheckCircle size={11} /> {t('extract.quality_perfect')}
          </span>
        ) : (
          <span className="ml-auto text-xs bg-red-50 border border-red-200 text-red-600 px-2 py-0.5 rounded-full">
            {t('extract.issues_count', { count: report.total_issues })}
          </span>
        )}
      </div>

      {allEmpty ? (
        <p className="text-sm text-gray-400">{t('extract.no_issues')}</p>
      ) : (
        <div className="space-y-3">
          {(['fatal', 'error', 'warning', 'info'] as const).map(sev => {
            const issues = bySeverity[sev] ?? []
            if (!issues.length) return null
            const cfg = SEVERITY_CONFIG[sev]
            const Icon = cfg.icon
            return (
              <div key={sev} className={`rounded-lg border ${cfg.border} ${cfg.bg} p-3`}>
                <p className={`text-xs font-semibold ${cfg.text} mb-1.5`}>{cfg.label} · {issues.length} 项</p>
                <ul className="space-y-1">
                  {issues.map((issue: any, i: number) => (
                    <li key={i} className={`flex items-start gap-1.5 text-xs ${cfg.text}`}>
                      <Icon size={11} className="mt-0.5 flex-shrink-0" />
                      <span>{issue.message}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

const STAGE_KEYS = [
  { key: 'queued',               i18nKey: 'extract.stage_queued' },
  { key: 'loading files',        i18nKey: 'extract.stage_loading' },
  { key: 'calling LLM',         i18nKey: 'extract.stage_llm' },
  { key: 'validating output',   i18nKey: 'extract.stage_validating' },
  { key: 'inferring relations', i18nKey: 'extract.stage_inferring' },
  { key: 'saving results',      i18nKey: 'extract.stage_saving' },
  { key: 'done',                 i18nKey: 'extract.stage_done' },
]

const STAGE_PCT: Record<string, number> = {
  queued: 0, 'loading files': 10, 'calling LLM': 40,
  'validating output': 65, 'inferring relations': 75, 'saving results': 85, done: 100,
}

const lastTaskKey = (oid: string) => `ontoprompt_last_task_${oid}`

type SavedTask = { task_id?: string; status?: string; [key: string]: unknown }

function loadSavedTask(oid: string): SavedTask | null {
  try {
    const saved = localStorage.getItem(lastTaskKey(oid))
    return saved ? JSON.parse(saved) : null
  } catch {
    return null
  }
}

function saveTask(oid: string, data: SavedTask) {
  try {
    localStorage.setItem(lastTaskKey(oid), JSON.stringify(data))
  } catch {}
}

function StructuredDataLink() {
  const navigate = useNavigate()
  return (
    <button
      onClick={() => navigate('/data/structured')}
      className="text-xs text-blue-600 hover:underline"
    >
      → 查看结构数据
    </button>
  )
}
function PipelineMappingInfo({ ontology }: { ontology: OntologyDetail }) {
  const [mappings, setMappings] = useState<any[]>([])
  useEffect(() => {
    import('@/api/client').then(({ apiClientV2 }) => {
      apiClientV2.get(`/ontologies/${ontology.id}/mappings`)
        .then((res: any) => setMappings(Array.isArray(res) ? res : []))
        .catch(() => setMappings([]))
    })
  }, [ontology.id])

  return (
    <div className="bg-white rounded-xl border p-6 space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold">Pipeline Mapping 状态</span>
        <span className="px-2 py-0.5 rounded text-xs bg-blue-50 border border-blue-200 text-blue-700">🔄 Pipeline 模式</span>
      </div>
      {mappings.length === 0 ? (
        <p className="text-sm text-gray-400">暂无 Mapping 配置。请先在 Pipelines → Curated Datasets 中审批数据，然后在新建本体时配置 Mapping。</p>
      ) : (
        <div className="space-y-2">
          {mappings.map((m: any) => (
            <div key={m.mapping_id || m.id} className="border rounded-lg px-3 py-2 text-sm flex items-center justify-between">
              <div>
                <span className="font-medium">{m.entity_class}</span>
                {m.entity_class_cn && <span className="text-gray-400 ml-2">({m.entity_class_cn})</span>}
              </div>
              <span className={`text-xs px-1.5 py-0.5 rounded border ${m.status === 'active' ? 'border-green-200 bg-green-50 text-green-700' : 'border-gray-200 text-gray-500'}`}>
                {m.status || 'draft'}
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="pt-2 border-t flex gap-2">
        <StructuredDataLink />
      </div>
    </div>
  )
}

export default function InfoTab({ ontology }: { ontology: OntologyDetail }) {
  const { t, i18n } = useTranslation()
  const qc = useQueryClient()
  const pollRef = useRef<(() => void) | null>(null)
  const [promptId, setPromptId] = useState('')
  const [modelId, setModelId] = useState('')
  const [modelName, setModelName] = useState('')
  const [pollTimedOut, setPollTimedOut] = useState(false)
  const [taskStatus, setTaskStatus] = useState<any>(() => loadSavedTask(ontology.id))
  const [exportingFormat, setExportingFormat] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  const handleExport = async (format: string) => {
    setExportError(null)
    setExportingFormat(format)
    try {
      await ontologyApi.exportOntology(ontology.id, format)
    } catch (err: any) {
      setExportError(err?.detail ?? err?.message ?? t('extract.export_failed', 'Export failed'))
    } finally {
      setExportingFormat(null)
    }
  }

  const { data: prompts } = useQuery({ queryKey: ['prompts'], queryFn: () => promptApi.list() as any })
  const { data: models } = useQuery({ queryKey: ['models'], queryFn: () => modelApi.list() as any })
  const { data: files = [] } = useQuery({
    queryKey: ['files', ontology.id],
    queryFn: () => ontologyApi.listFiles(ontology.id) as any,
  })

  const extractMut = useMutation({
    mutationFn: (constraints: string[]) =>
      ontologyApi.startExtraction(ontology.id, {
        prompt_id: promptId,
        model_id: modelId,
        model_name: modelName,
        constraints,
      }),
  })

  const startPoll = (taskId: string) => {
    pollRef.current?.()
    setPollTimedOut(false)
    let attempts = 0
    let cancelled = false
    pollRef.current = () => { cancelled = true }

    const poll = async () => {
      if (cancelled) return
      // 2s interval × 600 ≈ 20 min — local LLM extraction can exceed 5 min
      if (attempts++ > 600) {
        setPollTimedOut(true)
        return
      }
      try {
        const status: any = await ontologyApi.getExtractionStatus(ontology.id, taskId)
        if (cancelled) return
        const merged = { ...status, task_id: taskId }
        setTaskStatus(merged)
        saveTask(ontology.id, merged)
        if (status.status === 'completed' || status.status === 'failed') {
          setPollTimedOut(false)
          qc.invalidateQueries({ queryKey: ['ontology', ontology.id] })
          qc.invalidateQueries({ queryKey: ['stats'] })
          qc.invalidateQueries({ queryKey: ['entities', ontology.id] })
          qc.invalidateQueries({ queryKey: ['logic', ontology.id] })
          qc.invalidateQueries({ queryKey: ['actions', ontology.id] })
          qc.invalidateQueries({ queryKey: ['graph', ontology.id] })
          return
        }
        setTimeout(poll, 2000)
      } catch {
        if (!cancelled) setTimeout(poll, 3000)
      }
    }
    poll()
  }

  // Resume polling if user refreshed while a task was still running
  useEffect(() => {
    const saved = loadSavedTask(ontology.id)
    if (saved?.task_id && saved.status !== 'completed' && saved.status !== 'failed') {
      startPoll(saved.task_id)
    }
    return () => { pollRef.current?.() }
  }, [ontology.id])

  const handleExtract = async () => {
    setPollTimedOut(false)
    setTaskStatus({ status: 'running', progress: { stage: 'queued', pct: 0 }, error: null } as any)
    const constraints = getActiveConstraints(loadRuleStates())
    try {
      const res: any = await extractMut.mutateAsync(constraints)
      saveTask(ontology.id, { status: 'running', progress: { stage: 'queued', pct: 0 }, task_id: res.task_id })
      startPoll(res.task_id)
    } catch (e: any) {
      setTaskStatus({
        status: 'failed',
        progress: { stage: 'error', pct: 0 },
        error: String(e?.detail || e?.message || e),
      } as any)
    }
  }

  const selectedModel = (models as any[] | undefined)?.find((m: any) => m.id === modelId)
  const activeConstraints = getActiveConstraints(loadRuleStates())
  const fileList = files as any[]
  const isExtracting = taskStatus && taskStatus.status !== 'completed' && taskStatus.status !== 'failed'
  const currentPct = taskStatus?.progress?.pct ?? 0
  const currentStage = taskStatus?.progress?.stage ?? ''

  const isPipelineMode = ontology.build_mode === 'pipeline_mapping'

  return (
    <div className="space-y-5">
      {/* Basic Info */}
      <div className="bg-white rounded-xl border p-6">
        <h3 className="font-semibold mb-4">{t('ontology.tabs.info')}</h3>
        <dl className="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
          <div><dt className="text-xs text-gray-500 mb-0.5">{t('ontology.name')}</dt><dd className="font-medium">{ontology.name}</dd></div>
          <div><dt className="text-xs text-gray-500 mb-0.5">{t('ontology.domain')}</dt><dd>{ontology.domain}</dd></div>
          <div><dt className="text-xs text-gray-500 mb-0.5">{t('ontology.version')}</dt><dd className="font-mono">{ontology.version}</dd></div>
          <div><dt className="text-xs text-gray-500 mb-0.5">{t('ontology.status')}</dt><dd>{ontology.status}</dd></div>
          <div>
            <dt className="text-xs text-gray-500 mb-0.5">构建方式</dt>
            <dd>
              {isPipelineMode
                ? <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-blue-50 border border-blue-200 text-blue-700">🔄 Pipeline Mapping</span>
                : <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-amber-50 border border-amber-200 text-amber-700">⚡ 简易 LLM 提取</span>
              }
            </dd>
          </div>
          {ontology.description && (
            <div className="col-span-2"><dt className="text-xs text-gray-500 mb-0.5">{t('ontology.desc_optional')}</dt><dd className="text-gray-700">{ontology.description}</dd></div>
          )}
        </dl>
      </div>

      {/* Pipeline Mapping 状态（仅 pipeline_mapping 模式显示） */}
      {isPipelineMode && <PipelineMappingInfo ontology={ontology} />}

      {/* LLM Config（仅简易模式显示） */}
      {!isPipelineMode && <div className="bg-white rounded-xl border p-6">
        <div className="flex items-center gap-2 mb-4">
          <h3 className="font-semibold">{t('extract.llm_config')}</h3>
          {activeConstraints.length > 0 && (
            <span className="ml-auto text-xs bg-amber-50 border border-amber-200 text-amber-700 px-2 py-0.5 rounded-full">
              {t('extract.constraints_active', { count: activeConstraints.length })}
            </span>
          )}
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('extract.prompt_label')}</label>
            <select value={promptId} onChange={e => setPromptId(e.target.value)}
              className="w-full border rounded-lg px-3 py-2 text-sm">
              <option value="">{t('extract.select_prompt')}</option>
              {(prompts as any[] || []).map((p: any) => (
                <option key={p.id} value={p.id}>{p.name}（{p.domain}）</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('extract.model_label')}</label>
            <select value={modelId} onChange={e => { setModelId(e.target.value); setModelName('') }}
              className="w-full border rounded-lg px-3 py-2 text-sm">
              <option value="">{t('extract.select_model')}</option>
              {(models as any[] || []).map((m: any) => (
                <option key={m.id} value={m.id}>{m.name}（{m.provider}）</option>
              ))}
            </select>
          </div>

          {selectedModel && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('extract.model_specific')}</label>
              <select value={modelName} onChange={e => setModelName(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm">
                <option value="">{t('extract.select')}</option>
                {(selectedModel.models || []).map((m: string) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          )}

          <div className="pt-1 flex items-center gap-3">
            <button
              onClick={handleExtract}
              disabled={!promptId || !modelId || !modelName || extractMut.isPending || isExtracting || fileList.length === 0}
              className="px-5 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2">
              {isExtracting && <Loader2 size={14} className="animate-spin" />}
              {isExtracting ? t('extract.extracting') : t('extract.start')}
            </button>
            {fileList.length === 0 && (
              <span className="text-xs text-gray-400">{t('extract.need_files')}</span>
            )}
          </div>
        </div>
      </div>}

      {/* Extraction Progress */}
      {!isPipelineMode && taskStatus && (
        <div className={`bg-white rounded-xl border p-6 ${taskStatus.status === 'failed' ? 'border-red-200 bg-red-50' : ''}`}>
          <h3 className="font-semibold mb-4">{t('extract.progress')}</h3>

          {taskStatus.status === 'failed' ? (
            <div className="flex items-start gap-2 text-red-600">
              <XCircle size={16} className="mt-0.5 flex-shrink-0" />
              <div>
                <p className="text-sm font-medium">{t('extract.failed')}</p>
                <p className="text-xs mt-0.5 text-red-500">{taskStatus.error}</p>
              </div>
            </div>
          ) : (
            <>
              {/* Stage steps */}
              <div className="flex items-center mb-5 overflow-x-auto pb-1">
                {STAGE_KEYS.map((stage, i) => {
                  const stagePct = STAGE_PCT[stage.key] ?? 0
                  const passed = currentPct >= stagePct
                  const done = taskStatus.status === 'completed'
                  return (
                    <div key={stage.key} className="flex items-center flex-shrink-0">
                      <div className="flex flex-col items-center gap-1">
                        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium transition-colors ${
                          passed
                            ? done ? 'bg-green-500 text-white' : 'bg-black text-white'
                            : 'bg-gray-100 text-gray-400'
                        }`}>
                          {passed && done ? <CheckCircle size={14} /> : i + 1}
                        </div>
                        <span className={`text-xs whitespace-nowrap ${passed ? 'text-gray-700' : 'text-gray-400'}`}>
                          {t(stage.i18nKey)}
                        </span>
                      </div>
                      {i < STAGE_KEYS.length - 1 && (
                        <ChevronRight size={14} className="text-gray-300 mx-2 flex-shrink-0 mb-4" />
                      )}
                    </div>
                  )
                })}
              </div>

              {/* Progress bar */}
              <div className="w-full bg-gray-100 rounded-full h-1.5">
                <div
                  className={`h-1.5 rounded-full transition-all duration-700 ${
                    taskStatus.status === 'completed' ? 'bg-green-500' : 'bg-black'
                  }`}
                  style={{ width: `${currentPct}%` }}
                />
              </div>
              <p className="text-xs text-gray-400 mt-1.5">{currentPct}%{currentStage ? ` · ${currentStage}` : ''}</p>
              {pollTimedOut && isExtracting && taskStatus?.task_id && (
                <div className="mt-3 flex items-center gap-2">
                  <p className="text-xs text-amber-600">{t('extract.poll_timeout')}</p>
                  <button
                    type="button"
                    onClick={() => startPoll(taskStatus.task_id)}
                    className="text-xs px-2 py-1 border border-amber-200 rounded text-amber-700 hover:bg-amber-50"
                  >
                    {t('extract.resume_poll')}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Validation Report */}
      {!isPipelineMode && taskStatus?.validation_report && (
        <ValidationReportCard report={taskStatus.validation_report} />
      )}

      {/* Export */}
      <div className="bg-white rounded-xl border p-6">
        <h3 className="font-semibold mb-4">{t('extract.export')}</h3>
        {exportError && (
          <p className="text-sm text-red-600 mb-2">{exportError}</p>
        )}
        <div className="flex gap-2 flex-wrap">
          {['json', 'yaml', 'csv', 'ttl', 'html', 'cypher', 'tugraph'].map(fmt => (
            <button
              key={fmt}
              type="button"
              disabled={exportingFormat !== null}
              onClick={() => handleExport(fmt)}
              className="px-3 py-1.5 border rounded-lg text-sm hover:bg-gray-50 font-mono text-gray-700 disabled:opacity-50"
            >
              {exportingFormat === fmt && <Loader2 size={14} className="animate-spin inline mr-1" />}
              {fmt.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}
