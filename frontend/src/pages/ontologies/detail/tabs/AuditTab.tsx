import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ontologyApi, modelApi } from '@/api/ontologies'
import {
  CheckCircle, XCircle, Loader2, ChevronRight, AlertTriangle, AlertCircle,
  Info, ChevronDown, ChevronUp,
} from 'lucide-react'

const STAGE_KEYS = [
  { key: 'queued',               i18nKey: 'audit.stage_queued' },
  { key: 'loading ontology',     i18nKey: 'audit.stage_loading' },
  { key: 'running react agent',  i18nKey: 'audit.stage_running' },
  { key: 'saving findings',      i18nKey: 'audit.stage_saving' },
  { key: 'done',                 i18nKey: 'audit.stage_done' },
]

const STAGE_PCT: Record<string, number> = {
  queued: 0, 'loading ontology': 10, 'running react agent': 30, 'saving findings': 90, done: 100,
}

const auditTaskKey = (oid: string) => `ontoprompt_last_audit_${oid}`

type SavedAuditTask = { task_id?: string; status?: string; [key: string]: unknown }

function loadSavedTask(oid: string): SavedAuditTask | null {
  try {
    const saved = localStorage.getItem(auditTaskKey(oid))
    return saved ? JSON.parse(saved) : null
  } catch { return null }
}

function saveTask(oid: string, data: SavedAuditTask) {
  try { localStorage.setItem(auditTaskKey(oid), JSON.stringify(data)) } catch {}
}

const SEVERITY_STYLE: Record<string, { border: string; bg: string; text: string; badge: string; Icon: any }> = {
  critical: { border: 'border-red-200', bg: 'bg-red-50', text: 'text-red-700', badge: 'bg-red-100 text-red-700', Icon: XCircle },
  warning:  { border: 'border-amber-200', bg: 'bg-amber-50', text: 'text-amber-700', badge: 'bg-amber-100 text-amber-700', Icon: AlertTriangle },
  info:     { border: 'border-blue-200', bg: 'bg-blue-50', text: 'text-blue-700', badge: 'bg-blue-100 text-blue-700', Icon: Info },
}

