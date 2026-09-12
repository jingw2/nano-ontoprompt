import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { ontologyApi } from '@/api/ontologies'
import ConfidenceBar from '@/components/ConfidenceBar'
import { ArrowLeft, Pencil, Trash2, Save, X, Plus, Check } from 'lucide-react'
import type { Action, Entity, LogicRule } from '@/types/ontology'

/** One JSON-array field of an Action's four Palantir components, edited as
 * raw JSON text (consistent with V2DefinitionEditor's pattern elsewhere in
 * this app) since each entry's shape varies by field. */
function JsonListField({
  label, placeholder, value, onChange, error,
}: {
  label: string
  placeholder: string
  value: string
  onChange: (v: string) => void
  error?: string
}) {
  return (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <textarea value={value} onChange={e => onChange(e.target.value)} rows={4} placeholder={placeholder}
        className="w-full border rounded-lg px-3 py-2 text-sm font-mono resize-none" />
      {error && <p className="text-xs text-red-500 mt-1">{error}</p>}
    </div>
  )
}

function formatJsonList(value: unknown): string {
  return JSON.stringify(value ?? [], null, 2)
}

function ChipEditor({
  editing, items, onRemove, availableOptions, onAdd, color,
}: {
  editing: boolean
  items: { id: string; label: string; href: string }[]
  onRemove: (id: string) => void
  availableOptions: { id: string; label: string }[]
  onAdd: (id: string) => void
  color: 'blue' | 'orange' | 'purple'
}) {
  const { t } = useTranslation()
  const [addId, setAddId] = useState('')
  const cls = {
    blue:   { chip: 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100', del: 'text-blue-400 hover:text-blue-700' },
    orange: { chip: 'bg-orange-50 text-orange-700 border-orange-200 hover:bg-orange-100', del: 'text-orange-400 hover:text-orange-700' },
    purple: { chip: 'bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100', del: 'text-purple-400 hover:text-purple-700' },
  }[color]

  if (!editing) {
    if (items.length === 0) return <p className="text-sm text-gray-400">{t('common.none')}</p>
    return (
      <div className="flex flex-wrap gap-2">
        {items.map(item => (
          <Link key={item.id} to={item.href}
            className={`px-3 py-1.5 rounded-full text-xs border ${cls.chip}`}>
            {item.label}
          </Link>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {items.map(item => (
          <span key={item.id} className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-xs border ${cls.chip}`}>
            {item.label}
            <button onClick={() => onRemove(item.id)} className={`${cls.del} ml-0.5`}>
              <X size={10} />
            </button>
          </span>
        ))}
      </div>
      {availableOptions.length > 0 && (
        <div className="flex items-center gap-2">
          <select value={addId} onChange={e => setAddId(e.target.value)}
            className="flex-1 border rounded-lg px-2 py-1.5 text-xs">
            <option value="">{t('detailPage.select_to_add')}</option>
            {availableOptions.map(o => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </select>
          <button disabled={!addId} onClick={() => { if (addId) { onAdd(addId); setAddId('') } }}
            className="flex items-center gap-1 px-2.5 py-1.5 bg-black text-white rounded-lg text-xs disabled:opacity-40">
            <Plus size={12} /> {t('detailPage.add')}
          </button>
        </div>
      )}
    </div>
  )
}

export default function ActionDetailPage() {
  const { t } = useTranslation()
  const { id: oid, aid } = useParams<{ id: string; aid: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [entitiesEditing, setEntitiesEditing] = useState(false)
  const [logicEditing, setLogicEditing] = useState(false)
  const { register, handleSubmit, reset } = useForm<Partial<Action>>()
  const [parametersText, setParametersText] = useState('[]')
  const [rulesText, setRulesText] = useState('[]')
  const [criteriaText, setCriteriaText] = useState('[]')
  const [effectsText, setEffectsText] = useState('[]')
  const [jsonError, setJsonError] = useState('')

  const { data: action, isLoading } = useQuery({
    queryKey: ['action', oid, aid],
    queryFn: () => ontologyApi.listActions(oid!).then((list) => {
      const found = (list as Action[]).find(a => a.id === aid)
      if (!found) throw new Error('Action not found')
      return found
    }),
    enabled: !!oid && !!aid,
  })

  const { data: allEntities = [] } = useQuery({
    queryKey: ['entities', oid],
    queryFn: () => ontologyApi.listEntities(oid!),
    enabled: !!oid,
  })

  const { data: allLogic = [] } = useQuery({
    queryKey: ['logic', oid],
    queryFn: () => ontologyApi.listLogic(oid!),
    enabled: !!oid,
  })

  const updateMut = useMutation({
    mutationFn: (data: Partial<Action>) => ontologyApi.updateAction(oid!, aid!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['action', oid, aid] })
      qc.invalidateQueries({ queryKey: ['actions', oid] })
      setEditing(false)
    },
  })

  const deleteMut = useMutation({
    mutationFn: () => ontologyApi.deleteAction(oid!, aid!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['actions', oid] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      navigate(`/ontologies/${oid}?tab=actions`)
    },
  })

  const onSubmit = (data: Partial<Action>) => {
    try {
      const parameters = JSON.parse(parametersText)
      const rules = JSON.parse(rulesText)
      const submission_criteria = JSON.parse(criteriaText)
      const side_effects = JSON.parse(effectsText)
      setJsonError('')
      updateMut.mutate({ ...data, parameters, rules, submission_criteria, side_effects })
    } catch {
      setJsonError(t('actionDetail.json_array_error'))
    }
  }

  const startEdit = () => {
    if (action) {
      reset(action)
      setParametersText(formatJsonList(action.parameters))
      setRulesText(formatJsonList(action.rules))
      setCriteriaText(formatJsonList(action.submission_criteria))
      setEffectsText(formatJsonList(action.side_effects))
      setJsonError('')
    }
    setEditing(true)
  }

  if (isLoading) return <div className="p-6 text-gray-400">{t('common.loading')}</div>
  if (!action) return <div className="p-6 text-red-500">{t('actionDetail.not_found')}</div>

  // linked_entities 可能是实体显示名(简易 LLM)或实体类型名(Pipeline Mapping)
  const linkedKeys = new Set(action.linked_entities ?? [])
  const entityHit = (e: Entity) =>
    linkedKeys.has(e.name_cn) || (e.type ? linkedKeys.has(e.type) : false) || (e.name_en ? linkedKeys.has(e.name_en) : false)
  const relatedEntities = (allEntities as Entity[]).filter(entityHit)
  const unlinkedEntities = (allEntities as Entity[]).filter(e => !entityHit(e))

  // 关联逻辑: 显式 linked_logic_ids, 或与本动作共享 linked_entities(同一实体类)
  const linkedLogicIds = new Set(action.linked_logic_ids ?? [])
  const logicHit = (r: LogicRule) =>
    linkedLogicIds.has(r.id) || (r.linked_entities ?? []).some(x => linkedKeys.has(x))
  const relatedLogic = (allLogic as LogicRule[]).filter(logicHit)
  const unlinkedLogic = (allLogic as LogicRule[]).filter(r => !logicHit(r))

  // Entity link helpers
  const removeEntity = (entityId: string) => {
    const entity = (allEntities as Entity[]).find(e => e.id === entityId)
    if (!entity) return
    const next = (action.linked_entities ?? []).filter(n => n !== entity.name_cn && n !== entity.name_en)
    updateMut.mutate({ linked_entities: next })
  }
  const addEntity = (entityId: string) => {
    const entity = (allEntities as Entity[]).find(e => e.id === entityId)
    if (!entity) return
    const next = [...(action.linked_entities ?? []), entity.name_cn]
    updateMut.mutate({ linked_entities: next })
  }

  // Logic link helpers
  const removeLogic = (logicId: string) => {
    const next = (action.linked_logic_ids ?? []).filter(i => i !== logicId)
    updateMut.mutate({ linked_logic_ids: next })
  }
  const addLogic = (logicId: string) => {
    const next = [...(action.linked_logic_ids ?? []), logicId]
    updateMut.mutate({ linked_logic_ids: next })
  }

  const formatDate = (s: string) => new Date(s).toLocaleString('zh-CN')

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={() => navigate(`/ontologies/${oid}?tab=actions`)}
          className="flex items-center gap-2 text-gray-500 hover:text-black text-sm">
          <ArrowLeft size={16} /> {t('actionDetail.back_to_list')}
        </button>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <button onClick={() => setEditing(false)}
                className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
                <X size={14} /> {t('common.cancel')}
              </button>
              <button onClick={handleSubmit(onSubmit)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm">
                <Save size={14} /> {t('common.save')}
              </button>
            </>
          ) : (
            <>
              <button onClick={startEdit}
                className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
                <Pencil size={14} /> {t('common.edit')}
              </button>
              <button onClick={() => setShowDeleteConfirm(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 border border-red-200 text-red-500 rounded-lg text-sm hover:bg-red-50">
                <Trash2 size={14} /> {t('common.delete')}
              </button>
            </>
          )}
        </div>
      </div>

      {/* Action Info Card */}
      <div className="bg-white border rounded-xl p-6">
        <h3 className="font-semibold mb-4">{t('actionDetail.info_title')}</h3>
        {editing ? (
          <form className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('entities.ph_name_cn')}</label>
                <input {...register('name_cn', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('entities.col_name_en')}</label>
                <input {...register('name_en')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('detailPage.confidence')} (0-1)</label>
                <input {...register('confidence', { valueAsNumber: true })} type="number" step="0.01" min="0" max="1" className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('detailPage.description')}</label>
              <textarea {...register('description')} rows={2} className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <JsonListField label={`${t('actionDetail.parameters_label')} (Parameters)`} value={parametersText} onChange={setParametersText}
                placeholder='[{"name": "target", "type": "object_reference", "description": "..."}]' />
              <JsonListField label={`${t('actionDetail.rules_label')} (Rules)`} value={rulesText} onChange={setRulesText}
                placeholder='[{"operation": "Modify", "target": "Entity.field", "value": "..."}]' />
              <JsonListField label={`${t('actionDetail.criteria_label')} (Submission Criteria)`} value={criteriaText} onChange={setCriteriaText}
                placeholder='["new_flight.status != Cancelled"]' />
              <JsonListField label={`${t('actionDetail.effects_label')} (Side Effects)`} value={effectsText} onChange={setEffectsText}
                placeholder='[{"type": "Notification", "target": "...", "detail": "..."}]' />
            </div>
            {jsonError && <p className="text-xs text-red-500">{jsonError}</p>}
          </form>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('entities.col_name_cn')}</p>
                <p className="text-sm font-medium">{action.name_cn}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('entities.col_name_en')}</p>
                <p className="text-sm">{action.name_en || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('detailPage.version')}</p>
                <p className="text-sm font-mono">{action.version}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('detailPage.confidence')}</p>
                <div className="flex items-center gap-3">
                  <div className="w-32"><ConfidenceBar value={action.confidence} /></div>
                  <span className="text-sm text-gray-600">{Math.round(action.confidence * 100)}%</span>
                </div>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">{t('detailPage.description')}</p>
              <p className="text-sm text-gray-700">{action.description || '—'}</p>
            </div>
            {!!action.parameters?.length && (
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('actionDetail.parameters_label')} (Parameters)</p>
                <div className="bg-gray-50 rounded-lg p-3 font-mono text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto">{formatJsonList(action.parameters)}</div>
              </div>
            )}
            {!!action.rules?.length && (
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('actionDetail.rules_label')} (Rules)</p>
                <div className="bg-gray-50 rounded-lg p-3 font-mono text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto">{formatJsonList(action.rules)}</div>
              </div>
            )}
            {!!action.submission_criteria?.length && (
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('actionDetail.criteria_label')} (Submission Criteria)</p>
                <div className="bg-gray-50 rounded-lg p-3 font-mono text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto">{formatJsonList(action.submission_criteria)}</div>
              </div>
            )}
            {!!action.side_effects?.length && (
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('actionDetail.effects_label')} (Side Effects)</p>
                <div className="bg-gray-50 rounded-lg p-3 font-mono text-xs text-gray-700 whitespace-pre-wrap overflow-x-auto">{formatJsonList(action.side_effects)}</div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4 pt-2 border-t">
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('detailPage.created_at')}</p>
                <p className="text-xs text-gray-600">{formatDate(action.created_at)}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">{t('detailPage.updated_at')}</p>
                <p className="text-xs text-gray-600">{formatDate(action.updated_at)}</p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Related Entities — inline link management */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">{t('actionDetail.related_entities_title')}</h3>
          <button onClick={() => setEntitiesEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${entitiesEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {entitiesEditing ? <><Check size={11} /> {t('detailPage.done')}</> : <><Pencil size={11} /> {t('common.edit')}</>}
          </button>
        </div>
        <ChipEditor
          editing={entitiesEditing}
          items={relatedEntities.map(e => ({ id: e.id, label: e.name_cn, href: `/ontologies/${oid}/entities/${e.id}` }))}
          onRemove={removeEntity}
          availableOptions={unlinkedEntities.map(e => ({ id: e.id, label: e.name_cn }))}
          onAdd={addEntity}
          color="blue"
        />
      </div>

      {/* Related Logic Rules — inline link management */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">{t('entityDetail.related_logic_title')}</h3>
          <button onClick={() => setLogicEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${logicEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {logicEditing ? <><Check size={11} /> {t('detailPage.done')}</> : <><Pencil size={11} /> {t('common.edit')}</>}
          </button>
        </div>
        <ChipEditor
          editing={logicEditing}
          items={relatedLogic.map(r => ({ id: r.id, label: r.name_cn, href: `/ontologies/${oid}/logic/${r.id}` }))}
          onRemove={removeLogic}
          availableOptions={unlinkedLogic.map(r => ({ id: r.id, label: r.name_cn }))}
          onAdd={addLogic}
          color="orange"
        />
      </div>

      {/* Delete Confirm Dialog */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-lg p-6 w-80">
            <h3 className="font-semibold mb-2">{t('common.confirm_delete')}</h3>
            <p className="text-sm text-gray-600 mb-4">{t('actions.delete_confirm', { name: action.name_cn })}</p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 border rounded-lg text-sm">{t('common.cancel')}</button>
              <button onClick={() => deleteMut.mutate()}
                className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm">{t('common.delete')}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
