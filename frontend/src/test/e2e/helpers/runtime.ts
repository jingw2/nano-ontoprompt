import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

type RefreshSeed = {
  config_version: { source_id: string, version: number }
  cursor: { primary_key: string }
  lag: { source_lag_seconds: number }
}

const apiBase = process.env.AGENT_E2E_API_BASE || 'http://localhost:8000'

/** Runtime governance E2E is deliberately opt-in: its supported REST API
 * requires a delegated credential, and this frontend suite has no endpoint
 * that may mint one. A local/test runner must provision its governed records
 * and credentials through authenticated API flows before setting this flag. */
export function hasLocalRuntimeGovernanceSeed(): boolean {
  const host = new URL(apiBase).hostname
  const isLocal = host === 'localhost' || host === '127.0.0.1' || host === '::1'
  return isLocal && process.env.AGENT_E2E_RUNTIME_GOVERNANCE_READY === '1'
}

export async function readRefreshSeed(): Promise<RefreshSeed> {
  const filename = resolve(process.cwd(), '../test_data/runtime/playwright_seed.json')
  return JSON.parse(await readFile(filename, 'utf8')) as RefreshSeed
}

export function runtimeSeedId(name: 'plan' | 'reconciliation'): string | undefined {
  return name === 'plan'
    ? process.env.AGENT_E2E_RUNTIME_PLAN_ID
    : process.env.AGENT_E2E_RUNTIME_RECONCILIATION_ID
}
