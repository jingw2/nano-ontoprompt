import { useState, useEffect } from 'react'
import { Database, BarChart3, ChevronDown, ChevronUp, Eye, GitBranch } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

interface Dataset {
  id: string
  name: string
  kind: string
}

interface SchemaColumn {
  name: string
  type: string
  sample_values: string[]
}

interface Version {
  id: string
  version_no: number
  rowcount: number | null
  storage_uri: string
}

type SubTab = 'schema' | 'preview' | 'versions'

const KIND_META: Record<string, { label: string; color: string }> = {
  structured:   { label: '结构化',   color: 'bg-blue-50 text-blue-600 border-blue-200' },
  semi:         { label: '半结构化', color: 'bg-amber-50 text-amber-600 border-amber-200' },
  unstructured: { label: '非结构化', color: 'bg-purple-50 text-purple-600 border-purple-200' },
  curated:      { label: 'Curated',  color: 'bg-green-50 text-green-600 border-green-200' },
}

export default function DatasetsTab() {
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [activeSub, setActiveSub] = useState<Record<string, SubTab>>({})
  const [schemas, setSchemas] = useState<Record<string, SchemaColumn[]>>({})
  const [previews, setPreviews] = useState<Record<string, Record<string, unknown>[]>>({})
  const [versions, setVersions] = useState<Record<string, Version[]>>({})

  useEffect(() => {
    apiClientV2.get('/datasets')
      .then((res: unknown) => {
        const arr = Array.isArray(res) ? res : ((res as Record<string, unknown>)?.data ?? [])
        setDatasets(arr as Dataset[])
      })
      .catch(() => setDatasets([]))
      .finally(() => setLoading(false))
  }, [])

  const loadSubTab = async (id: string, tab: SubTab) => {
    if (tab === 'schema' && !schemas[id]) {
      try {
        const r = await apiClientV2.get(`/datasets/${id}/schema`) as { columns: SchemaColumn[] }
        setSchemas(p => ({ ...p, [id]: r.columns ?? [] }))
      } catch {
        setSchemas(p => ({ ...p, [id]: [] }))
      }
    }
    if (tab === 'preview' && !previews[id]) {
      try {
        const vers = await apiClientV2.get(`/datasets/${id}/versions`) as Version[]
        const list = Array.isArray(vers) ? vers : []
        if (list.length > 0) {
          const latest = list[list.length - 1]
          const pr = await apiClientV2.get(`/datasets/${id}/versions/${latest.version_no}/preview?limit=20`) as { rows?: Record<string, unknown>[] }
          setPreviews(p => ({ ...p, [id]: pr.rows ?? [] }))
        } else {
          setPreviews(p => ({ ...p, [id]: [] }))
        }
      } catch {
        setPreviews(p => ({ ...p, [id]: [] }))
      }
    }
    if (tab === 'versions' && !versions[id]) {
      try {
        const r = await apiClientV2.get(`/datasets/${id}/versions`) as Version[]
        setVersions(p => ({ ...p, [id]: Array.isArray(r) ? r : [] }))
      } catch {
        setVersions(p => ({ ...p, [id]: [] }))
      }
    }
  }

  const handleExpand = async (id: string) => {
    if (expanded === id) { setExpanded(null); return }
    setExpanded(id)
    const tab = activeSub[id] ?? 'schema'
    await loadSubTab(id, tab)
  }

  const handleSubTab = async (id: string, tab: SubTab) => {
    setActiveSub(p => ({ ...p, [id]: tab }))
    await loadSubTab(id, tab)
  }

  if (loading) return <div className="text-gray-400 text-sm p-4">加载中...</div>

  if (datasets.length === 0) return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-lg font-semibold">原始数据集</h2>
      </div>
      <div className="border-2 border-dashed rounded-xl p-8 text-center text-gray-400">
        <BarChart3 size={28} className="mx-auto mb-2 opacity-30" />
        <p className="text-sm">暂无原始数据集</p>
        <p className="text-xs mt-1">在 Connections 中上传文件后，数据将在此显示</p>
      </div>
    </div>
  )

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-lg font-semibold">原始数据集</h2>
          <p className="text-xs text-gray-400 mt-0.5">{datasets.length} 个数据集</p>
        </div>
      </div>

      <div className="border rounded-xl overflow-hidden">
        {datasets.map(ds => {
          const meta = KIND_META[ds.kind] ?? { label: ds.kind, color: 'bg-gray-50 text-gray-600 border-gray-200' }
          const isOpen = expanded === ds.id
          const tab = activeSub[ds.id] ?? 'schema'

          return (
            <div key={ds.id} className="border-b last:border-b-0">
              <div
                className="p-4 flex items-center gap-3 cursor-pointer hover:bg-gray-50 transition-colors"
                onClick={() => handleExpand(ds.id)}
              >
                <div className="w-8 h-8 bg-gray-100 rounded-lg flex items-center justify-center">
                  <Database size={14} className="text-gray-500" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="font-medium text-sm">{ds.name}</p>
                  <p className="text-xs text-gray-400 font-mono">{ds.id.slice(0, 8)}</p>
                </div>
                <span className={`text-xs font-medium px-2 py-0.5 rounded border ${meta.color}`}>
                  {meta.label}
                </span>
                {isOpen
                  ? <ChevronUp size={14} className="text-gray-400 shrink-0" />
                  : <ChevronDown size={14} className="text-gray-400 shrink-0" />
                }
              </div>

              {isOpen && (
                <div className="border-t bg-gray-50">
                  <div className="flex border-b bg-white">
                    {([
                      ['schema', 'Schema', <BarChart3 size={12} />],
                      ['preview', '数据预览', <Eye size={12} />],
                      ['versions', '版本历史', <GitBranch size={12} />],
                    ] as [SubTab, string, React.ReactNode][]).map(([k, label, icon]) => (
                      <button
                        key={k}
                        onClick={e => { e.stopPropagation(); handleSubTab(ds.id, k) }}
                        className={`flex items-center gap-1.5 px-4 py-2.5 text-xs border-b-2 transition-colors
                          ${tab === k ? 'border-black text-black font-medium' : 'border-transparent text-gray-500 hover:text-black'}`}
                      >
                        {icon} {label}
                      </button>
                    ))}
                  </div>

                  <div className="p-4">
                    {tab === 'schema' && (
                      <div>
                        {(schemas[ds.id] ?? []).length === 0
                          ? <p className="text-xs text-gray-400">暂无 Schema 信息</p>
                          : (
                            <div className="overflow-x-auto">
                              <table className="w-full text-xs">
                                <thead>
                                  <tr className="border-b text-gray-500">
                                    <th className="text-left py-1 pr-6 font-medium">列名</th>
                                    <th className="text-left py-1 pr-6 font-medium">类型</th>
                                    <th className="text-left py-1 font-medium">样本值</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {(schemas[ds.id] ?? []).map((col, i) => (
                                    <tr key={i} className="border-b last:border-0 hover:bg-white">
                                      <td className="py-1.5 pr-6 font-mono font-medium">{col.name}</td>
                                      <td className="py-1.5 pr-6 text-blue-600">{col.type}</td>
                                      <td className="py-1.5 text-gray-500 truncate max-w-xs">{col.sample_values?.join(', ')}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )
                        }
                      </div>
                    )}

                    {tab === 'preview' && (
                      <div>
                        {(previews[ds.id] ?? []).length === 0
                          ? <p className="text-xs text-gray-400">暂无预览数据</p>
                          : (
                            <div className="overflow-x-auto">
                              <table className="text-xs w-full min-w-max">
                                <thead>
                                  <tr className="border-b text-gray-500">
                                    {Object.keys(previews[ds.id][0]).map(k => (
                                      <th key={k} className="text-left py-1 pr-4 font-medium whitespace-nowrap">{k}</th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {previews[ds.id].map((row, i) => (
                                    <tr key={i} className="border-b last:border-0 hover:bg-white">
                                      {Object.values(row).map((v, j) => (
                                        <td key={j} className="py-1.5 pr-4 text-gray-700 max-w-[8rem] truncate">
                                          {String(v ?? '')}
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )
                        }
                      </div>
                    )}

                    {tab === 'versions' && (
                      <div className="space-y-2">
                        {(versions[ds.id] ?? []).length === 0
                          ? <p className="text-xs text-gray-400">暂无版本记录</p>
                          : (versions[ds.id] ?? []).map(v => (
                            <div key={v.id} className="flex items-center gap-3 text-xs bg-white border rounded-lg px-3 py-2">
                              <span className="text-gray-500 font-medium">v{v.version_no}</span>
                              <span className="text-gray-700">{v.rowcount != null ? `${v.rowcount} 行` : '行数未知'}</span>
                              <span className="text-gray-400 truncate">{v.storage_uri}</span>
                            </div>
                          ))
                        }
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