function FindingCard({ finding, t }: { finding: any; t: any }) {
  const sev = finding.severity as string
  const style = SEVERITY_STYLE[sev] ?? SEVERITY_STYLE.info
  const Icon = style.Icon
  const catKey = `audit.category_${finding.category}` as const

  return (
    <div className={`rounded-lg border ${style.border} ${style.bg} p-4`}>
      <div className="flex items-start gap-2">
        <Icon size={15} className={`mt-0.5 flex-shrink-0 ${style.text}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`text-xs font-semibold ${style.text}`}>{finding.title}</span>
            <span className={`text-xs px-1.5 py-0.5 rounded ${style.badge}`}>{t(catKey)}</span>
          </div>
          {finding.description && (
            <p className={`text-xs mt-1 ${style.text} opacity-80`}>{finding.description}</p>
          )}
          {finding.affected_items?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              <span className="text-xs text-gray-400 mr-1">{t('audit.affected_items')}:</span>
              {finding.affected_items.map((item: string, i: number) => (
                <span key={i} className="text-xs bg-white border border-gray-200 px-1.5 py-0.5 rounded text-gray-600">{item}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function FindingsReport({ findings, t }: { findings: any[]; t: any }) {
  const [traceOpen, setTraceOpen] = useState(false)
  const critical = findings.filter(f => f.severity === 'critical')
  const warning  = findings.filter(f => f.severity === 'warning')
  const info     = findings.filter(f => f.severity === 'info')
  const [openSections, setOpenSections] = useState<Record<string, boolean>>({ critical: true, warning: false, info: false })

  const toggle = (key: string) => setOpenSections(prev => ({ ...prev, [key]: !prev[key] }))

  if (findings.length === 0) {
    return (
      <div className="bg-white rounded-xl border border-green-200 p-8 text-center">
        <CheckCircle size={32} className="mx-auto text-green-500 mb-3" />
        <p className="text-sm font-medium text-green-700">{t('audit.no_findings')}</p>
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl border p-6 space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <h3 className="font-semibold">{t('audit.findings_title')}</h3>
        <span className="ml-auto text-xs text-gray-500">
          {t('audit.findings_summary', { total: findings.length, critical: critical.length, warning: warning.length, info: info.length })}
        </span>
      </div>

      {(['critical', 'warning', 'info'] as const).map(sev => {
        const group = sev === 'critical' ? critical : sev === 'warning' ? warning : info
        if (!group.length) return null
        const style = SEVERITY_STYLE[sev]
        return (
          <div key={sev} className={`rounded-lg border ${style.border}`}>
            <button
              className={`w-full flex items-center justify-between px-4 py-2.5 ${style.bg} rounded-lg`}
              onClick={() => toggle(sev)}>
              <span className={`text-sm font-semibold ${style.text}`}>
                {t(`audit.severity_${sev}`)} · {group.length}
              </span>
              {openSections[sev] ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {openSections[sev] && (
              <div className="p-3 space-y-2">
                {group.map((f: any, i: number) => <FindingCard key={i} finding={f} t={t} />)}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function TracePanel({
  trace, open, onToggle, t, thinking,
}: {
  trace: any[]
  open: boolean
  onToggle: () => void
  t: any
  thinking?: boolean
}) {
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (open) bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [trace.length, thinking, open])

  return (
    <div className="bg-white rounded-xl border p-4">
      <button
        className="flex items-center gap-2 text-sm font-medium text-gray-600 w-full"
        onClick={onToggle}>
        {open ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        {t('audit.trace_title')} ({trace.length}{thinking ? '+' : ''} steps)
      </button>
      {open && (
        <div className="mt-3 space-y-2 font-mono text-xs text-gray-500 max-h-96 overflow-y-auto">
          {trace.map((step: any, i: number) => (
            <div key={i} className="bg-gray-50 rounded p-2 border border-gray-100">
              <span className="text-gray-400">Step {step.step + 1}</span>
              {step.thought && <p className="mt-1 text-gray-600 whitespace-pre-wrap">{step.thought}</p>}
              {step.tool_name && (
                <p className="mt-1"><span className="text-purple-600">▶ {step.tool_name}</span>
                  {step.tool_args && Object.keys(step.tool_args).length > 0 && (
                    <span className="text-gray-400"> {JSON.stringify(step.tool_args)}</span>
                  )}
                </p>
              )}
              {step.observation && (
                <p className="mt-1 text-green-700 whitespace-pre-wrap break-all line-clamp-4">{step.observation}</p>
              )}
              {step.text && <p className="mt-1 whitespace-pre-wrap">{step.text}</p>}
              {step.error && <p className="mt-1 text-red-500">{step.error}</p>}
            </div>
          ))}
          {thinking && (
            <div className="bg-gray-50 rounded p-2 border border-gray-100 flex items-center gap-2 text-gray-400">
              <Loader2 size={12} className="animate-spin" />
              {t('audit.trace_thinking')}
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      )}
    </div>
  )
}

export default function AuditTab({ ontologyId }: { ontologyId: string }) {
  const { t } = useTranslation()
  const pollRef = useRef<(() => void) | null>(null)

  const [modelId, setModelId] = useState('')
  const [modelName, setModelName] = useState('')
  const [pollTimedOut, setPollTimedOut] = useState(false)
  const [taskStatus, setTaskStatus] = useState<any>(() => loadSavedTask(ontologyId))
  const [traceOpen, setTraceOpen] = useState(false)

  const { data: models } = useQuery({ queryKey: ['models'], queryFn: () => modelApi.list() as any })
  const selectedModel = (models as any[] | undefined)?.find((m: any) => m.id === modelId)

  const auditMut = useMutation({
    mutationFn: () => ontologyApi.startAudit(ontologyId, { model_id: modelId, model_name: modelName }),
  })

  const startPoll = (taskId: string) => {
    pollRef.current?.()
    setPollTimedOut(false)
    let attempts = 0
    let cancelled = false
    pollRef.current = () => { cancelled = true }

    const poll = async () => {
      if (cancelled) return
      if (attempts++ > 600) { setPollTimedOut(true); return }
      try {
        const status: any = await ontologyApi.getAuditStatus(ontologyId, taskId)
        if (cancelled) return
        const merged = { ...status, task_id: taskId }
        setTaskStatus(merged)
        saveTask(ontologyId, merged)
        if (status.status === 'completed' || status.status === 'failed') {
          setPollTimedOut(false)
          return
        }
        setTimeout(poll, 2000)
      } catch {
        if (!cancelled) setTimeout(poll, 3000)
      }
    }
    poll()
  }

  useEffect(() => {
    const saved = loadSavedTask(ontologyId)
    if (saved?.task_id && saved.status !== 'completed' && saved.status !== 'failed') {
      setTraceOpen(true)
      startPoll(saved.task_id as string)
    }
    return () => { pollRef.current?.() }
  }, [ontologyId])

  const handleAudit = async () => {
    setPollTimedOut(false)
    setTraceOpen(true)
    setTaskStatus({ status: 'running', progress: { stage: 'queued', pct: 0 }, error: null, react_trace: [] })
    try {
      const res: any = await auditMut.mutateAsync()
      saveTask(ontologyId, { status: 'running', progress: { stage: 'queued', pct: 0 }, task_id: res.task_id })
      startPoll(res.task_id)
    } catch (e: any) {
      setTaskStatus({ status: 'failed', progress: { stage: 'error', pct: 0 }, error: String(e?.detail || e?.message || e) })
    }
  }

  const isAuditing = taskStatus && taskStatus.status !== 'completed' && taskStatus.status !== 'failed'
  const currentPct = taskStatus?.progress?.pct ?? 0
  const currentStage = taskStatus?.progress?.stage ?? ''
  const trace = taskStatus?.react_trace ?? []
  const showTrace = trace.length > 0 || (isAuditing && currentStage === 'running react agent')
  const traceThinking = isAuditing && currentStage === 'running react agent'

  return (
    <div className="space-y-5">
      {/* Config */}
      <div className="bg-white rounded-xl border p-6">
        <h3 className="font-semibold mb-1">{t('audit.title')}</h3>
        <p className="text-xs text-gray-400 mb-4">{t('audit.description')}</p>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">{t('audit.model_label')}</label>
            <select value={modelId} onChange={e => { setModelId(e.target.value); setModelName('') }}
              className="w-full border rounded-lg px-3 py-2 text-sm">
              <option value="">— {t('audit.model_label')} —</option>
              {(models as any[] || []).map((m: any) => (
                <option key={m.id} value={m.id}>{m.name}（{m.provider}）</option>
              ))}
            </select>
          </div>

          {selectedModel && (
            <div>
              <label className="block text-xs font-medium text-gray-600 mb-1">{t('audit.model_specific')}</label>
              <select value={modelName} onChange={e => setModelName(e.target.value)}
                className="w-full border rounded-lg px-3 py-2 text-sm">
                <option value="">— {t('audit.model_specific')} —</option>
                {(selectedModel.models || []).map((m: string) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
          )}

          <div className="pt-1">
            <button
              onClick={handleAudit}
              disabled={!modelId || !modelName || auditMut.isPending || !!isAuditing}
              className="px-5 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2">
              {isAuditing && <Loader2 size={14} className="animate-spin" />}
              {isAuditing ? t('audit.auditing') : t('audit.start')}
            </button>
          </div>
        </div>
      </div>

      {/* Progress */}
      {taskStatus && (
        <div className={`bg-white rounded-xl border p-6 ${taskStatus.status === 'failed' ? 'border-red-200 bg-red-50' : ''}`}>
          <h3 className="font-semibold mb-4">{t('audit.progress')}</h3>

          {taskStatus.status === 'failed' ? (
            <div className="flex items-start gap-2 text-red-600">
              <XCircle size={16} className="mt-0.5 flex-shrink-0" />
              <div>
                <p className="text-sm font-medium">{t('audit.failed')}</p>
                <p className="text-xs mt-0.5 text-red-500">{taskStatus.error}</p>
              </div>
            </div>
          ) : (
            <>
              <div className="flex items-center mb-5 overflow-x-auto pb-1">
                {STAGE_KEYS.map((stage, i) => {
                  const stagePct = STAGE_PCT[stage.key] ?? 0
                  const passed = currentPct >= stagePct
                  const done = taskStatus.status === 'completed'
                  return (
                    <div key={stage.key} className="flex items-center flex-shrink-0">
                      <div className="flex flex-col items-center gap-1">
                        <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-medium transition-colors ${
                          passed ? done ? 'bg-green-500 text-white' : 'bg-black text-white' : 'bg-gray-100 text-gray-400'
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

              <div className="w-full bg-gray-100 rounded-full h-1.5 mb-2">
                <div
                  className={`h-1.5 rounded-full transition-all duration-700 ${
                    taskStatus.status === 'completed' ? 'bg-green-500' : 'bg-black'
                  }`}
                  style={{ width: `${currentPct}%` }}
                />
              </div>
              <p className="text-xs text-gray-400 text-right">{currentPct}%</p>

              {pollTimedOut && (
                <div className="mt-3 flex items-center gap-2 text-xs text-amber-600">
                  <AlertCircle size={13} />
                  <span>{t('audit.poll_timeout')}</span>
                  <button onClick={() => taskStatus?.task_id && startPoll(taskStatus.task_id)}
                    className="underline ml-1">{t('audit.resume_poll')}</button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {/* Live trace during audit */}
      {showTrace && (
        <TracePanel
          trace={trace}
          open={traceOpen}
          onToggle={() => setTraceOpen(o => !o)}
          t={t}
          thinking={traceThinking}
        />
      )}

      {/* Findings */}
      {taskStatus?.status === 'completed' && taskStatus?.findings != null && (
        <FindingsReport findings={taskStatus.findings} t={t} />
      )}
    </div>
  )
}
