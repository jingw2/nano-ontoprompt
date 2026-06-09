import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { apiClientV2 } from '@/api/client'
import { Search, Loader2 } from 'lucide-react'
import OntologySearchBox from '@/components/search/OntologySearchBox'
import cytoscape from 'cytoscape'

interface GraphData {
  nodes: Array<{ id: string; labels: string[]; properties: Record<string, unknown> }>
  edges: Array<{ id: string; source: string; target: string; type: string }>
  neo4j_available: boolean
}

interface GraphQuality {
  quality_score: number
  isolated_node_count: number
  duplicate_display_name_count: number
  orphan_relation_count: number
}

interface IntegrationStatus {
  neo4j: { available: boolean }
  chroma: { available: boolean; entity_count: number }
}

type QueryMode = 'natural' | 'cypher'

const TYPE_COLORS: Record<string, string> = {
  Supplier: '#3b82f6', supplier: '#3b82f6', 供应商: '#3b82f6',
  Product: '#10b981', product: '#10b981', 产品: '#10b981',
  Material: '#f59e0b', material: '#f59e0b', 物料: '#f59e0b',
  Organization: '#8b5cf6', organization: '#8b5cf6', 组织: '#8b5cf6',
  Order: '#ef4444', order: '#ef4444', 订单: '#ef4444',
  Customer: '#06b6d4', customer: '#06b6d4', 客户: '#06b6d4',
  Process: '#ec4899', process: '#ec4899', 流程: '#ec4899',
  Document: '#f97316', document: '#f97316', 文档: '#f97316',
  Disease: '#ef4444', 疾病: '#ef4444',
  Drug: '#10b981', 药物: '#10b981',
}

function nodeColor(labels: string[]): string {
  for (const l of labels) {
    if (TYPE_COLORS[l]) return TYPE_COLORS[l]
  }
  return '#6b7280'
}

