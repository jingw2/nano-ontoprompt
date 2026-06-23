import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { ontologyApi } from '@/api/ontologies'
import ConfidenceBar from '@/components/ConfidenceBar'
import { ArrowLeft, Pencil, Trash2, Save, X, Plus, Check } from 'lucide-react'
import type { Entity, LogicRule, Action } from '@/types/ontology'
import { parseEntityDisplay } from '@/utils/entityDisplay'

interface GraphNode { data: { id: string; label: string; type?: string } }
interface GraphEdge { data: { id: string; source: string; target: string; label?: string } }

// Inline chip + add/remove editor for a list of linked items
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

export default function EntityDetailPage() {
  const { id: oid, eid } = useParams<{ id: string; eid: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const { register, handleSubmit, reset } = useForm<Partial<Entity>>()

  // Per-card edit modes
  const [propEditing, setPropEditing] = useState(false)
  const [newKey, setNewKey] = useState('')
  const [newVal, setNewVal] = useState('')
  const [graphEditing, setGraphEditing] = useState(false)
  const [newRelTarget, setNewRelTarget] = useState('')
  const [newRelType, setNewRelType] = useState('关联')
  const [logicLinkEditing, setLogicLinkEditing] = useState(false)
  const [actionLinkEditing, setActionLinkEditing] = useState(false)

  const { data: entity, isLoading } = useQuery({
    queryKey: ['entity', oid, eid],
    queryFn: () => ontologyApi.listEntities(oid!).then((list: any) => {
      const found = (list as Entity[]).find(e => e.id === eid)
      if (!found) throw new Error('Entity not found')
      return found
    }),
    enabled: !!oid && !!eid,
  })

  const { data: graph } = useQuery({
    queryKey: ['graph', oid],
    queryFn: () => ontologyApi.getGraph(oid!) as any,
    enabled: !!oid,
  })

  const { data: allLogic = [] } = useQuery({
    queryKey: ['logic', oid],
    queryFn: () => ontologyApi.listLogic(oid!) as any,
    enabled: !!oid,
  })

  const { data: allActions = [] } = useQuery({
    queryKey: ['actions', oid],
    queryFn: () => ontologyApi.listActions(oid!) as any,
    enabled: !!oid,
  })

  const { data: relatedData } = useQuery({
    queryKey: ['entity-related', oid, eid],
    queryFn: () => ontologyApi.getEntityRelated(oid!, eid!),
    enabled: !!oid && !!eid,
  })

  const updateMut = useMutation({
    mutationFn: (data: Partial<Entity>) => ontologyApi.updateEntity(oid!, eid!, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['entity', oid, eid] })
      qc.invalidateQueries({ queryKey: ['entities', oid] })
      qc.invalidateQueries({ queryKey: ['graph', oid] })
      setEditing(false)
    },
  })

  // Updating a logic rule's linked_entities (for bidirectional link management)
  const updateLogicMut = useMutation({
    mutationFn: ({ lid, linked_entities }: { lid: string; linked_entities: string[] }) =>
      ontologyApi.updateLogic(oid!, lid, { linked_entities } as any),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['logic', oid] }),
  })

  // Updating an action's linked_entities
  const updateActionLinkMut = useMutation({
    mutationFn: ({ aid, linked_entities }: { aid: string; linked_entities: string[] }) =>
      ontologyApi.updateAction(oid!, aid, { linked_entities } as any),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['actions', oid] }),
  })

  const createRelMut = useMutation({
    mutationFn: (body: { source_entity: string; target_entity: string; type: string }) =>
      ontologyApi.createRelation(oid!, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['graph', oid] }),
  })

  const deleteRelMut = useMutation({
    mutationFn: (rid: string) => ontologyApi.deleteRelation(oid!, rid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['graph', oid] }),
  })

  const deleteMut = useMutation({
    mutationFn: () => ontologyApi.deleteEntity(oid!, eid!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['entities', oid] })
      qc.invalidateQueries({ queryKey: ['graph', oid] })
      qc.invalidateQueries({ queryKey: ['stats'] })
      navigate(`/ontologies/${oid}?tab=entities`)
    },
  })

  if (isLoading) return <div className="p-6 text-gray-400">加载中...</div>
  if (!entity) return <div className="p-6 text-red-500">实体未找到</div>

  const { labelCn, abbr } = parseEntityDisplay(entity)

  const nodes: GraphNode[] = (graph as any)?.nodes ?? []
  const edges: GraphEdge[] = (graph as any)?.edges ?? []
  const nodeMap: Record<string, string> = {}
  nodes.forEach(n => { nodeMap[n.data.id] = n.data.label })
  const incomingEdges = edges.filter(e => e.data.target === eid)
  const outgoingEdges = edges.filter(e => e.data.source === eid)

  // linked_entities 可能存实体显示名(简易 LLM)或实体类型名(Pipeline Mapping),
  // 两者都匹配。
  const matchKeys = [entity.name_cn, entity.name_en, entity.type].filter(Boolean) as string[]
  const linkedHit = (linked?: string[]) => (linked ?? []).some(x => matchKeys.includes(x))

  const relatedLogic = (allLogic as LogicRule[]).filter(r => linkedHit(r.linked_entities))
  const unlinkedLogic = (allLogic as LogicRule[]).filter(r => !linkedHit(r.linked_entities))

  const relatedActions = (allActions as Action[]).filter(a => linkedHit(a.linked_entities))
  const unlinkedActions = (allActions as Action[]).filter(a => !linkedHit(a.linked_entities))

  // Property helpers
  const props = (entity.properties ?? {}) as Record<string, unknown>

  const addProp = () => {
    if (!newKey.trim()) return
    const updated = { ...props, [newKey.trim()]: newVal.trim() }
    updateMut.mutate({ properties: updated as any })
    setNewKey(''); setNewVal('')
  }

  const deleteProp = (key: string) => {
    const updated = { ...props }
    delete updated[key]
    updateMut.mutate({ properties: updated as any })
  }

  // Logic link helpers
  const removeFromLogic = (rule: LogicRule) => {
    const next = (rule.linked_entities ?? []).filter(
      n => n !== entity.name_cn && n !== entity.name_en
    )
    updateLogicMut.mutate({ lid: rule.id, linked_entities: next })
  }
  const addToLogic = (ruleId: string) => {
    const rule = (allLogic as LogicRule[]).find(r => r.id === ruleId)
    if (!rule) return
    const next = [...(rule.linked_entities ?? []), entity.name_cn]
    updateLogicMut.mutate({ lid: rule.id, linked_entities: next })
  }

  // Action link helpers
  const removeFromAction = (action: Action) => {
    const next = (action.linked_entities ?? []).filter(
      n => n !== entity.name_cn && n !== entity.name_en
    )
    updateActionLinkMut.mutate({ aid: action.id, linked_entities: next })
  }
  const addToAction = (actionId: string) => {
    const action = (allActions as Action[]).find(a => a.id === actionId)
    if (!action) return
    const next = [...(action.linked_entities ?? []), entity.name_cn]
    updateActionLinkMut.mutate({ aid: action.id, linked_entities: next })
  }

  const formatDate = (s: string) => new Date(s).toLocaleString('zh-CN')

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={() => navigate(`/ontologies/${oid}?tab=entities`)}
          className="flex items-center gap-2 text-gray-500 hover:text-black text-sm">
          <ArrowLeft size={16} /> 返回实体列表
        </button>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <button onClick={() => setEditing(false)}
                className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm text-gray-600 hover:bg-gray-50">
                <X size={14} /> 取消
              </button>
              <button onClick={handleSubmit(d => updateMut.mutate(d))}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm">
                <Save size={14} /> 保存
              </button>
            </>
          ) : (
            <>
              <button onClick={() => { reset(entity); setEditing(true) }}
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

      {/* Basic Info */}
      <div className="bg-white border rounded-xl p-6">
        <h3 className="font-semibold mb-4">基本信息</h3>
        {editing ? (
          <form className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-xs text-gray-500 mb-1">中文名 *</label>
                <input {...register('name_cn', { required: true })} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">英文缩写</label>
                <input {...register('name_abbr')} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">英文名</label>
                <input {...register('name_en')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">SNOMED-CT ID</label>
                <input {...register('snomed_id')} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" placeholder="如 366979004" />
              </div>
              <div className="col-span-2">
                <label className="block text-xs text-gray-500 mb-1">Canonical ID</label>
                <input {...register('canonical_id')} className="w-full border rounded-lg px-3 py-2 text-sm font-mono" placeholder="如 symptom:depressed_mood" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">类型</label>
                <input {...register('type')} className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">置信度 (0-1)</label>
                <input {...register('confidence', { valueAsNumber: true })} type="number" step="0.01" min="0" max="1" className="w-full border rounded-lg px-3 py-2 text-sm" />
              </div>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">描述</label>
              <textarea {...register('description')} rows={3} className="w-full border rounded-lg px-3 py-2 text-sm resize-none" />
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div><p className="text-xs text-gray-500 mb-1">中文名</p><p className="text-sm font-medium">{labelCn}</p></div>
              <div><p className="text-xs text-gray-500 mb-1">英文缩写</p><p className="text-sm font-mono">{entity.name_abbr?.trim() || abbr || '—'}</p></div>
              <div><p className="text-xs text-gray-500 mb-1">英文名</p><p className="text-sm">{entity.name_en || '—'}</p></div>
              <div><p className="text-xs text-gray-500 mb-1">SNOMED-CT ID</p><p className="text-sm font-mono text-blue-600">{entity.snomed_id || '—'}</p></div>
              <div className="col-span-2"><p className="text-xs text-gray-500 mb-1">Canonical ID</p><p className="text-sm font-mono text-green-700">{entity.canonical_id || '—'}</p></div>
              <div><p className="text-xs text-gray-500 mb-1">类型</p><p className="text-sm">{entity.type || '—'}</p></div>
              <div><p className="text-xs text-gray-500 mb-1">版本</p><p className="text-sm font-mono">{entity.version}</p></div>
            </div>
            <div>
              <p className="text-xs text-gray-500 mb-1">置信度</p>
              <div className="flex items-center gap-3">
                <div className="w-40"><ConfidenceBar value={entity.confidence} /></div>
                <span className="text-sm text-gray-600">{Math.round(entity.confidence * 100)}%</span>
              </div>
            </div>
            <div><p className="text-xs text-gray-500 mb-1">描述</p><p className="text-sm text-gray-700">{entity.description || '—'}</p></div>
            <div className="grid grid-cols-2 gap-4 pt-2 border-t">
              <div><p className="text-xs text-gray-500 mb-1">创建时间</p><p className="text-xs text-gray-600">{formatDate(entity.created_at)}</p></div>
              <div><p className="text-xs text-gray-500 mb-1">更新时间</p><p className="text-xs text-gray-600">{formatDate(entity.updated_at)}</p></div>
            </div>
          </div>
        )}
      </div>

      {/* Properties — inline add/delete */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">实体属性</h3>
          <button onClick={() => setPropEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${propEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {propEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
          </button>
        </div>
        {Object.keys(props).length > 0 ? (
          <table className="w-full text-sm mb-3">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-3 py-2 text-left text-xs text-gray-500 font-medium">属性名</th>
                <th className="px-3 py-2 text-left text-xs text-gray-500 font-medium">值</th>
                {propEditing && <th className="w-8" />}
              </tr>
            </thead>
            <tbody>
              {Object.entries(props).map(([k, v]) => (
                <tr key={k} className="border-t">
                  <td className="px-3 py-2 font-mono text-xs text-gray-700">{k}</td>
                  <td className="px-3 py-2 text-gray-600 text-xs">{String(v)}</td>
                  {propEditing && (
                    <td className="px-2 py-2">
                      <button onClick={() => deleteProp(k)} className="text-gray-300 hover:text-red-500">
                        <X size={13} />
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="text-sm text-gray-400 mb-3">暂无属性</p>
        )}
        {propEditing && (
          <div className="flex items-center gap-2 border-t pt-3">
            <input value={newKey} onChange={e => setNewKey(e.target.value)} placeholder="属性名"
              className="flex-1 border rounded-lg px-2 py-1.5 text-xs" onKeyDown={e => e.key === 'Enter' && addProp()} />
            <input value={newVal} onChange={e => setNewVal(e.target.value)} placeholder="值"
              className="flex-1 border rounded-lg px-2 py-1.5 text-xs" onKeyDown={e => e.key === 'Enter' && addProp()} />
            <button onClick={addProp} disabled={!newKey.trim()}
              className="flex items-center gap-1 px-2.5 py-1.5 bg-black text-white rounded-lg text-xs disabled:opacity-40">
              <Plus size={12} /> 添加
            </button>
          </div>
        )}
      </div>

      {/* Related Entities (graph) — editable */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="font-semibold">关联实体（图关系）</h3>
            <p className="text-xs text-gray-400 mt-0.5">编辑后同步至知识图谱</p>
          </div>
          <button onClick={() => setGraphEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${graphEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {graphEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
          </button>
        </div>

        <div className="space-y-4">
          {/* Incoming edges (read-only — other entities point here, edit from their side) */}
          {incomingEdges.length > 0 && (
            <div>
              <p className="text-xs text-gray-500 mb-2">上游（其他实体指向此实体，在源实体页编辑）</p>
              <div className="flex flex-wrap gap-2">
                {incomingEdges.map(e => (
                  <Link key={e.data.id} to={`/ontologies/${oid}/entities/${e.data.source}`}
                    className="px-3 py-1.5 rounded-full text-xs bg-blue-50 text-blue-700 hover:bg-blue-100 border border-blue-200">
                    {nodeMap[e.data.source] || e.data.source} —[{e.data.label || 'relation'}]→
                  </Link>
                ))}
              </div>
            </div>
          )}

          {/* Outgoing edges — editable */}
          <div>
            <p className="text-xs text-gray-500 mb-2">下游（此实体指向其他实体）</p>
            {outgoingEdges.length === 0 && !graphEditing && (
              <p className="text-sm text-gray-400">暂无下游关系</p>
            )}
            <div className="flex flex-wrap gap-2">
              {outgoingEdges.map(e => (
                graphEditing ? (
                  <span key={e.data.id}
                    className="flex items-center gap-1 px-2.5 py-1 rounded-full text-xs border bg-green-50 text-green-700 border-green-200">
                    [{e.data.label || 'relation'}] → {nodeMap[e.data.target] || e.data.target}
                    <button onClick={() => deleteRelMut.mutate(e.data.id)}
                      className="text-green-400 hover:text-green-700 ml-0.5">
                      <X size={10} />
                    </button>
                  </span>
                ) : (
                  <Link key={e.data.id} to={`/ontologies/${oid}/entities/${e.data.target}`}
                    className="px-3 py-1.5 rounded-full text-xs bg-green-50 text-green-700 hover:bg-green-100 border border-green-200">
                    [{e.data.label || 'relation'}] → {nodeMap[e.data.target] || e.data.target}
                  </Link>
                )
              ))}
            </div>

            {/* Add new outgoing relation */}
            {graphEditing && (
              <div className="flex items-center gap-2 mt-3 pt-3 border-t">
                <select value={newRelTarget} onChange={e => setNewRelTarget(e.target.value)}
                  className="flex-1 border rounded-lg px-2 py-1.5 text-xs">
                  <option value="">— 选择目标实体 —</option>
                  {nodes.filter(n => n.data.id !== eid).map(n => (
                    <option key={n.data.id} value={n.data.id}>{n.data.label}</option>
                  ))}
                </select>
                <select value={newRelType} onChange={e => setNewRelType(e.target.value)}
                  className="border rounded-lg px-2 py-1.5 text-xs w-36">
                  {['关联','IS-A','PART-OF','INSTANCE-OF','supply','stores','processes'].map(t => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
                <button
                  disabled={!newRelTarget}
                  onClick={() => {
                    if (!newRelTarget) return
                    createRelMut.mutate({ source_entity: eid!, target_entity: newRelTarget, type: newRelType })
                    setNewRelTarget('')
                  }}
                  className="flex items-center gap-1 px-2.5 py-1.5 bg-black text-white rounded-lg text-xs disabled:opacity-40">
                  <Plus size={12} /> 添加
                </button>
              </div>
            )}
          </div>

          {incomingEdges.length === 0 && outgoingEdges.length === 0 && !graphEditing && (
            <p className="text-sm text-gray-400">暂无关联实体</p>
          )}
        </div>
      </div>

      {/* Related Logic Rules — inline link management */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">关联逻辑规则</h3>
          <button onClick={() => setLogicLinkEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${logicLinkEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {logicLinkEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
          </button>
        </div>
        <ChipEditor
          editing={logicLinkEditing}
          items={relatedLogic.map(r => ({ id: r.id, label: r.name_cn, href: `/ontologies/${oid}/logic/${r.id}` }))}
          onRemove={id => { const r = relatedLogic.find(x => x.id === id); if (r) removeFromLogic(r) }}
          availableOptions={unlinkedLogic.map(r => ({ id: r.id, label: r.name_cn }))}
          onAdd={addToLogic}
          color="orange"
        />
      </div>

      {/* Related Actions — inline link management */}
      <div className="bg-white border rounded-xl p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold">关联动作</h3>
          <button onClick={() => setActionLinkEditing(v => !v)}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs border ${actionLinkEditing ? 'bg-black text-white border-black' : 'text-gray-500 hover:bg-gray-50'}`}>
            {actionLinkEditing ? <><Check size={11} /> 完成</> : <><Pencil size={11} /> 编辑</>}
          </button>
        </div>
        <ChipEditor
          editing={actionLinkEditing}
          items={relatedActions.map(a => ({ id: a.id, label: a.name_cn, href: `/ontologies/${oid}/actions/${a.id}` }))}
          onRemove={id => { const a = relatedActions.find(x => x.id === id); if (a) removeFromAction(a) }}
          availableOptions={unlinkedActions.map(a => ({ id: a.id, label: a.name_cn }))}
          onAdd={addToAction}
          color="purple"
        />
      </div>

      {/* Related Logic Rules (from /related endpoint) */}
      {(relatedData?.logic ?? []).length > 0 && (
        <div className="bg-white border rounded-xl p-6">
          <div className="mt-0 border-t-0">
            <h3 className="text-sm font-medium text-gray-700 mb-3">
              关联逻辑规则（{relatedData!.logic.length}）
            </h3>
            <div className="space-y-2">
              {relatedData!.logic.map((lr: any) => (
                <Link
                  key={lr.id}
                  to={`/ontologies/${oid}/logic/${lr.id}`}
                  className="flex items-center justify-between p-3 border rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <div>
                    <span className="text-sm font-medium">{lr.name_cn}</span>
                    {lr.name_en && <span className="text-xs text-gray-400 ml-2">{lr.name_en}</span>}
                    {lr.formula && <p className="text-xs text-gray-500 mt-0.5 font-mono truncate max-w-sm">{lr.formula}</p>}
                  </div>
                  <span className="text-xs text-gray-400 shrink-0 ml-2">
                    置信度 {((lr.confidence ?? 1) * 100).toFixed(0)}%
                  </span>
                </Link>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Related Actions (from /related endpoint) */}
      {(relatedData?.actions ?? []).length > 0 && (
        <div className="bg-white border rounded-xl p-6">
          <h3 className="text-sm font-medium text-gray-700 mb-3">
            关联动作（{relatedData!.actions.length}）
          </h3>
          <div className="space-y-2">
            {relatedData!.actions.map((ac: any) => (
              <Link
                key={ac.id}
                to={`/ontologies/${oid}/actions/${ac.id}`}
                className="flex items-center justify-between p-3 border rounded-lg hover:bg-gray-50 transition-colors"
              >
                <div className="flex-1 min-w-0">
                  <span className="text-sm font-medium">{ac.name_cn}</span>
                  {ac.name_en && <span className="text-xs text-gray-400 ml-2">{ac.name_en}</span>}
                  {ac.description && <p className="text-xs text-gray-500 mt-0.5 truncate">{ac.description}</p>}
                </div>
                <span className="text-xs text-gray-400 shrink-0 ml-2">
                  置信度 {((ac.confidence ?? 1) * 100).toFixed(0)}%
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Delete Confirm */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-lg p-6 w-80">
            <h3 className="font-semibold mb-2">确认删除</h3>
            <p className="text-sm text-gray-600 mb-4">确定要删除实体「{entity.name_cn}」吗？此操作不可撤销。</p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setShowDeleteConfirm(false)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
              <button onClick={() => deleteMut.mutate()} className="px-4 py-2 bg-red-500 text-white rounded-lg text-sm">删除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
