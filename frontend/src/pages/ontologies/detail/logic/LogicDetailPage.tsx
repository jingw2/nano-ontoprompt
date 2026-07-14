import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { ontologyApi } from '@/api/ontologies'
import { apiClient } from '@/api/client'
import ConfidenceBar from '@/components/ConfidenceBar'
import { ArrowLeft, Pencil, Trash2, Save, X, Plus, Check, ToggleLeft, ToggleRight } from 'lucide-react'
import type { LogicRule, Action, Entity } from '@/types/ontology'

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

export default function LogicDetailPage() {
  const { id: oid, lid } = useParams<{ id: string; lid: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [entitiesEditing, setEntitiesEditing] = useState(false)
  const [actionsEditing, setActionsEditing] = useState(false)
  const { register, handleSubmit, reset } = useForm<Partial<LogicRule>>()

  const { data: rule, isLoading } = useQuery({
    queryKey: ['logic-rule', oid, lid],
    queryFn: () => ontologyApi.listLogic(oid!).then((list: any) => {
      const found = (list as LogicRule[]).find(r => r.id === lid)
      if (!found) throw new Error('Logic rule not found')
      return found
    }),
    enabled: !!oid && !!lid,
  })

  const { data: allActions = [] } = useQuery({
    queryKey: ['actions', oid],
    queryFn: () => ontologyApi.listActions(oid!) as any,
    enabled: !!oid,
  })

  const { data: allEntities = [] } = useQuery({
    queryKey: ['entities', oid],
    queryFn: () => ontologyApi.listEntities(oid!) as any,
    enabled: !!oid,
  })

  const updateMut = useMutation({
    mutationFn: (data: Partial<LogicRule>) => ontologyApi.updateLogic(oid!, lid!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['logic-rule', oid, lid] })
      qc.invalidateQueries({ queryKey: ['logic', oid] })
      setEditing(false)
    },
  })

  const toggleMut = useMutation({
    mutationFn: () => apiClient.post(`/ontologies/${oid}/logic/${lid}/toggle`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['logic-rule', oid, lid] })
      qc.invalidateQueries({ queryKey: ['logic', oid] })
    },
  })

  // Patch an action's linked_logic_ids (for bidirectional action linking)
  const updateActionLinkMut = useMutation({
    mutationFn: ({ aid, linked_logic_ids }: { aid: string; linked_logic_ids: string[] }) =>
      ontologyApi.updateAction(oid!, aid, { linked_logic_ids } as any),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['actions', oid] }),
  })

  const deleteMut = useMutation({
    mutationFn: () => ontologyApi.deleteLogic(oid!, lid!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['logic', oid] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      navigate(`/ontologies/${oid}?tab=logic`)
    },
  })

  const onSubmit = (data: Partial<LogicRule>) => updateMut.mutate(data)

  const startEdit = () => {
    if (rule) reset(rule)
    setEditing(true)
  }

  if (isLoading) return <div className="p-6 text-gray-400">加载中...</div>
  if (!rule) return <div className="p-6 text-red-500">逻辑规则未找到</div>

  // linked_entities 可能是实体显示名(简易 LLM)或实体类型名(Pipeline Mapping)
  const linkedKeys = new Set(rule.linked_entities ?? [])
  const entityHit = (e: Entity) =>
    linkedKeys.has(e.name_cn) || (e.type ? linkedKeys.has(e.type) : false) || (e.name_en ? linkedKeys.has(e.name_en) : false)
  const relatedEntities = (allEntities as Entity[]).filter(entityHit)
  const unlinkedEntities = (allEntities as Entity[]).filter(e => !entityHit(e))

  // 关联动作: 显式 linked_logic_ids, 或与本规则共享 linked_entities(同一实体类)
  const actionHit = (a: Action) =>
    (a.linked_logic_ids?.includes(lid!) ?? false) ||
    (a.linked_entities ?? []).some(x => linkedKeys.has(x))
  const relatedActions = (allActions as Action[]).filter(actionHit)
  const unlinkedActions = (allActions as Action[]).filter(a => !actionHit(a))

  // Entity link helpers
  const removeEntity = (entityId: string) => {
    const entity = relatedEntities.find(e => e.id === entityId)
    if (!entity) return
    const next = (rule.linked_entities ?? []).filter(
      n => n !== entity.name_cn && n !== entity.name_en
    )
    updateMut.mutate({ linked_entities: next } as any)
  }
  const addEntity = (entityId: string) => {
    const entity = (allEntities as Entity[]).find(e => e.id === entityId)
    if (!entity) return
    const next = [...(rule.linked_entities ?? []), entity.name_cn]
    updateMut.mutate({ linked_entities: next } as any)
  }

  // Action link helpers (patch action's linked_logic_ids)
  const removeAction = (actionId: string) => {
    const action = (allActions as Action[]).find(a => a.id === actionId)
    if (!action) return
    const next = (action.linked_logic_ids ?? []).filter(i => i !== lid)
    updateActionLinkMut.mutate({ aid: actionId, linked_logic_ids: next })
  }
  const addAction = (actionId: string) => {
    const action = (allActions as Action[]).find(a => a.id === actionId)
    if (!action) return
    const next = [...(action.linked_logic_ids ?? []), lid!]
    updateActionLinkMut.mutate({ aid: actionId, linked_logic_ids: next })
  }

  const formatDate = (s: string) => new Date(s).toLocaleString('zh-CN')

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={() => navigate(`/ontologies/${oid}?tab=logic`)}
          className="flex items-center gap-2 text-gray-500 hover:text-black text-sm">
          <ArrowLeft size={16} /> 返回逻辑规则列表
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
              <button onClick={() => toggleMut.mutate()} disabled={toggleMut.isPending}
                className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-50">
                {rule.enabled !== false ? <ToggleRight size={14} className="text-green-600" /> : <ToggleLeft size={14} />}
                {rule.enabled !== false ? '已启用' : '已禁用'}
              </button>
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

      {/* Rule Info Card */}
      <div className="bg-white border rounded-xl p-6">
        <h3 className="font-semibold mb-4">规则信息</h3>
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
              <label className="block text-xs text-gray-500 mb-1">公式</label>
              <input {...register('formula')} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">描述</label>
              <textarea {...register('description')} rows={3} className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-gray-500 mb-1">中文名</p>
                <p className="text-sm font-medium">{rule.name_cn}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">英文名</p>
                <p className="text-sm">{rule.name_en || '—'}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">版本</p>
                <p className="text-sm font-mono">{rule.version}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">状态</p>
                <span className={`inline-flex text-xs px-1.5 py-0.5 rounded border ${
                  rule.status === 'published' ? 'bg-green-50 text-green-700 border-green-200' :
                  rule.status === 'draft' ? 'bg-amber-50 text-amber-700 border-amber-200' :
                  'bg-gray-50 text-gray-600'
                }`}>{rule.status || 'draft'}</span>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">置信度</p>
                <div className="flex items-center gap-3">
                  <div className="w-32"><ConfidenceBar value={rule.confidence} /></div>
                  <span className="text-sm text-gray-600">{Math.round(rule.confidence * 100)}%</span>
                </div>
              </div>
            </div>
            {rule.formula && (
              <div>
                <p className="text-xs text-gray-500 mb-1">公式</p>
                <div className="bg-gray-50 rounded-lg p-3 font-mono text-xs text-gray-700 whitespace-pre-wrap">{rule.formula}</div>
              </div>
            )}
            <div>
              <p className="text-xs text-gray-500 mb-1">描述</p>
              <p className="text-sm text-gray-700">{rule.description || '—'}</p>
            </div>
            <div className="grid grid-cols-2 gap-4 pt-2 border-t">
              <div>
                <p className="text-xs text-gray-500 mb-1">创建时间</p>
                <p className="text-xs text-gray-600">{formatDate(rule.created_at)}</p>
              </div>
              <div>
                <p className="text-xs text-gray-500 mb-1">更新时间</p>
                <p className="text-xs text-gray-600">{formatDate(rule.updated_at)}</p>
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
          items={relatedEntities.map(e => ({ id: e.id, label: `${e.name_cn}${e.type ? ` (${e.type})` : ''}`, href: `/ontologies/${oid}/entities/${e.id}` }))}
          onRemove={removeEntity}
          availableOptions={unlinkedEntities.map(e => ({ id: e.id, label: e.name_cn }))}
          onAdd={addEntity}
          color="blue"
        />
      </div>

      {/* Related Actions — inline link management */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">关联动作</h3>
          <button onClick={() => setActionsEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${actionsEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {actionsEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
          </button>
        </div>
        <ChipEditor
          editing={actionsEditing}
          items={relatedActions.map(a => ({ id: a.id, label: a.name_cn, href: `/ontologies/${oid}/actions/${a.id}` }))}
          onRemove={removeAction}
          availableOptions={unlinkedActions.map(a => ({ id: a.id, label: a.name_cn }))}
          onAdd={addAction}
          color="purple"
        />
      </div>

      {/* Delete Confirm Dialog */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-lg p-6 w-80">
            <h3 className="font-semibold mb-2">确认删除</h3>
            <p className="text-sm text-gray-600 mb-4">确定要删除规则「{rule.name_cn}」吗？此操作不可撤销。</p>
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
