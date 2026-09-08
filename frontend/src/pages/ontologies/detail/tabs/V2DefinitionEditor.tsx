import { useEffect, useState } from 'react'
import { apiClientV2 } from '@/api/client'

type DefinitionKind = 'logic' | 'actions'

interface Definition {
  id: string
  name: string
  description?: string
  logic_type?: string
  action_category?: string
  expression?: Record<string, unknown>
  effects?: unknown[]
  parameters?: unknown[]
  backed_by_function?: string | null
}

interface Props {
  ontologyId: string
  kind: DefinitionKind
}

function label(kind: DefinitionKind) {
  return kind === 'logic' ? '公式定义（运行时）' : '函数定义（运行时）'
}

export default function V2DefinitionEditor({ ontologyId, kind }: Props) {
  const [items, setItems] = useState<Definition[]>([])
  const [selected, setSelected] = useState<Definition | null>(null)
  const [definition, setDefinition] = useState('{}')
  const [functionRef, setFunctionRef] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  const load = async () => {
    try {
      const rows = await apiClientV2.get<Definition[]>(`/ontologies/${ontologyId}/${kind}`)
      setItems(Array.isArray(rows) ? rows : [])
    } catch {
      setError('运行时定义加载失败')
    }
  }

  useEffect(() => { void load() }, [ontologyId, kind]) // eslint-disable-line react-hooks/exhaustive-deps

  const select = (item: Definition) => {
    setSelected(item)
    setDefinition(JSON.stringify(kind === 'logic' ? item.expression ?? {} : item.effects ?? [], null, 2))
    setFunctionRef(item.backed_by_function ?? '')
    setError('')
  }

  const save = async () => {
    if (!selected) return
    let parsed: Record<string, unknown> | unknown[]
    try {
      parsed = JSON.parse(definition) as Record<string, unknown> | unknown[]
    } catch {
      setError('定义必须是有效 JSON')
      return
    }
    if ((kind === 'logic' && (Array.isArray(parsed) || parsed === null)) || (kind === 'actions' && !Array.isArray(parsed))) {
      setError(kind === 'logic' ? '公式必须是 JSON 对象' : '函数 effects 必须是 JSON 数组')
      return
    }
    setSaving(true)
    setError('')
    try {
      const body = kind === 'logic'
        ? { expression: parsed }
        : { effects: parsed, backed_by_function: functionRef || null }
      await apiClientV2.patch(`/ontologies/${ontologyId}/${kind}/${selected.id}`, body)
      await load()
    } catch {
      setError('保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="bg-white border rounded-lg p-4 space-y-3" data-testid={`v2-${kind}-editor`}>
      <div>
        <h3 className="font-medium text-sm">{label(kind)}</h3>
        <p className="text-xs text-gray-500 mt-1">修改后会标记为未发布；请在发布生命周期中发布新版本。</p>
      </div>
      {items.length === 0 ? <p className="text-sm text-gray-400">暂无运行时定义</p> : (
        <div className="flex flex-wrap gap-2">
          {items.map(item => <button key={item.id} type="button" onClick={() => select(item)}
            className="px-2 py-1 border rounded text-xs hover:bg-gray-50">{item.name}</button>)}
        </div>
      )}
      {selected && <>
        <textarea data-testid={`v2-${kind}-definition`} value={definition} onChange={event => setDefinition(event.target.value)}
          rows={8} className="w-full border rounded p-2 font-mono text-xs" />
        {kind === 'actions' && <input data-testid="v2-actions-function-ref" value={functionRef}
          onChange={event => setFunctionRef(event.target.value)} placeholder="函数引用（可选）"
          className="w-full border rounded px-2 py-1.5 text-sm" />}
        <button data-testid={`v2-${kind}-save`} type="button" disabled={saving} onClick={() => void save()}
          className="px-3 py-1.5 bg-black text-white rounded text-xs disabled:opacity-50">保存运行时定义</button>
      </>}
      {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    </section>
  )
}
