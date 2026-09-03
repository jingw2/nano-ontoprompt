/**
 * businessJourneys.ts — strict fixture loader for the three real-model
 * business-journey acceptance tests (business-journeys.spec.ts).
 *
 * Two sources of truth, never mixed:
 *  - `BUSINESS_JOURNEY_RUN_MANIFEST` (Task 3's sanitized `run.json`,
 *    written by `backend/evals/business_journeys/orchestrator.py::
 *    write_run_manifest`): the ONLY source for baseline evidence
 *    identifiers/hashes actually produced by the pre-browser API chain —
 *    Pipeline/Curated/release/Snapshot/MCP/grant/model IDs.  It never
 *    contains an `agent_id`/`agent_version_id` (Agent creation is
 *    browser-only) or a raw prompt/row/credential — `loadBusinessJourneyRun`
 *    rejects the file outright if any of those leak in.
 *  - Task 1's checked-in, non-secret, synthetic per-journey fixtures
 *    (`test_data/runtime/<journey>/{semantic_minima,dialogues,governance}.json`):
 *    the source for the dialogue question, acceptable keywords/citation, and
 *    the exact governance before/after hashes the browser must observe once
 *    it decides each of the three high-risk plan branches.  These are
 *    read directly (never through the run manifest, which predates the
 *    browser turn and therefore cannot carry turn-derived evidence).
 *
 * Every failure mode throws — this module never returns `undefined` for
 * missing/invalid data, so a broken environment fails the test loudly
 * instead of silently skipping assertions.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { TestInfo } from '@playwright/test'

export type JourneyId = 'supply_chain' | 'finance' | 'credit'

export const JOURNEY_IDS: readonly JourneyId[] = ['supply_chain', 'finance', 'credit']

const EXPECTED_MODEL_ID = 'deepseek-v4-flash-vision-exp'

// Mirrors `orchestrator.FORBIDDEN_MANIFEST_KEYS` — the run manifest must
// already have rejected these at write time; this is a second, independent
// check on the consuming side so a corrupted/hand-edited manifest can never
// silently seed an Agent/turn identity into the browser.
const FORBIDDEN_MANIFEST_KEYS = new Set([
  'agent_id', 'agent_version_id', 'session_id', 'turn_id', 'prompt', 'prompts',
  'messages', 'input_rows', 'rows', 'api_key', 'authorization', 'token', 'secret',
])

// frontend/src/test/e2e/fixtures -> repository root (5 levels up). Playwright
// loads spec/fixture files as ESM, so `__dirname` is unavailable here.
const CURRENT_DIR = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(CURRENT_DIR, '../../../../../')
const RUNTIME_FIXTURES_DIR = path.join(REPO_ROOT, 'test_data', 'runtime')

export interface AgentBindingOptionsFixture {
  ontology_release_id: string
  mcp_descriptor_ids: string[]
  model_config_version_id: string
}

export interface JourneyPreparationEntry {
  run_id: string
  journey_id: JourneyId
  fixture_version: string
  fixture_manifest_sha256: string
  pipeline_id: string
  pipeline_run_id: string
  dataset_version_id: string
  pipeline_status: string
  curated_dataset_id: string
  curated_review_id: string
  curated_status: string
  ontology_id: string
  ontology_release_id: string
  release_status: string
  semantic_snapshot_id: string
  mcp_descriptor_ids: string[]
  grant_id: string
  grant_status: string
  model_config_id: string
  model_config_version_id: string
  model_caller: string
  model_origin: string
  preflight_model_id: string
  requested_model_id: string
  observed_model_id: string
  call_kinds: string[]
  correlation_id: string
  logical_model_calls: number
  http_attempts: number
  retry_count: number
  agent_binding_options: AgentBindingOptionsFixture
  status: string
}

export interface BusinessJourneyRun {
  schema_version: number
  run_id: string
  model_caller: string
  model_origin: string
  requested_model_id: string
  preparations: JourneyPreparationEntry[]
}

export interface JourneyData {
  journeyId: JourneyId
  runId: string
  model_config_version_id: string
  ontology_release_id: string
  mcp_descriptor_ids: string[]
  dialogues: { governed_turn: { question: string } }
  semantic_minima: { keywords: string[] }
}

export interface BrowserPlanBranchEvidence {
  branch: 'approved' | 'rejected' | 'expired'
  action_plan_id: string
  plan_hash: string
}

export interface BrowserJourneyEvidence {
  turnId: string
  agentId?: string
  sessionId?: string
  ontologyReleaseId?: string
  modelConfigVersionId?: string
  planBranches: BrowserPlanBranchEvidence[]
}

function fail(reason: string): never {
  throw new Error(`business journey run manifest invalid: ${reason}`)
}

function readJsonFile(filePath: string): unknown {
  if (!fs.existsSync(filePath)) fail(`missing file ${filePath}`)
  const raw = fs.readFileSync(filePath, 'utf-8')
  try {
    return JSON.parse(raw)
  } catch (err) {
    fail(`unparsable JSON at ${filePath}: ${(err as Error).message}`)
  }
}

function assertNoForbiddenKeys(node: unknown, pathPrefix = ''): void {
  if (Array.isArray(node)) {
    node.forEach((item, i) => assertNoForbiddenKeys(item, `${pathPrefix}[${i}].`))
    return
  }
  if (node && typeof node === 'object') {
    for (const [key, value] of Object.entries(node as Record<string, unknown>)) {
      if (FORBIDDEN_MANIFEST_KEYS.has(key.trim().toLowerCase())) {
        fail(`forbidden field "${pathPrefix}${key}" present in run manifest`)
      }
      assertNoForbiddenKeys(value, `${pathPrefix}${key}.`)
    }
  }
}

const REQUIRED_PREPARATION_STRING_FIELDS: (keyof JourneyPreparationEntry)[] = [
  'run_id', 'journey_id', 'fixture_version', 'fixture_manifest_sha256',
  'pipeline_id', 'pipeline_run_id', 'dataset_version_id', 'pipeline_status',
  'curated_dataset_id', 'curated_review_id', 'curated_status',
  'ontology_id', 'ontology_release_id', 'release_status', 'semantic_snapshot_id',
  'grant_id', 'grant_status', 'model_config_id', 'model_config_version_id',
  'model_caller', 'model_origin', 'preflight_model_id', 'requested_model_id',
  'observed_model_id', 'correlation_id', 'status',
]

/**
 * Reads and strictly validates the Task 3 staging run manifest.  Throws on
 * any missing/invalid/forbidden field rather than returning `undefined` —
 * a caller can never mistake a broken environment for "no data yet".
 */
