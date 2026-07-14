import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'
import { CheckCircle, Loader2, Plus, Play, Database, ChevronDown, ChevronRight } from 'lucide-react'

interface Mapping {
  id: string
  curated_dataset_id: string | null
  dataset_name: string | null
  row_count: number | null
  entity_class: string
  field_mapping: Record<string, string>
  status: string
  confidence: number | null
  created_at: string | null
}

interface CuratedDataset {
  id: string
  name: string
  status: string
  row_count: number | null
  quality_score: number | null
}

function StatusChip({ status }: { status: string }) {
  const cfg: Record<string, string> = {
    applied: 'bg-green-50 border-green-200 text-green-700',
    draft: 'bg-gray-50 border-gray-200 text-gray-500',
    active: 'bg-blue-50 border-blue-200 text-blue-700',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${cfg[status] ?? cfg.draft}`}>
      {status}
    </span>
  )
}

function MappingRow({ mapping, ontologyId, onApplied }: { mapping: Mapping; ontologyId: string; onApplied: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const [applying, setApplying] = useState(false)
  const [applyResult, setApplyResult] = useState<any>(null)
  const [applyError, setApplyError] = useState('')

  const handleApply = async () => {
    setApplying(true)
    setApplyError('')
    setApplyResult(null)
    try {
      const res: any = await apiClientV2.post(
        `/ontologies/${ontologyId}/mappings/${mapping.id}/apply-from-dataset`
      )
      setApplyResult(res)
      onApplied()
    } catch (e: any) {
      setApplyError(e?.detail || e?.message || '执行失败')
    } finally {
      setApplying(false)
    }
  }

  const fieldEntries = Object.entries(mapping.field_mapping || {}).filter(
    ([k]) => !k.startsWith('__')
  )

  return (
    <div className="border rounded-xl bg-white overflow-hidden">
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          onClick={() => setExpanded(e => !e)}
          className="text-gray-400 hover:text-gray-700 flex-shrink-0"
        >
          {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </button>
        <Database size={15} className="text-blue-500 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm truncate">{mapping.entity_class}</span>
            <StatusChip status={mapping.status} />
          </div>
          <p className="text-xs text-gray-400 truncate mt-0.5">
            {mapping.dataset_name ?? mapping.curated_dataset_id?.slice(0, 8)}
            {mapping.row_count != null && ` · ${mapping.row_count.toLocaleString()} 行`}
          </p>
        </div>
        <button
          onClick={handleApply}
          disabled={applying}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-xs hover:bg-gray-800 disabled:opacity-50 flex-shrink-0"
        >
          {applying ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          {applying ? '执行中...' : '应用 Mapping'}
        </button>
      </div>

      {/* Apply result */}
      {applyResult && (
        <div className="mx-4 mb-3 flex items-center gap-2 text-xs text-green-700 bg-green-50 border border-green-200 rounded-lg px-3 py-2">
          <CheckCircle size={13} />
          写入完成：实体 {applyResult.v1_entities_written ?? applyResult.nodes_created} 条
          {applyResult.total_rows > 0 && `（共 ${applyResult.total_rows} 行）`}
        </div>
      )}
      {applyError && (
        <div className="mx-4 mb-3 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          {applyError}
        </div>
      )}

      {/* Field mapping detail */}
      {expanded && fieldEntries.length > 0 && (
        <div className="border-t mx-4 mb-3 pt-3">
          <p className="text-xs font-medium text-gray-500 mb-2">字段映射</p>
          <div className="grid grid-cols-2 gap-1.5">
            {fieldEntries.map(([col, prop]) => (
              <div key={col} className="flex items-center gap-1.5 text-xs bg-gray-50 rounded px-2 py-1">
                <span className="font-mono text-gray-500">{col}</span>
                <span className="text-gray-300">→</span>
                <span className="font-mono text-blue-600">{prop}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function LinkDatasetPanel({ ontologyId, onDone }: { ontologyId: string; onDone: () => void }) {
  const [selectedId, setSelectedId] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [suggestion, setSuggestion] = useState<any>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const { data: datasets = [], isLoading } = useQuery<CuratedDataset[]>({
    queryKey: ['curated-all'],
    queryFn: () => apiClientV2.get('/curated') as any,
  })

  const approvedDatasets = (datasets as CuratedDataset[]).filter(d => d.status === 'approved')

  const handleSuggest = async () => {
    if (!selectedId) return
    setSuggesting(true)
    setError('')
    setSuggestion(null)
    try {
      const ds = approvedDatasets.find(d => d.id === selectedId)
      // Get preview for column info
      const preview: any = await apiClientV2.get(`/curated/${selectedId}/preview?limit=5`)
      const columns = preview.rows?.length > 0 ? Object.keys(preview.rows[0]) : []
      const res: any = await apiClientV2.post(`/ontologies/${ontologyId}/mappings/suggest`, {
        dataset_name: ds?.name ?? '',
        columns,
        sample_rows: preview.rows?.slice(0, 3) ?? [],
        ontology_domain: '',
      })
      setSuggestion(res)
    } catch (e: any) {
      setError(e?.detail || e?.message || '自动建议失败')
    } finally {
      setSuggesting(false)
    }
  }

  const handleSave = async () => {
    if (!suggestion) return
    setSaving(true)
    setError('')
    try {
      const fieldMapping: Record<string, string> = {}
      for (const fm of suggestion.field_mappings ?? []) {
        fieldMapping[fm.column_name] = fm.property_name
      }
      if (suggestion.primary_key_column) {
        fieldMapping['__primary_key__'] = suggestion.primary_key_column
      }
      await apiClientV2.post(`/ontologies/${ontologyId}/mappings`, {
        curated_dataset_id: selectedId,
        entity_class: suggestion.entity_class,
        field_mapping: fieldMapping,
        confidence: 0.9,
      })
      onDone()
    } catch (e: any) {
      setError(e?.detail || e?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border rounded-xl bg-white p-4 space-y-4">
      <p className="text-sm font-medium">关联 Curated 数据集</p>

      {isLoading ? (
        <p className="text-xs text-gray-400">加载中...</p>
      ) : approvedDatasets.length === 0 ? (
        <p className="text-xs text-gray-400">暂无已审批的 Curated Dataset。请先在 Pipeline → Curated 中完成审批。</p>
      ) : (
        <>
          <select
            value={selectedId}
            onChange={e => { setSelectedId(e.target.value); setSuggestion(null) }}
            className="w-full border rounded-lg px-3 py-2 text-sm"
          >
            <option value="">选择 Curated Dataset...</option>
            {approvedDatasets.map(d => (
              <option key={d.id} value={d.id}>
                {d.name}{d.row_count != null ? ` (${d.row_count.toLocaleString()} 行)` : ''}
              </option>
            ))}
          </select>

          {selectedId && !suggestion && (
            <button
              onClick={handleSuggest}
              disabled={suggesting}
              className="flex items-center gap-1.5 px-4 py-2 border border-black rounded-lg text-sm hover:bg-gray-50 disabled:opacity-50"
            >
              {suggesting ? <Loader2 size={14} className="animate-spin" /> : null}
              {suggesting ? '生成映射中...' : '自动生成 Mapping'}
            </button>
          )}

          {error && <p className="text-xs text-red-500">{error}</p>}

          {suggestion && (
            <div className="space-y-3">
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs space-y-1">
                <p className="font-medium text-blue-700">实体类型：{suggestion.entity_class}
                  {suggestion.entity_class_cn && <span className="ml-1 text-blue-500">({suggestion.entity_class_cn})</span>}
                </p>
                {suggestion.primary_key_column && (
                  <p className="text-blue-600">主键列：<span className="font-mono">{suggestion.primary_key_column}</span></p>
                )}
              </div>
              <div className="space-y-1">
                <p className="text-xs font-medium text-gray-500">字段映射建议</p>
                {(suggestion.field_mappings ?? []).map((fm: any) => (
                  <div key={fm.column_name} className="flex items-center gap-2 text-xs bg-gray-50 rounded px-2 py-1.5">
                    <span className="font-mono text-gray-600 w-32 truncate">{fm.column_name}</span>
                    <span className="text-gray-300">→</span>
                    <span className="font-mono text-blue-600">{fm.property_name}</span>
                    {fm.confidence != null && (
                      <span className="ml-auto text-gray-300">{Math.round(fm.confidence * 100)}%</span>
                    )}
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                >
                  {saving ? <Loader2 size={13} className="animate-spin" /> : <CheckCircle size={13} />}
                  {saving ? '保存中...' : '确认并保存 Mapping'}
                </button>
                <button
                  onClick={() => setSuggestion(null)}
                  className="px-3 py-2 text-sm border rounded-lg hover:bg-gray-50"
                >
                  重新生成
                </button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  )
}

export default function CuratedDatasetsTab({ ontologyId }: { ontologyId: string }) {
  const qc = useQueryClient()
  const [showLink, setShowLink] = useState(false)

  const { data: mappings = [], isLoading } = useQuery<Mapping[]>({
    queryKey: ['mappings', ontologyId],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/mappings`) as any,
  })

  const handleApplied = () => {
    qc.invalidateQueries({ queryKey: ['mappings', ontologyId] })
    qc.invalidateQueries({ queryKey: ['entities', ontologyId] })
    qc.invalidateQueries({ queryKey: ['stats'] })
  }

  const handleLinkDone = () => {
    setShowLink(false)
    qc.invalidateQueries({ queryKey: ['mappings', ontologyId] })
  }

  if (isLoading) return <div className="text-gray-400 text-sm py-8 text-center">加载中...</div>

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">
          关联已审批的 Curated Dataset，配置字段映射后即可构建本体实体。
        </p>
        <button
          onClick={() => setShowLink(v => !v)}
          className="flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-sm hover:bg-gray-50"
        >
          <Plus size={14} />
          关联数据集
        </button>
      </div>

      {showLink && (
        <LinkDatasetPanel ontologyId={ontologyId} onDone={handleLinkDone} />
      )}

      {(mappings as Mapping[]).length === 0 && !showLink ? (
        <div className="border-2 border-dashed rounded-xl py-16 text-center text-gray-400">
          <Database size={32} className="mx-auto mb-3 opacity-30" />
          <p className="text-sm">尚未关联任何 Curated Dataset</p>
          <p className="text-xs mt-1">点击右上角"关联数据集"开始配置 Mapping</p>
        </div>
      ) : (
        <div className="space-y-2">
          {(mappings as Mapping[]).map(m => (
            <MappingRow key={m.id} mapping={m} ontologyId={ontologyId} onApplied={handleApplied} />
          ))}
        </div>
      )}
    </div>
  )
}
