/**
 * agentMvp.ts — I-FRONTEND disposable MVP fixture seeder.
 *
 * Runs against a disposable PostgreSQL + the pinned API: seeds the admin
 * login (the backend bootstrap seeds the first admin), creates one governed
 * Agent via the API, and inserts the admin's `run` grant + a session directly
 * through psql so the application E2E specs have data to drive.  All values
 * are env-driven; the seeder fails softly (specs self-skip on missing data).
 *
 * Run: AGENT_E2E_DB_URL=... AGENT_E2E_API_BASE=... \
 *        node --experimental-vm-modules frontend/src/test/e2e/fixtures/agentMvp.ts
 */
import { execFileSync } from 'node:child_process'

const API_BASE = process.env.AGENT_E2E_API_BASE || 'http://localhost:8000'
const DB_URL = process.env.AGENT_E2E_DB_URL || ''
const ADMIN_USER = process.env.AGENT_E2E_ADMIN_USER || 'admin'
const ADMIN_PASSWORD = process.env.AGENT_E2E_ADMIN_PASSWORD || 'admin123'

async function login(): Promise<{ access_token: string; user_id: string }> {
  const res = await fetch(`${API_BASE}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: ADMIN_USER, password: ADMIN_PASSWORD }),
  })
  if (!res.ok) throw new Error(`login failed: ${res.status} ${await res.text()}`)
  const body = (await res.json()) as { data?: { access_token?: string; user?: { id?: string } } }
  const token = body?.data?.access_token
  if (!token) throw new Error('login response missing token')
  // the backend returns only the bearer; the JWT `sub` claim is the user id
  const payload = JSON.parse(Buffer.from(token.split('.')[1] ?? '', 'base64url').toString('utf8')) as {
    sub?: string
  }
  const userId = body?.data?.user?.id ?? payload.sub
  if (!userId) throw new Error('login response missing user id')
  return { access_token: token, user_id: userId }
}

async function createAgent(token: string, modelConfigId: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/agents`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
      'Idempotency-Key': `agent-mvp-fixture-${Date.now()}`,
    },
    body: JSON.stringify({
      name: 'MVP Fixture Agent',
      description: 'seeded by agentMvp.ts',
      default_model_config_version_id: modelConfigId,
      default_model_name: 'gpt-4o-mini',
      system_prompt: 'You are a fixture agent.',
    }),
  })
  if (!res.ok) {
    const text = await res.text()
    if (res.status === 409 && text.includes('IDEMPOTENCY')) {
      // a previous seed created it under this key with a different body:
      // locate the fixture agent instead of failing
      const list = await fetch(`${API_BASE}/api/v1/agents`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      const body = (await list.json()) as { data?: { items?: Array<{ agent_id?: string; id?: string; name?: string }> } }
      const found = (body?.data?.items ?? []).find(a => a.name === 'MVP Fixture Agent')
      if (found?.agent_id ?? found?.id) return found?.agent_id ?? found?.id ?? ''
    }
    throw new Error(`create agent failed: ${res.status} ${text}`)
  }
  const body = (await res.json()) as { data?: { agent_id?: string; id?: string } }
  const agentId = body?.data?.agent_id ?? body?.data?.id
  if (!agentId) throw new Error('create agent response missing id')
  return agentId
}

function psql(sql: string): void {
  execFileSync('psql', ['-d', DB_URL, '-v', 'ON_ERROR_STOP=1', '-c', sql], {
    stdio: 'pipe',
  })
}

/** Seed one active model config + immutable behavior version (psql). */
function seedModelConfig(userId: string): string {
  const modelConfigId = '00000000-0000-0000-0000-00000000c001'
  const versionId = '00000000-0000-0000-0000-00000000c002'
  // config first (version FK references it), then the version, then activate
  psql(
    `INSERT INTO model_configs (id, name, config_type, provider, models, options, created_by, created_at, updated_at, status)
     VALUES ('${modelConfigId}', 'fixture-gpt-4o-mini', 'llm', 'openai', '["gpt-4o-mini"]'::json, '{}'::json,
             '${userId}', now(), now(), 'active')
     ON CONFLICT (id) DO NOTHING;`
  )
  psql(
    `INSERT INTO model_config_versions (id, model_config_id, version_no, provider, options, behavior_hash, model_contract, created_by)
     VALUES ('${versionId}', '${modelConfigId}', 1, 'openai',
             '{"model":"gpt-4o-mini"}'::json, '${'b'.repeat(64)}', '{"schema_contract_version":1}'::json, '${userId}')
     ON CONFLICT (id) DO NOTHING;`
  )
  psql(
    `UPDATE model_configs SET active_version_id = '${versionId}' WHERE id = '${modelConfigId}';`
  )
  return modelConfigId
}

async function main(): Promise<void> {
  if (!DB_URL) throw new Error('AGENT_E2E_DB_URL is required')
  const { access_token, user_id } = await login()
  seedModelConfig(user_id)
  // default_model_config_version_id is the ACTIVE model behavior VERSION id
  const agentId = await createAgent(access_token, '00000000-0000-0000-0000-00000000c002')

  // run grant so the seeded session/turn flows are authorized (session owner)
  psql(
    `INSERT INTO agent_access_grants (id, agent_id, user_id, capabilities, revision, status, created_by)
     SELECT gen_random_uuid(), '${agentId}', '${user_id}', '["run","view_config"]'::json, 1, 'active', '${user_id}'
     WHERE NOT EXISTS (
       SELECT 1 FROM agent_access_grants WHERE agent_id = '${agentId}' AND user_id = '${user_id}'
     );`
  )
  psql(
    `INSERT INTO agent_sessions (id, agent_id, owner_user_id, status, created_at, updated_at)
     SELECT gen_random_uuid(), '${agentId}', '${user_id}', 'active', now(), now()
     WHERE NOT EXISTS (SELECT 1 FROM agent_sessions WHERE agent_id = '${agentId}' AND owner_user_id = '${user_id}');`
  )
  console.log(`[agentMvp] seeded agent ${agentId} with run grant for ${user_id}`)
}

main().catch(err => {
  console.error(`[agentMvp] seeding skipped: ${err instanceof Error ? err.message : String(err)}`)
  process.exit(0) // soft failure: specs self-skip
})