export function loadBusinessJourneyRun(manifestPath: string): BusinessJourneyRun {
  if (!manifestPath) fail('no manifest path supplied (set BUSINESS_JOURNEY_RUN_MANIFEST)')
  const document = readJsonFile(manifestPath)
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    fail('manifest root is not a JSON object')
  }
  assertNoForbiddenKeys(document)
  const run = document as Record<string, unknown>

  if (run.model_caller !== 'DeepSeekVisionCaller') fail('manifest model_caller is not "DeepSeekVisionCaller"')
  if (run.model_origin !== 'https://api.deepseek.com') fail('manifest model_origin is not the official DeepSeek origin')
  if (run.requested_model_id !== EXPECTED_MODEL_ID) fail(`manifest requested_model_id is not ${EXPECTED_MODEL_ID}`)

  const preparations = run.preparations
  if (!Array.isArray(preparations) || preparations.length !== 3) {
    fail(`expected exactly three preparations, found ${Array.isArray(preparations) ? preparations.length : 'none'}`)
  }

  const seen = new Set<string>()
  for (const rawEntry of preparations) {
    const prep = rawEntry as Partial<JourneyPreparationEntry> | null
    if (!prep || typeof prep !== 'object') fail('a preparation entry is not an object')
    const journeyId = prep.journey_id as string | undefined
    if (!journeyId || !JOURNEY_IDS.includes(journeyId as JourneyId)) {
      fail(`unknown or missing journey_id: ${String(journeyId)}`)
    }
    if (seen.has(journeyId)) fail(`duplicate preparation for journey ${journeyId}`)
    seen.add(journeyId)

    for (const field of REQUIRED_PREPARATION_STRING_FIELDS) {
      const value = prep[field]
      if (typeof value !== 'string' || value.length === 0) {
        fail(`preparation "${journeyId}" is missing required field "${String(field)}"`)
      }
    }
    if (prep.status !== 'passed') fail(`preparation "${journeyId}" did not pass (status=${String(prep.status)})`)
    if (prep.requested_model_id !== EXPECTED_MODEL_ID
      || prep.observed_model_id !== EXPECTED_MODEL_ID
      || prep.preflight_model_id !== EXPECTED_MODEL_ID) {
      fail(`preparation "${journeyId}" used a model id other than ${EXPECTED_MODEL_ID}`)
    }
    if (!Array.isArray(prep.mcp_descriptor_ids) || prep.mcp_descriptor_ids.length === 0) {
      fail(`preparation "${journeyId}" has no granted mcp_descriptor_ids`)
    }
    if (!prep.agent_binding_options || typeof prep.agent_binding_options !== 'object'
      || !(prep.agent_binding_options as AgentBindingOptionsFixture).ontology_release_id
      || !(prep.agent_binding_options as AgentBindingOptionsFixture).model_config_version_id) {
      fail(`preparation "${journeyId}" is missing agent_binding_options`)
    }
    if (typeof prep.logical_model_calls !== 'number' || typeof prep.http_attempts !== 'number') {
      fail(`preparation "${journeyId}" is missing model-call counters`)
    }
  }
  for (const journeyId of JOURNEY_IDS) {
    if (!seen.has(journeyId)) fail(`missing preparation for journey "${journeyId}"`)
  }

  return run as unknown as BusinessJourneyRun
}

