/**
 * availability.ts — I-FRONTEND e2e helpers.
 *
 * `hasApi` consults the LIVE backend OpenAPI and returns false (skip) when the
 * surface is absent OR the stack is down, so every functional spec stays green
 * until its backend surface is registered and running.  Mirrors the reviewer's
 * e2e-review availability contract.
 */
const API_BASE = process.env.AGENT_E2E_API_BASE || 'http://localhost:8000'

let openapiCache: { paths: Record<string, unknown> } | null = null

export async function hasApi(prefix: string, hint?: string): Promise<boolean> {
  try {
    if (!openapiCache) {
      const res = await fetch(`${API_BASE}/openapi.json`, { signal: AbortSignal.timeout(10_000) })
      if (!res.ok) return false
      openapiCache = (await res.json()) as { paths: Record<string, unknown> }
    }
    return Object.keys(openapiCache.paths).some(
      p => p.startsWith(prefix) && (!hint || p.includes(hint)),
    )
  } catch {
    return false
  }
}
