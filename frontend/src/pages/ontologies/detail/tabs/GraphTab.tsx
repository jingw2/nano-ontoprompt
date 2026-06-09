import { useEffect, useRef, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { ontologyApi } from '@/api/ontologies'
import cytoscape from 'cytoscape'
import { ZoomIn, ZoomOut, Maximize2, Search, X } from 'lucide-react'

// Entity type → node color
const TYPE_COLORS: Record<string, string> = {
  // Supply chain
  '供应商': '#3b82f6', 'Supplier': '#3b82f6', 'supplier': '#3b82f6',
  '产品': '#10b981',   'Product': '#10b981',   'product': '#10b981',
  '物料': '#f59e0b',   'Material': '#f59e0b',  'material': '#f59e0b',
  '仓库': '#8b5cf6',   'Warehouse': '#8b5cf6', 'warehouse': '#8b5cf6',
  '采购订单': '#ef4444', 'Document': '#ef4444', 'PurchaseOrder': '#ef4444',
  '类别': '#ec4899',   'Category': '#ec4899',  'category': '#ec4899',
  '工艺流程': '#06b6d4', 'Process': '#06b6d4', 'process': '#06b6d4',
  '组织': '#3b82f6',   'Organization': '#3b82f6',
  // Medical
  '疾病': '#ef4444',   'Disease': '#ef4444',
  '药物': '#10b981',   'Drug': '#10b981',
  '症状': '#f59e0b',   'Symptom': '#f59e0b',
  '治疗': '#8b5cf6',   'Treatment': '#8b5cf6',
  '设施': '#06b6d4',   'Facility': '#06b6d4',
}

function typeColor(type: string): string {
  return TYPE_COLORS[type] || '#6b7280'
}

// Relation type → edge color
const EDGE_COLORS: Record<string, string> = {
  'IS-A': '#7c3aed', 'PART-OF': '#db2777', 'CONTAINS': '#db2777',
  'supply': '#3b82f6', '供应': '#3b82f6',
  'stores': '#10b981', '存储': '#10b981',
  'processes': '#f59e0b', '处理': '#f59e0b',
  '关联': '#9ca3af',
}

function edgeColor(type: string): string {
  return EDGE_COLORS[type] || '#9ca3af'
}

export default function GraphTab({ ontologyId }: { ontologyId: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [selected, setSelected] = useState<any>(null)
  const [info, setInfo] = useState<string>('')
  const [layout, setLayout] = useState<'cose' | 'breadthfirst' | 'circle'>('cose')
  const [legendTypes, setLegendTypes] = useState<{ type: string; color: string }[]>([])
  const [initError, setInitError] = useState<string | null>(null)
  const [searchQ, setSearchQ] = useState('')
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['graph', ontologyId],
    queryFn: () => ontologyApi.getGraph(ontologyId) as any,
  })

  const deleteMut = useMutation({
    mutationFn: (rid: string) => ontologyApi.deleteRelation(ontologyId, rid),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['graph', ontologyId] }),
  })

  useEffect(() => {
    setInitError(null)
    if (!containerRef.current || !data) return

    // Sanitize: drop nodes with null/empty id or label, deduplicate by id
    const rawNodes = (data.nodes || []) as any[]
    const rawEdges = (data.edges || []) as any[]

    const seenNodeIds = new Set<string>()
    const nodes = rawNodes.filter((n: any) => {
      if (!n.data?.id || !n.data?.label) return false
      if (seenNodeIds.has(n.data.id)) return false
      seenNodeIds.add(n.data.id)
      return true
    })

    const seenEdgeIds = new Set<string>()
    const edges = rawEdges.filter((e: any) => {
      if (!e.data?.id || !e.data?.source || !e.data?.target) return false
      if (e.data.source === e.data.target) return false  // skip self-loops
      if (!seenNodeIds.has(e.data.source) || !seenNodeIds.has(e.data.target)) return false
      if (seenEdgeIds.has(e.data.id)) return false
      seenEdgeIds.add(e.data.id)
      return true
    })

    // Collect unique types for legend
    const typeSet = new Map<string, string>()
    nodes.forEach((n: any) => {
      const t = n.data.type || '未分类'
      if (!typeSet.has(t)) typeSet.set(t, typeColor(t))
    })
    setLegendTypes(Array.from(typeSet.entries()).map(([type, color]) => ({ type, color })))

    if (cyRef.current) cyRef.current.destroy()

    const layoutOptions: Record<string, any> = {
      cose: {
        name: 'cose',
        animate: false,
        fit: false,
        padding: 60,
        nodeRepulsion: () => 12000,
        idealEdgeLength: () => 160,
        gravity: 1.5,
        numIter: 1200,
        initialTemp: 500,
        coolingFactor: 0.99,
        componentSpacing: 80,
      },
      breadthfirst: {
        name: 'breadthfirst',
        animate: false,
        fit: true,
        padding: 60,
        spacingFactor: 1.75,
        directed: false,
        circle: false,
      },
      circle: {
        name: 'circle',
        animate: false,
        fit: true,
        padding: 60,
        spacingFactor: 1.5,
      },
    }

    // Compute per-node size based on label length
    const nodeElements = nodes.map((n: any) => {
      const deg = edges.filter((e: any) =>
        e.data.source === n.data.id || e.data.target === n.data.id
      ).length
      const labelLen = (n.data.label || '').length
      const size = labelLen > 8 ? 96 : labelLen > 5 ? 80 : 68
      return {
        data: {
          ...n.data,
          color: typeColor(n.data.type || ''),
          degree: deg,
          size,
          textMaxWidth: size - 12,
        }
      }
    })

    let cy: cytoscape.Core
    try {
    cy = cytoscape({
      container: containerRef.current,
      elements: [
        ...nodeElements,
        ...edges.map((e: any) => ({
          data: {
            ...e.data,
            edgeColor: edgeColor(e.data.label || e.data.type || ''),
          }
        })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            'label': 'data(label)',
            'background-color': 'data(color)',
            'color': '#fff',
            'font-size': '11px',
            'font-weight': 'bold',
            'text-valign': 'center',
            'text-halign': 'center',
            'width': 'data(size)' as any,
            'height': 'data(size)' as any,
            'text-wrap': 'wrap',
            'text-max-width': 'data(textMaxWidth)' as any,
            'border-width': '0px',
            'text-outline-width': '2px',
            'text-outline-color': 'data(color)',
          }
        },
        {
          // Isolated nodes — slightly smaller, lower opacity, but still readable
          selector: 'node[degree = 0]',
          style: {
            'opacity': 0.75,
            'font-size': '10px',
            'border-width': '1.5px',
            'border-color': 'data(color)',
            'border-opacity': 0.5,
          }
        },
        {
          selector: 'edge',
          style: {
            'label': 'data(label)',
            'font-size': '11px',
            'font-weight': 500,
            'color': '#1f2937',
            'line-color': 'data(edgeColor)',
            'target-arrow-color': 'data(edgeColor)',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'line-style': 'solid',
            'text-background-color': '#ffffff',
            'text-background-opacity': 0.92,
            'text-background-padding': '3px',
            'text-border-width': 1,
            'text-border-color': '#d1d5db',
            'text-border-opacity': 1,
            'width': 1.8,
          }
        },
        {
          selector: 'edge[confidence < 0.7]',
          style: { 'line-style': 'dashed', 'opacity': 0.7 }
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
        {
          selector: '.search-dim',
          style: { 'opacity': 0.15 }
        },
        {
          selector: '.search-match',
          style: { 'border-width': '3px', 'border-color': '#f59e0b', 'opacity': 1 }
        }
      ],
      // No layout in constructor — run manually so we can post-process isolated nodes
    })

    // Run layout first (animate:false makes it synchronous for cose)
    cy.layout(layoutOptions[layout]).run()

    // After cose layout: reposition isolated nodes into a compact grid below connected subgraph
    if (layout === 'cose') {
      const isolated = cy.nodes().filter(n => n.degree(false) === 0)
      if (isolated.length > 0) {
        const connected = cy.nodes().filter(n => n.degree(false) > 0)
        let gridStartX = 80
        let gridStartY = 80
        if (connected.length > 0) {
          const bb = connected.boundingBox({})
          gridStartX = bb.x1
          gridStartY = bb.y2 + 100
        }
        const cols = Math.max(3, Math.ceil(Math.sqrt(isolated.length * 1.6)))
        const spacing = 110
        isolated.forEach((node, i) => {
          const row = Math.floor(i / cols)
          const col = i % cols
          node.position({ x: gridStartX + col * spacing, y: gridStartY + row * spacing })
        })
      }
    }

    cy.fit(undefined, 50)

    cy.on('tap', 'node', (e) => {
      const d = e.target.data()
      setSelected(d)
      setInfo(`${d.label}（${d.type || '未分类'}）置信度 ${Math.round((d.confidence || 1) * 100)}%`)
    })

    cy.on('tap', 'edge', (e) => {
      const d = e.target.data()
      setSelected(d)
      setInfo(`关系类型: ${d.label} — 置信度 ${Math.round((d.confidence || 1) * 100)}%`)
    })

    cy.on('tap', (e) => {
      if (e.target === cy) { setSelected(null); setInfo('') }
    })

    cyRef.current = cy
    } catch (err) {
      console.error('Cytoscape init error:', err)
      setInitError(err instanceof Error ? err.message : '图谱初始化失败，请检查数据格式')
      return
    }

    return () => { if (cyRef.current) { cyRef.current.destroy(); cyRef.current = null } }
  }, [data, layout])

  // 搜索高亮：匹配节点正常显示，其余节点半透明
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    const q = searchQ.trim().toLowerCase()
    if (!q) {
      cy.elements().removeClass('search-dim').removeClass('search-match')
      return
    }
    cy.nodes().forEach(n => {
      const label = (n.data('label') || '').toLowerCase()
      const type = (n.data('type') || '').toLowerCase()
      if (label.includes(q) || type.includes(q)) {
        n.addClass('search-match').removeClass('search-dim')
      } else {
        n.addClass('search-dim').removeClass('search-match')
      }
    })
    cy.edges().forEach(e => {
      const src = cy.getElementById(e.data('source'))
      const tgt = cy.getElementById(e.data('target'))
      if (src.hasClass('search-match') || tgt.hasClass('search-match')) {
        e.removeClass('search-dim')
      } else {
        e.addClass('search-dim')
      }
    })
  }, [searchQ])

  if (isLoading) return <div className="text-gray-400 text-center py-12">加载图谱中...</div>
  if (initError) return (
    <div className="bg-red-50 border border-red-200 rounded-lg p-8 text-center">
      <p className="text-red-600 font-medium mb-2">知识图谱渲染失败</p>
      <p className="text-red-400 text-sm font-mono mb-4">{initError}</p>
      <button onClick={() => setInitError(null)} className="px-3 py-1.5 text-sm border border-red-300 text-red-500 rounded-lg hover:bg-red-100">重试</button>
    </div>
  )

  const meta = data?.meta as any
  const isEmpty = !data?.nodes?.length

  return (
    <div className="space-y-3">
      {/* Search bar */}
      <div className="relative">
        <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 pointer-events-none" />
        <input
          value={searchQ}
          onChange={e => setSearchQ(e.target.value)}
          placeholder="搜索节点（名称 / 类型）…"
          className="w-full border rounded-lg pl-9 pr-8 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-black"
        />
        {searchQ && (
          <button onClick={() => setSearchQ('')}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
            <X size={14} />
          </button>
        )}
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-2 text-sm text-gray-600">
          <span className="font-medium">节点 {meta?.entity_count ?? 0}</span>
          <span className="text-gray-300">|</span>
          <span className="font-medium">边 {meta?.relation_count ?? 0}</span>
        </div>

        {/* Layout selector */}
        <div className="flex items-center gap-1 text-sm border rounded px-2 py-1">
          <span className="text-gray-500 text-xs mr-1">布局</span>
          {(['cose', 'breadthfirst', 'circle'] as const).map(l => (
            <button key={l} onClick={() => setLayout(l)}
              className={`px-2 py-0.5 rounded text-xs ${layout === l ? 'bg-blue-600 text-white' : 'text-gray-600 hover:bg-gray-100'}`}>
              {l === 'cose' ? '力导向' : l === 'breadthfirst' ? '层级' : '圆形'}
            </button>
          ))}
        </div>

        {/* Zoom controls */}
        {cyRef.current && (
          <div className="flex items-center gap-1">
            <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 1.2)}
              className="p-1 rounded hover:bg-gray-100 text-gray-600"><ZoomIn size={15} /></button>
            <button onClick={() => cyRef.current?.zoom(cyRef.current.zoom() * 0.8)}
              className="p-1 rounded hover:bg-gray-100 text-gray-600"><ZoomOut size={15} /></button>
            <button onClick={() => cyRef.current?.fit(undefined, 50)}
              className="p-1 rounded hover:bg-gray-100 text-gray-600"><Maximize2 size={15} /></button>
          </div>
        )}

        {info && <span className="bg-blue-50 text-blue-700 px-3 py-1 rounded-full text-sm">{info}</span>}
        {selected?.source && (
          <button onClick={() => deleteMut.mutate(selected.id)}
            className="text-red-500 hover:underline text-xs ml-auto">删除此关系</button>
        )}
      </div>

      {/* Legend */}
      {legendTypes.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {legendTypes.map(({ type, color }) => (
            <span key={type} className="flex items-center gap-1 text-xs text-gray-600 bg-white border rounded-full px-2 py-0.5">
              <span className="inline-block w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ backgroundColor: color }} />
              {type}
            </span>
          ))}
        </div>
      )}

      {isEmpty ? (
        <div className="bg-white border rounded-lg h-96 flex items-center justify-center text-gray-400">
          <p>暂无图谱数据 — 请先上传文件并执行 LLM 提取</p>
        </div>
      ) : (
        <div ref={containerRef} className="bg-white border rounded-lg" style={{ height: '580px' }} />
      )}
    </div>
  )
}
