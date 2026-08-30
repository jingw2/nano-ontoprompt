import { useEffect, useState, type ReactNode } from 'react'
import { runtimeDelegationApi, type RuntimeDelegationAgent } from '@/api/runtime'
import { useRuntimeDelegationStore } from '@/stores/runtimeDelegationStore'

export default function RuntimeDelegationGate({ children }: { children: ReactNode }) {
  const token = useRuntimeDelegationStore(state => state.token)
  const setToken = useRuntimeDelegationStore(state => state.setToken)
  const [agents, setAgents] = useState<RuntimeDelegationAgent[]>([])
  const [agentId, setAgentId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    runtimeDelegationApi.listAgents().then(items => {
      setAgents(items)
      setAgentId(items[0]?.id ?? '')
    }).catch(() => setError('Unable to load Runtime agents'))
  }, [])

  if (token) return <>{children}</>
  const agent = agents.find(item => item.id === agentId)
  const scopes = (agent?.allowed_scopes ?? []).filter(scope => scope === 'ontology:read' || scope === 'ontology:write')

  return <div className="p-6" data-testid="runtime-delegation-gate">
    <h2 className="text-base font-medium mb-2">Runtime access</h2>
    <p className="text-sm text-gray-500 mb-3">Choose a registered Runtime agent to create a short-lived session for your account.</p>
    {agents.length > 0 && <>
      <select value={agentId} onChange={event => setAgentId(event.target.value)} data-testid="runtime-delegation-agent"
        className="border rounded px-2 py-1 text-sm mr-2">
        {agents.map(agent => <option key={agent.id} value={agent.id}>{agent.client_name}</option>)}
      </select>
      <button type="button" data-testid="runtime-delegation-start" disabled={!agentId || !scopes.length}
        onClick={() => runtimeDelegationApi.issue(agentId, scopes).then(result => setToken(result.token)).catch(() => setError('Runtime delegation was denied'))}
        className="px-3 py-1.5 text-xs bg-black text-white rounded disabled:opacity-40">Start Runtime session</button>
    </>}
    {!agents.length && !error && <p className="text-sm text-gray-500">No Runtime-capable agents are registered.</p>}
    {error && <p role="alert" className="mt-2 text-sm text-red-600">{error}</p>}
  </div>
}
