import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { ontologyApi } from '@/api/ontologies'
import ConfidenceBar from '@/components/ConfidenceBar'
import { ArrowLeft, Pencil, Trash2, Save, X, Plus, Check } from 'lucide-react'
import type { Action, Entity, LogicRule } from '@/types/ontology'

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
  const [addId, setAddId] = useState('')
  const cls = {
    blue:   { chip: 'bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100', del: 'text-blue-400 hover:text-blue-700' },
    orange: { chip: 'bg-orange-50 text-orange-700 border-orange-200 hover:bg-orange-100', del: 'text-orange-400 hover:text-orange-700' },
    purple: { chip: 'bg-purple-50 text-purple-700 border-purple-200 hover:bg-purple-100', del: 'text-purple-400 hover:text-purple-700' },
  }[color]

  if (!editing) {
    if (items.length === 0) return <p className="text-sm text-gray-400">暂无</p>
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
            <option value="">— 选择添加 —</option>
            {availableOptions.map(o => (
              <option key={o.id} value={o.id}>{o.label}</option>
            ))}
          </select>
          <button disabled={!addId} onClick={() => { if (addId) { onAdd(addId); setAddId('') } }}
            className="flex items-center gap-1 px-2.5 py-1.5 bg-black text-white rounded-lg text-xs disabled:opacity-40">
            <Plus size={12} /> 添加
          </button>
        </div>
      )}
    </div>
  )
}

export default function ActionDetailPage() {
  const { id: oid, aid } = useParams<{ id: string; aid: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [entitiesEditing, setEntitiesEditing] = useState(false)
  const [logicEditing, setLogicEditing] = useState(false)
  const { register, handleSubmit, reset } = useForm<Partial<Action>>()

  const { data: action, isLoading } = useQuery({
    queryKey: ['action', oid, aid],
    queryFn: () => ontologyApi.listActions(oid!).then((list: any) => {
      const found = (list as Action[]).find(a => a.id === aid)
      if (!found) throw new Error('Action not found')
      return found
    }),
    enabled: !!oid && !!aid,
  })

  const { data: allEntities = [] } = useQuery({
    queryKey: ['entities', oid],
    queryFn: () => ontologyApi.listEntities(oid!) as any,
    enabled: !!oid,
  })

  const { data: allLogic = [] } = useQuery({
    queryKey: ['logic', oid],
    queryFn: () => ontologyApi.listLogic(oid!) as any,
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

  const onSubmit = (data: Partial<Action>) => updateMut.mutate(data)

  const startEdit = () => {
    if (action) reset(action)
    setEditing(true)
  }

  if (isLoading) return <div className="p-6 text-gray-400">加载中...</div>
  if (!action) return <div className="p-6 text-red-500">动作未找到</div>

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
    updateMut.mutate({ linked_entities: next } as any)
  }
  const addEntity = (entityId: string) => {
    const entity = (allEntities as Entity[]).find(e => e.id === entityId)
    if (!entity) return
    const next = [...(action.linked_entities ?? []), entity.name_cn]
    updateMut.mutate({ linked_entities: next } as any)
  }

  // Logic link helpers
  const removeLogic = (logicId: string) => {
    const next = (action.linked_logic_ids ?? []).filter(i => i !== logicId)
    updateMut.mutate({ linked_logic_ids: next } as any)
  }
  const addLogic = (logicId: string) => {
    const next = [...(action.linked_logic_ids ?? []), logicId]
    updateMut.mutate({ linked_logic_ids: next } as any)
  }

  const formatDate = (s: string) => new Date(s).toLocaleString('zh-CN')

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={() => navigate(`/ontologies/${oid}?tab=actions`)}
          className="flex items-center gap-2 text-gray-500 hover:text-black text-sm">
          <ArrowLeft size={16} /> 返回动作列表
        </button>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <button onClick={() => setEditing(false)}
                className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
                <X size={14} /> 取消
              </button>
              <button onClick={handleSubmit(onSubmit)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm">
                <Save size={14} /> 保存
              </button>
            </>
          ) : (
            <>
              <button onClick={startEdit}
                className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
                <Pencil size={14} /> 编辑
              </button>
              <button onClick={() => setShowDeleteConfirm(true)}
                className="flex items-center gap-1.5 px-3 py-1.5 border border-red-200 text-red-500 rounded-lg text-sm hover:bg-red-50">
                <Trash2 size={14} /> 删除
              </button>
            </>
          )}
        </div>
      </div>

      {/* Action Info Card */}
      <div className="bg-white border rounded-xl p-6">
        <h3 className="font-semibold mb-4">动作信息</h3>
        {editing ? (
          <form className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">中文名 *</label>
                <input {...register('name_cn', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">英文名</label>
                <input {...register('name_en')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">置信度 (0-1)</label>
                <input {...register('confidence', { valueAsNumber: true })} type="number" step="0.01" min="0" max="1" className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">描述</label>
              <textarea {...register('description')} rows={2} className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">执行规则</label>
              <textarea {...register('execution_rule')} rows={2} className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">函数代码</label>
              <textarea {...register('function_code')} rows={6} className="w-full border rounded-lg px-3 py-2 text-sm font-mono resize-none" />
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-gray-500 mb-1">中文名</p>
                <p className="text-sm font-medium">{action.name_cn}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">英文名</p>
                <p className="text-sm">{action.name_en || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">版本</p>
                <p className="text-sm font-mono">{action.version}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">置信度</p>
                <div className="flex items-center gap-3">
                  <div className="w-32"><ConfidenceBar value={action.confidence} /></div>
                  <span className="text-sm text-gray-600">{Math.round(action.confidence * 100)}%</span>
                </div>
              </div>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">描述</p>
              <p className="text-sm text-gray-700">{action.description || '—'}</p>
            </div>
            {action.execution_rule && (
              <div>
                <p className="text-xs text-gray-500 mb-1">执行规则</p>
                <p className="text-sm text-gray-700 whitespace-pre-wrap">{action.execution_rule}</p>
              </div>
            )}
            {action.function_code && (
              <div>
                <p className="text-xs text-gray-500 mb-1">函数代码</p>
                <div className="bg-gray-900 rounded-lg p-4 font-mono text-xs text-green-300 whitespace-pre-wrap overflow-x-auto">{action.function_code}</div>
              </div>
            )}
            <div className="grid grid-cols-2 gap-4 pt-2 border-t">
              <div>
                <p className="text-xs text-gray-500 mb-1">创建时间</p>
                <p className="text-xs text-gray-600">{formatDate(action.created_at)}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">更新时间</p>
                <p className="text-xs text-gray-600">{formatDate(action.updated_at)}</p>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Related Entities — inline link management */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">关联实体</h3>
          <button onClick={() => setEntitiesEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${entitiesEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {entitiesEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
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
          <h3 className="font-semibold">关联逻辑规则</h3>
          <button onClick={() => setLogicEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${logicEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {logicEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
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
            <h3 className="font-semibold mb-2">确认删除</h3>
            <p className="text-sm text-gray-600 mb-4">确定要删除动作「{action.name_cn}」吗？此操作不可撤销。</p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setShowDeleteConfirm(false)}
                className="px-4 py-2 border rounded-lg text-sm">取消</button>
              <button onClick={() => deleteMut.mutate()}
                className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm">删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