let cachedRun: BusinessJourneyRun | null = null
let cachedRunPath: string | null = null

function currentRun(): BusinessJourneyRun {
  const manifestPath = process.env.BUSINESS_JOURNEY_RUN_MANIFEST
  if (!manifestPath) fail('BUSINESS_JOURNEY_RUN_MANIFEST environment variable is not set')
  if (!cachedRun || cachedRunPath !== manifestPath) {
    cachedRun = loadBusinessJourneyRun(manifestPath)
    cachedRunPath = manifestPath
  }
  return cachedRun
}

function readFixtureJson<T>(journeyId: JourneyId, fileName: string): T {
  const filePath = path.join(RUNTIME_FIXTURES_DIR, journeyId, fileName)
  return readJsonFile(filePath) as T
}

interface SemanticMinimaFixture {
  keywords: string[]
}

interface DialoguesFixture {
  dialogue_scenarios: { question: string }[]
}

interface GovernanceFixture {
  governance_outcomes: { id: string }[]
}

/** Returns one typed, fully-validated journey manifest entry, merging Task
 * 3's live preparation evidence with Task 1's checked-in dialogue/semantic/
 * governance fixtures. Throws — never returns a partial/undefined value.
 *
 * `governance.json`'s before/after target hashes are intentionally NOT
 * exposed here: they are computed live from a live `EntityInstance` row at
 * plan-create/decide time, and the approved branch's after-hash embeds a
 * freshly-generated random plan id — neither is knowable in advance from a
 * static fixture. The spec asserts the RELATION instead (approved mutates
 * the target, rejected/expired don't), the same thing Task 3's own
 * `verify_journey` asserts (`orchestrator.py::
 * _require_independent_plan_branches`) — only the outcome IDS below are
 * validated here, as part of this fixture's "throws on missing/invalid
 * data" contract. */
export function journeyData(journeyId: JourneyId): JourneyData {
  if (!JOURNEY_IDS.includes(journeyId)) fail(`unknown journey id: ${String(journeyId)}`)
  const run = currentRun()
  const preparation = run.preparations.find(p => p.journey_id === journeyId)
  if (!preparation) fail(`no preparation found for journey "${journeyId}"`)

  const semanticMinima = readFixtureJson<SemanticMinimaFixture>(journeyId, 'semantic_minima.json')
  if (!Array.isArray(semanticMinima.keywords) || semanticMinima.keywords.length === 0) {
    fail(`${journeyId} semantic_minima.json has no keywords`)
  }

  const dialogues = readFixtureJson<DialoguesFixture>(journeyId, 'dialogues.json')
  const governedTurn = dialogues.dialogue_scenarios?.[0]
  if (!governedTurn || !governedTurn.question) {
    fail(`${journeyId} dialogues.json has no governed dialogue scenario`)
  }

  const governance = readFixtureJson<GovernanceFixture>(journeyId, 'governance.json')
  const outcomeIds = new Set(governance.governance_outcomes.map(o => o.id))
  for (const id of ['automatic', 'approved', 'rejected', 'expired']) {
    if (!outcomeIds.has(id)) fail(`${journeyId} governance.json is missing outcome "${id}"`)
  }

  return {
    journeyId,
    runId: run.run_id,
    model_config_version_id: preparation.model_config_version_id,
    ontology_release_id: preparation.ontology_release_id,
    mcp_descriptor_ids: preparation.mcp_descriptor_ids,
    dialogues: { governed_turn: { question: governedTurn.question } },
    semantic_minima: { keywords: semanticMinima.keywords },
  }
}