export default function GraphTabV2({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate()
  const { i18n } = useTranslation()
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)

  const [graphData, setGraphData] = useState<GraphData | null>(null)
  const [loading, setLoading] = useState(true)
  const [hideIsolated, setHideIsolated] = useState(true)
  const [queryMode, setQueryMode] = useState<QueryMode>('natural')
  const [query, setQuery] = useState('')
  const [queryLoading, setQueryLoading] = useState(false)
  const [queryResult, setQueryResult] = useState<unknown[]>([])
  const [quality, setQuality] = useState<GraphQuality | null>(null)
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null)

  useEffect(() => {
    Promise.all([
      apiClientV2.get(`/ontologies/${ontologyId}/graph?limit=300`).catch(() => ({ nodes: [], edges: [], neo4j_available: false })),
      apiClientV2.get(`/ontologies/${ontologyId}/graph/quality`).catch(() => null),
      apiClientV2.get(`/ontologies/${ontologyId}/integrations/status`).catch(() => null),
    ])
      .then(([graph, q, status]: any[]) => {
        setGraphData(graph)
        setQuality(q)
        setIntegrations(status)
      })
      .finally(() => setLoading(false))
  }, [ontologyId])

  // Build and render Cytoscape graph whenever data or toggle changes
  useEffect(() => {
    if (!graphData || !containerRef.current) return

    const allNodes = graphData.nodes
    const allEdges = graphData.edges

    // Compute degree per node
    const degreeMap = new Map<string, number>()
    for (const n of allNodes) degreeMap.set(n.id, 0)
    for (const e of allEdges) {
      degreeMap.set(e.source, (degreeMap.get(e.source) ?? 0) + 1)
      degreeMap.set(e.target, (degreeMap.get(e.target) ?? 0) + 1)
    }

    const isolatedCount = Array.from(degreeMap.values()).filter(d => d === 0).length

    // Filter nodes if hiding isolated
    const visibleNodes = hideIsolated
      ? allNodes.filter(n => (degreeMap.get(n.id) ?? 0) > 0)
      : allNodes

    const visibleNodeIds = new Set(visibleNodes.map(n => n.id))

    // Only keep edges whose both endpoints are visible
    const visibleEdges = allEdges.filter(
      e => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target)
    )

    const cytoscapeNodes = visibleNodes.map(n => {
      const color = nodeColor(n.labels)
      const localizedName = i18n.language?.startsWith('zh')
        ? (n.properties?.name_cn || n.properties?.display_name || n.properties?.name)
        : (n.properties?.name_en || n.properties?.display_name || n.properties?.name_cn || n.properties?.name)
      const label = String(localizedName || n.labels[0] || n.id).slice(0, 20)
      const labelLen = label.length
      const size = labelLen > 8 ? 88 : labelLen > 5 ? 72 : 60
      return {
        data: {
          id: n.id,
          label,
          color,
          size,
          textMaxWidth: size - 12,
          degree: degreeMap.get(n.id) ?? 0,
          entityId: String(n.properties?.source_id || n.properties?.id || ''),
        }
      }
    })

    const cytoscapeEdges = visibleEdges.map(e => ({
      data: {
        id: e.id,
        source: e.source,
        target: e.target,
        label: e.type,
      }
    }))

    cyRef.current?.destroy()

    const cy = cytoscape({
      container: containerRef.current,
      elements: [...cytoscapeNodes, ...cytoscapeEdges],
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'background-color': 'data(color)',
            color: '#fff',
            'font-size': '11px',
            'font-weight': 'bold',
            'text-valign': 'center',
            'text-halign': 'center',
            width: 'data(size)' as any,
            height: 'data(size)' as any,
            'text-wrap': 'wrap',
            'text-max-width': 'data(textMaxWidth)' as any,
            'border-width': '0px',
            'text-outline-width': '2px',
            'text-outline-color': 'data(color)',
          }
        },
        {
          selector: 'node[degree = 0]',
          style: {
            opacity: 0.7,
            'font-size': '10px',
            'border-width': '1.5px',
            'border-color': 'data(color)',
            'border-opacity': 0.5,
          }
        },
        {
          selector: 'edge',
          style: {
            label: 'data(label)',
            'font-size': '10px',
            color: '#374151',
            'line-color': '#9ca3af',
            'target-arrow-color': '#9ca3af',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'text-background-color': '#ffffff',
            'text-background-opacity': 0.9,
            'text-background-padding': '2px',
            width: 1.5,
          }
        },
        {
          selector: ':selected',
          style: {
            'background-color': '#1d4ed8',
            'line-color': '#1d4ed8',
            'target-arrow-color': '#1d4ed8',
            'border-width': '3px',
            'border-color': '#fff',
          }
        },
      ],
      layout: {
        name: cytoscapeEdges.length > 0 ? 'breadthfirst' : 'cose',
        animate: false,
        directed: true,
        spacingFactor: 1.3,
        ...(cytoscapeEdges.length > 0 ? {} : {
          nodeRepulsion: () => 8000, idealEdgeLength: () => 120,
          gravity: 0.05, numIter: 1000, nodeDimensionsIncludeLabels: true,
        }),
      } as any,
    })

    cy.on('tap', 'node', evt => {
      const nodeData = evt.target.data()
      cy.elements().removeClass('highlighted dimmed')
      evt.target.addClass('highlighted')
      evt.target.neighborhood().addClass('highlighted')
      evt.target.neighborhood().edges().addClass('highlighted')
      cy.elements().not('.highlighted').addClass('dimmed')
    })

    cy.on('tap', function(evt) {
      if (evt.target === cy) cy.elements().removeClass('highlighted dimmed')
    })

    // 双击节点 → 跳转实体详情页
    cy.on('dblclick', 'node', evt => {
      const nodeData = evt.target.data()
      const nid = nodeData.entityId || nodeData.id
      if (nid) navigate(`/ontologies/${ontologyId}/entities/${nid}`)
    })

    cyRef.current = cy

    return () => {
      cy.destroy()
      cyRef.current = null
    }
  }, [graphData, hideIsolated, ontologyId, navigate, i18n.language])

  const handleQuery = async () => {
    if (!query.trim()) return
    setQueryLoading(true)
    setQueryResult([])
    try {
      if (queryMode === 'natural') {
        const res: any = await apiClientV2.post(`/ontologies/${ontologyId}/graph/ask`, { question: query })
        setQueryResult(res.results || [])
      } else {
        const res: any = await apiClientV2.post(`/ontologies/${ontologyId}/graph/cypher`, { query })
        setQueryResult(res.results || [])
      }
    } catch (err: any) {
      setQueryResult([{ error: err?.detail || err?.message || '查询失败' }])
    } finally {
      setQueryLoading(false)
    }
  }

  if (loading) return <div className="text-gray-400 text-sm py-8 text-center">加载中...</div>

  const neo4jOk = graphData?.neo4j_available
  const nodes = graphData?.nodes ?? []
  const edges = graphData?.edges ?? []

  // Count isolated nodes for toggle label
  const degreeMap = new Map<string, number>()
  for (const n of nodes) degreeMap.set(n.id, 0)
  for (const e of edges) {
    degreeMap.set(e.source, (degreeMap.get(e.source) ?? 0) + 1)
    degreeMap.set(e.target, (degreeMap.get(e.target) ?? 0) + 1)
  }
  const isolatedCount = Array.from(degreeMap.values()).filter(d => d === 0).length

  // Build legend from visible node labels
  const labelColorMap = new Map<string, string>()
  for (const n of nodes) {
    for (const l of n.labels) {
      if (!labelColorMap.has(l)) labelColorMap.set(l, nodeColor([l]))
    }
  }

  const hasData = nodes.length > 0

  return (
    <div className="space-y-4">
      {/* 状态栏 */}
      <div className="flex items-center gap-3 text-xs text-gray-500 flex-wrap">
        <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-full border ${neo4jOk ? 'border-green-200 bg-green-50 text-green-700' : 'border-gray-200 bg-gray-50'}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${neo4jOk ? 'bg-green-500' : 'bg-gray-300'}`} />
          {neo4jOk ? 'Neo4j 已连接' : 'Neo4j 未连接'}
        </span>
        <span>节点 {nodes.length}</span>
        <span>边 {edges.length}</span>
        {quality && (
          <>
            <span className={`px-2 py-1 rounded-full border ${quality.quality_score >= 0.8 ? 'border-green-200 bg-green-50 text-green-700' : 'border-amber-200 bg-amber-50 text-amber-700'}`}>
              图质量 {(quality.quality_score * 100).toFixed(0)}%
            </span>
            <span>重复名 {quality.duplicate_display_name_count}</span>
            <span>孤立 {quality.isolated_node_count}</span>
            <span>孤儿关系 {quality.orphan_relation_count}</span>
          </>
        )}
        {integrations && (
          <span className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-full border ${integrations.chroma.available ? 'border-green-200 bg-green-50 text-green-700' : 'border-gray-200 bg-gray-50'}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${integrations.chroma.available ? 'bg-green-500' : 'bg-gray-300'}`} />
            {integrations.chroma.available ? `Chroma ${integrations.chroma.entity_count}` : 'Chroma 未连接'}
          </span>
        )}
        {isolatedCount > 0 && (
          <button
            onClick={() => setHideIsolated(h => !h)}
            className="px-2 py-1 rounded border border-gray-200 bg-white hover:bg-gray-50 text-gray-600 transition-colors"
          >
            {hideIsolated ? `显示 ${isolatedCount} 个孤立节点` : `隐藏 ${isolatedCount} 个孤立节点`}
          </button>
        )}
      </div>

      {/* 图例 */}
      {labelColorMap.size > 0 && (
        <div className="flex flex-wrap gap-2">
          {Array.from(labelColorMap.entries()).map(([label, color]) => (
            <span key={label} className="flex items-center gap-1 text-xs text-gray-600 bg-white border rounded-full px-2 py-0.5">
              <span className="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
              {label}
            </span>
          ))}
        </div>
      )}

      {/* Cytoscape 图谱画布 */}
      {hasData ? (
        <div ref={containerRef} className="border rounded-xl bg-white" style={{ height: 500 }} />
      ) : (
        <div className="border rounded-xl bg-gray-50 h-64 flex items-center justify-center">
          <p className="text-sm text-gray-400">
            {neo4jOk ? '该本体暂无图谱数据' : '启动 Neo4j 服务后图谱将在此显示'}
          </p>
        </div>
      )}

      {/* 查询区域 */}
      {neo4jOk && (
        <div className="bg-white border rounded-xl p-4 space-y-3">
          <div className="flex items-center gap-2">
            <div className="flex border rounded overflow-hidden text-xs">
              {(['natural', 'cypher'] as const).map(m => (
                <button
                  key={m}
                  onClick={() => { setQueryMode(m); setQueryResult([]) }}
                  className={`px-3 py-1.5 font-medium transition-colors ${
                    queryMode === m ? 'bg-black text-white' : 'bg-white text-gray-500 hover:bg-gray-50'
                  }`}
                >
                  {m === 'natural' ? '自然语言' : 'Cypher'}
                </button>
              ))}
            </div>
            <span className="text-xs text-gray-400">
              {queryMode === 'natural' ? '用中文提问，自动转为图查询' : '直接输入 Cypher 语句'}
            </span>
          </div>

          <div className="flex gap-2">
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleQuery()}
              placeholder={queryMode === 'natural'
                ? '例: 华为的供应链上下游有哪些？'
                : 'MATCH (n) RETURN n LIMIT 10'}
              className={`flex-1 border rounded-lg px-3 py-2 text-sm ${queryMode === 'cypher' ? 'font-mono' : ''}`}
            />
            <button
              onClick={handleQuery}
              disabled={queryLoading}
              className="px-3 py-2 bg-black text-white rounded-lg hover:bg-gray-800 disabled:opacity-50 flex items-center gap-1.5"
            >
              {queryLoading ? <Loader2 size={14} className="animate-spin" /> : <Search size={14} />}
              <span className="text-sm">查询</span>
            </button>
          </div>

          {queryResult.length > 0 && (
            <pre className="text-xs bg-gray-50 border rounded-lg p-3 overflow-auto max-h-40">
              {JSON.stringify(queryResult, null, 2)}
            </pre>
          )}
        </div>
      )}

      {/* 语义搜索 */}
      {neo4jOk && (
        <div className="bg-white border rounded-xl p-4">
          <p className="text-xs font-medium text-gray-600 mb-3">语义搜索</p>
          <OntologySearchBox ontologyId={ontologyId} />
        </div>
      )}
    </div>
  )
}
