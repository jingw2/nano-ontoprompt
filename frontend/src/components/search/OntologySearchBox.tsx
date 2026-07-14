import { useState } from 'react'
import { Search } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

type SearchMode = 'keyword' | 'semantic'

interface SearchResult {
  id: string
  document: string
  metadata: Record<string, unknown>
  score?: number
}

export default function OntologySearchBox({ ontologyId }: { ontologyId: string }) {
  const [query, setQuery] = useState('')
  const [mode, setMode] = useState<SearchMode>('keyword')
  const [results, setResults] = useState<SearchResult[]>([])
  const [loading, setLoading] = useState(false)
  const [searched, setSearched] = useState(false)

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    try {
      const endpoint = mode === 'semantic'
        ? `/ontologies/${ontologyId}/search/semantic?q=${encodeURIComponent(query)}`
        : `/ontologies/${ontologyId}/search/keyword?q=${encodeURIComponent(query)}`
      const res: any = await apiClientV2.get(endpoint)
      setResults(res.results || [])
      setSearched(true)
    } catch {
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex gap-2">
        <div className="flex border rounded overflow-hidden">
          {(['keyword', 'semantic'] as const).map(m => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`px-3 py-1.5 text-xs font-medium transition-colors ${
                mode === m ? 'bg-black text-white' : 'bg-white text-gray-500 hover:bg-gray-50'
              }`}
            >
              {m === 'keyword' ? '关键词' : '语义'}
            </button>
          ))}
        </div>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          placeholder={mode === 'semantic' ? '语义搜索...' : '关键词搜索...'}
          className="flex-1 border rounded px-3 py-1.5 text-sm"
        />
        <button
          onClick={handleSearch}
          disabled={loading}
          className="px-3 py-1.5 bg-black text-white rounded hover:bg-gray-800 disabled:opacity-50"
        >
          <Search size={14} />
        </button>
      </div>

      {searched && results.length === 0 && (
        <p className="text-sm text-gray-400">未找到相关结果。</p>
      )}

      {results.length > 0 && (
        <div className="border rounded-lg divide-y max-h-48 overflow-auto">
          {results.map(r => {
            const name = String(r.metadata?.name_cn || r.id)
            const entityType = r.metadata?.entity_type ? String(r.metadata.entity_type) : ''
            const score = r.score !== undefined ? (r.score * 100).toFixed(0) : null
            return (
              <div key={r.id} className="p-2 hover:bg-gray-50">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium">{name}</span>
                  {score !== null && <span className="text-xs text-gray-400">{score}%</span>}
                </div>
                {entityType && <span className="text-xs text-blue-500">{entityType}</span>}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