/**
 * Writes the ONE file `verify_journey` (Task 3's `orchestrator.
 * read_browser_evidence`) requires: a small pointer/cross-check document,
 * never the full evidence itself. `read_journey_evidence`
 * (`api_client.py::read_journey_evidence`) treats every field here except
 * `turn_id` (the lookup key) and each plan branch's `action_plan_id`/
 * `plan_hash` (used to fetch and cross-check the real persisted plan) as an
 * OPTIONAL cross-check against the application's own persisted event trace
 * — never as a trusted source of truth on its own — so this writer only
 * ever supplies real, browser-observed identifiers, never derives or
 * fabricates one.
 */
export function writeBrowserEvidence(
  manifestPath: string, runId: string, journeyId: JourneyId, evidence: BrowserJourneyEvidence,
): void {
  // manifestPath = <output_dir>/business_journeys/staging/run.json
  const businessJourneysDir = path.dirname(path.dirname(manifestPath))
  const browserDir = path.join(businessJourneysDir, 'browser')
  fs.mkdirSync(browserDir, { recursive: true })
  const document: Record<string, unknown> = {
    schema_version: 1,
    run_id: runId,
    journey_id: journeyId,
    turn_id: evidence.turnId,
    plan_branches: evidence.planBranches.map(branch => ({
      branch: branch.branch,
      action_plan_id: branch.action_plan_id,
      plan_hash: branch.plan_hash,
    })),
  }
  if (evidence.agentId) document.agent_id = evidence.agentId
  if (evidence.sessionId) document.session_id = evidence.sessionId
  if (evidence.ontologyReleaseId) document.ontology_release_id = evidence.ontologyReleaseId
  if (evidence.modelConfigVersionId) document.model_config_version_id = evidence.modelConfigVersionId
  const targetPath = path.join(browserDir, `${runId}.${journeyId}.json`)
  fs.writeFileSync(targetPath, JSON.stringify(document, null, 2), 'utf-8')
}

/** Fails the test if it was skipped or marked as an intentional bypass
 * (`test.skip`/`test.fixme`/`test.fail`) — a defensive guard so a future
 * edit can never silently turn one of these three required titles into a
 * soft-skipping test again. */
export function assertNoSkippedTests(testInfo: TestInfo): void {
  const bypassTypes = testInfo.annotations.map(a => a.type).filter(type => ['skip', 'fixme', 'fail'].includes(type))
  if (bypassTypes.length > 0) {
    throw new Error(
      `test "${testInfo.title}" carries a bypass annotation (${bypassTypes.join(', ')}); ` +
      'business-journey acceptance tests must never skip, fixme, or soft-fail',
    )
  }
  if (testInfo.status === 'skipped') {
    throw new Error(`test "${testInfo.title}" is skipped; business-journey acceptance tests must never skip`)
  }
}

/** The one read-only, pre-turn health check this fixture is allowed to make
 * (`Browser API use is limited to health and read-only evidence
 * validation`): a plain `GET /health` against the application-under-test.
 * Every Agent-creation/binding/dialogue/tool/Sandbox/plan/approval action
 * remains a real browser interaction — this never seeds or mutates state. */
export async function assertApiHealthy(apiBase: string): Promise<void> {
  let response: Response
  try {
    response = await fetch(`${apiBase.replace(/\/$/, '')}/health`)
  } catch (err) {
    fail(`API health check request failed against ${apiBase}: ${(err as Error).message}`)
  }
  if (!response.ok) fail(`API health check returned HTTP ${response.status}`)
  const body = await response.json() as { status?: string; db?: string }
  if (body.status !== 'ok' || body.db !== 'ok') {
    fail(`API is unhealthy: status=${String(body.status)} db=${String(body.db)}`)
  }
}
