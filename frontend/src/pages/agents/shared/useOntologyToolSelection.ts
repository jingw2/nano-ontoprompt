import { useCallback, useEffect, useState } from 'react'
import {
  agentToolsApi, TOOL_CATEGORIES,
  type OntologyBinding, type ToolCategory, type ToolDescriptor,
} from '@/api/agentTools'

export const BASE_CAPABILITIES = ['read_schema', 'read_instances', 'traverse_relations']

export function categoryOf(d: ToolDescriptor): ToolCategory {
  if (d.category) return d.category
  if (d.source_kind === 'builtin') return 'query'
  if (d.source_kind === 'logic') return 'logic'
  if (d.source_kind === 'action') return 'action'
  return 'mcp'
}

/** Categories in effect for a binding: explicit list when stored, otherwise
 * ALL (the legacy default keeps every category enabled). */
export function effectiveCategories(b: OntologyBinding): ToolCategory[] {
  return b.enabled_categories ?? TOOL_CATEGORIES
}

/** Capabilities accumulate across every selected tool's capability. */
export function deriveCapabilities(tools: ToolDescriptor[], selected: Set<string>): string[] {
  const extra = [...selected]
    .map(id => tools.find(d => d.descriptor_id === id)?.capability)
    .filter((c): c is string => Boolean(c))
  return [...new Set([...BASE_CAPABILITIES, ...extra])]
}

export function useOntologyToolSelection(ontologies: { id: string }[]) {
  const [bindings, setBindings] = useState<OntologyBinding[]>([])
  const [toolsByOntology, setToolsByOntology] = useState<Record<string, ToolDescriptor[]>>({})
  const [error, setError] = useState('')

  const loadTools = useCallback((ontologyId: string) => {
    agentToolsApi.listOntologyTools(ontologyId)
      .then(res => {
        const tools = Array.isArray(res.tools) ? res.tools : []
        setToolsByOntology(prev => ({ ...prev, [ontologyId]: tools }))
        setBindings(prev => prev.map(b => {
          if (b.ontology_id !== ontologyId || b.enabled_categories === undefined || b.enabled_categories === null) return b
          const selected = new Set(
            tools.filter(d => effectiveCategories(b).includes(categoryOf(d))).map(d => d.descriptor_id),
          )
          return { ...b, selected_tools: [...selected], capabilities: deriveCapabilities(tools, selected) }
        }))
      })
      .catch(() => setError('AGENTS_TOOLS_LOAD_FAILED'))
  }, [])

  // auto-load the tool list for every bound ontology so category state is visible
  useEffect(() => {
    bindings.forEach(b => {
      if (b.ontology_id && !toolsByOntology[b.ontology_id]) loadTools(b.ontology_id)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bindings.map(b => b.ontology_id).join(',')])

  // an Agent binds at most one Ontology: picking one REPLACES any existing binding
  const bindOntology = useCallback((ontologyId: string) => {
    const ontology = ontologies.find(o => o.id === ontologyId)
    if (!ontology) return
    setBindings([{
      ontology_id: ontology.id,
      capabilities: [...BASE_CAPABILITIES],
      allowlists: {},
      selected_tools: [],
      enabled_categories: [...TOOL_CATEGORIES],
    }])
    loadTools(ontology.id)
  }, [ontologies, loadTools])

  const unbindOntology = useCallback((ontologyId: string) => {
    setBindings(prev => prev.filter(b => b.ontology_id !== ontologyId))
  }, [])

  const toggleCategory = useCallback((ontologyId: string, category: ToolCategory, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const cats = new Set(effectiveCategories(b))
      if (on) cats.add(category)
      else cats.delete(category)
      const tools = toolsByOntology[ontologyId] ?? []
      const selected = new Set(b.selected_tools)
      for (const d of tools) {
        if (categoryOf(d) === category) {
          if (on) selected.add(d.descriptor_id)
          else selected.delete(d.descriptor_id)
        }
      }
      return { ...b, enabled_categories: [...cats],
               selected_tools: [...selected], capabilities: deriveCapabilities(tools, selected) }
    }))
  }, [toolsByOntology])

  const toggleTool = useCallback((ontologyId: string, descriptorId: string, on: boolean) => {
    setBindings(prev => prev.map(b => {
      if (b.ontology_id !== ontologyId) return b
      const selected = new Set(b.selected_tools)
      if (on) selected.add(descriptorId)
      else selected.delete(descriptorId)
      const known = toolsByOntology[ontologyId] ?? []
      return { ...b, selected_tools: [...selected],
               capabilities: deriveCapabilities(known, selected) }
    }))
  }, [toolsByOntology])

  return {
    bindings, setBindings, toolsByOntology, setToolsByOntology, error, setError,
    bindOntology, unbindOntology, toggleCategory, toggleTool,
  }
}
