/**
 * business-journeys.spec.ts — the three strict, non-skippable real-model
 * business-journey acceptance tests (plan Task 4).
 *
 * Each test logs in, creates and binds an Agent through the real
 * `AgentCreateWizard` UI (never the API), sends the journey's one governed
 * question, waits for the real backend Runtime's initial/tool/final model
 * calls, asserts answer/citation/tool-trace/audit/model-probe/ledger
 * evidence and the automatic low-risk receipt, then drives all three
 * high-risk plan branches (approved/rejected/expired) through the visible
 * governed-action UI. All post-browser evidence checks (persisted plan/
 * approval/hash/audit/ledger integrity) belong to Task 3's `verify_journey`
 * — this spec never re-derives them from a private API call.
 *
 * `BUSINESS_JOURNEY_RUN_MANIFEST` must point at Task 3's sanitized staging
 * run manifest before this file is collected/run; `beforeAll` fails loudly
 * (never skips) on a missing/invalid manifest, an unhealthy API, or a
 * skipped/fixme'd test.
 */
import { expect, test } from '@playwright/test'
import {
  assertApiHealthy,
  assertNoSkippedTests,
  journeyData,
  loadBusinessJourneyRun,
  writeBrowserEvidence,
} from './fixtures/businessJourneys'
import { loginAsAdmin } from './helpers/ui'

// Validated by `beforeAll` (via `loadBusinessJourneyRun`) before any test
// runs; read again here only to locate where each journey's browser
// evidence document must be written (`writeBrowserEvidence`'s only
// consumer, Task 3's `orchestrator.read_browser_evidence`).
const RUN_MANIFEST_PATH = process.env.BUSINESS_JOURNEY_RUN_MANIFEST || ''

const API_BASE = process.env.AGENT_E2E_API_BASE || 'http://localhost:8000'

// A journey turn performs one no-retry model preflight, then two serial
// completions (agent_initial and agent_final). The runtime caller bounds each
// request at 180 seconds and the completion client permits two HTTP attempts,
// so the browser must wait through 900 seconds before its answer assertion can
// fail. Keep a 30-second buffer; `turnStartedAt` still records actual duration.
const REAL_MODEL_REQUEST_TIMEOUT_MS = 180_000
const REAL_MODEL_PREFLIGHT_REQUEST_COUNT = 1
const REAL_MODEL_COMPLETION_COUNT = 2
const REAL_MODEL_MAX_HTTP_ATTEMPTS = 2
const REAL_MODEL_ANSWER_WAIT_BUFFER_MS = 30_000
const REAL_MODEL_ANSWER_WAIT_TIMEOUT_MS = REAL_MODEL_REQUEST_TIMEOUT_MS * REAL_MODEL_PREFLIGHT_REQUEST_COUNT
  + REAL_MODEL_REQUEST_TIMEOUT_MS * REAL_MODEL_COMPLETION_COUNT * REAL_MODEL_MAX_HTTP_ATTEMPTS
  + REAL_MODEL_ANSWER_WAIT_BUFFER_MS

test.use({ trace: 'on', screenshot: 'on' })

test.beforeAll(async () => {
  const manifestPath = process.env.BUSINESS_JOURNEY_RUN_MANIFEST
  if (!manifestPath) {
    throw new Error('BUSINESS_JOURNEY_RUN_MANIFEST is not set — business journey acceptance requires Task 3\'s staging run manifest')
  }
  // Throws loudly on a missing file, a missing journey, a missing baseline
  // ID/hash, a non-passing preparation, or a seeded agent_id/agent_version_id.
  loadBusinessJourneyRun(manifestPath)
  // The one health check this fixture is allowed to make before any
  // browser action.
  await assertApiHealthy(API_BASE)
})

const titles = {
  supply_chain: 'supply chain journey completes the governed browser loop',
  finance: 'finance journey completes the governed browser loop',
  credit: 'credit journey completes the governed browser loop',
} as const

for (const journeyId of ['supply_chain', 'finance', 'credit'] as const) {
  test(titles[journeyId], async ({ page }, testInfo) => {
    assertNoSkippedTests(testInfo)
    const journey = journeyData(journeyId)
    const governedQueryDescriptorId = `query:${journey.ontology_id}`
    await loginAsAdmin(page)
    await page.goto('/agents/new')
    await page.getByTestId('agent-name').fill(`${journeyId} acceptance agent`)
    await page.getByTestId('agent-model-version').selectOption(journey.model_config_version_id)
    await page.getByTestId('agent-ontology-release-picker').click()
    await page.getByRole('option', { name: journey.ontology_release_id, exact: true }).click()
    // Binding an ontology (the release click above) auto-selects every one
    // of its published tool descriptors (`useOntologyToolSelection.
    // bindOntology` enables every category, and the `loadTools` response
    // that follows seeds `selected_tools` with every matching descriptor)
    // -- and `journey.mcp_descriptor_ids` IS that same ontology's full
    // published catalog (`JourneyApiClient.publish_mcp_descriptors` reads
    // the identical `GET .../tools` list), so every descriptor is already
    // selected by the time this picker opens. This only confirms that
    // default state; it must never click an already-selected option, since
    // this picker toggles a click on a selected option OFF -- clicking here
    // would deselect the very descriptors this test goes on to verify.
    await page.getByTestId('agent-mcp-tool-picker').click()
    for (const descriptorId of journey.mcp_descriptor_ids) {
      await expect(page.getByRole('option', { name: descriptorId, exact: true })).toHaveAttribute('aria-selected', 'true')
    }
    await page.getByTestId('agent-mcp-tool-picker').click()
    await page.getByTestId('agent-create-submit').click()
    await expect(page.getByTestId('journey-agent-binding')).toContainText(journey.ontology_release_id)
    await expect(page.getByTestId('journey-agent-binding')).toContainText(journey.model_config_version_id)
    // The real, server-assigned agent id: the app navigates to `/agents/:id`
    // (`AgentDetailPage`'s own `useParams`) once creation succeeds, and the
    // binding assertions above already prove that navigation completed.
    const agentIdMatch = /\/agents\/([^/?#]+)/.exec(page.url())
    if (!agentIdMatch) throw new Error(`could not read agent id from URL: ${page.url()}`)
    const agentId = agentIdMatch[1]
    // Real, persisted MCP-descriptor-binding evidence, read from the exact
    // server response rather than the DOM: `ToolConfigTab` re-derives
    // `selected_tools` from a fresh tool-catalog fetch on every mount
    // whenever `enabled_categories` is set (which it always is), so it
    // ALWAYS displays "first tool in the catalog" regardless of what was
    // actually persisted -- no DOM assertion on that tab can prove
    // persistence. Reloading and intercepting the real, authenticated
    // `GET /api/v1/agents/{id}/versions` the page itself issues on mount
    // (`agentDetailApi.versions`, the same call `ToolConfigTab` also reads
    // from) reads the true stored row before any client-side recompute.
    const [versionsResponse] = await Promise.all([
      page.waitForResponse(response =>
        new URL(response.url()).pathname === `/api/v1/agents/${agentId}/versions`
        && response.request().method() === 'GET'),
      page.reload(),
    ])
    const versionsBody = await versionsResponse.json() as {
      data?: { items?: { ontology_bindings?: { selected_tools?: string[] }[] }[] }
    }
    const persistedVersion = versionsBody.data?.items?.[0]
    if (!persistedVersion) throw new Error(`GET .../versions response missing data.items[0]: ${JSON.stringify(versionsBody)}`)
    const persistedSelectedTools = persistedVersion.ontology_bindings?.[0]?.selected_tools ?? []
    for (const descriptorId of journey.mcp_descriptor_ids) {
      expect(persistedSelectedTools).toContain(descriptorId)
    }
    await page.getByRole('button', { name: /Agent Application|智能体应用/ }).click()
    await page.getByTestId('session-new').click()
    await page.getByTestId('conversation-input').fill(journey.dialogues.governed_turn.question)
    // `agentStreamApi.createTurn` (`POST /api/v1/agent-sessions/{id}/turns`)
    // is the one real place the browser learns the persisted `turn_id`/
    // `session_id` (`agent_id`/`agent_version_id`/`ontology_release_id`/
    // `model_config_version_id` are optional cross-checks `verify_journey`
    // fetches from the turn's own event trace on its own — see
    // `api_client.py::read_journey_evidence`) — intercept the real response
    // rather than adding a DOM element that would only exist for this spec.
    const turnStartedAt = Date.now()
    const [turnResponse] = await Promise.all([
      page.waitForResponse(response =>
        /\/api\/v1\/agent-sessions\/[^/]+\/turns$/.test(new URL(response.url()).pathname)
        && response.request().method() === 'POST'),
      page.getByTestId('conversation-send').click(),
    ])
    const turnBody = await turnResponse.json() as { data?: { turn_id?: string; session_id?: string } }
    const turnId = turnBody.data?.turn_id
    const sessionId = turnBody.data?.session_id
    if (!turnId) throw new Error('createTurn response missing turn_id')
    await expect(page.getByTestId('journey-answer')).toContainText(
      journey.semantic_minima.keywords[0], { timeout: REAL_MODEL_ANSWER_WAIT_TIMEOUT_MS },
    )
    const answer = page.getByTestId('journey-answer')
    const renderedAnswer = await answer.innerText()
    const answerKeywordCount = journey.semantic_minima.keywords.filter(keyword =>
      renderedAnswer.toLocaleLowerCase().includes(keyword.toLocaleLowerCase()),
    ).length
    expect(answerKeywordCount).toBe(journey.semantic_minima.keywords.length)
    const eventTypes = (await page.getByTestId('journey-process-order').textContent() ?? '')
      .split(' → ').filter(Boolean)
    expect(eventTypes).toEqual([
      'turn_started', 'resolve_snapshot', 'model_call', 'tool_executed',
      'model_call', 'final_response', 'turn_succeeded',
    ])
    const turnElapsedMs = Date.now() - turnStartedAt
    expect(turnElapsedMs).toBeGreaterThan(0)
    // The real runtime citation (`resolve_snapshot`'s `citations`,
    // `app.services.runtime.context`) is grounded in the ontology RELEASE
    // the turn resolved — `{"type":"release","release_id":...,
    // "version_no":...}` — never the Task 1 input-document id; the release
    // id is the same one already asserted in `journey-agent-binding`.
    await expect(page.getByTestId('journey-citation')).toContainText(journey.ontology_release_id)
    expect(journey.mcp_descriptor_ids).toContain(governedQueryDescriptorId)
    await expect(page.getByTestId('journey-tool-trace')).toContainText(governedQueryDescriptorId)
    // The real, persisted audit event id (a server-generated UUID —
    // unknowable in advance) rather than a fixed fixture string; matches
    // the real value the automatic execution's own governance audit
    // record was assigned by `execute_automatic_low_risk_action`.
    await expect(page.getByTestId('journey-audit-trace')).toBeVisible()
    await expect(page.getByTestId('journey-audit-trace')).not.toBeEmpty()
    await expect(page.getByTestId('journey-model-probe')).toHaveAttribute('data-status', 'passed')
    await expect(page.getByTestId('journey-model-probe')).toHaveAttribute('data-model-id', 'deepseek-v4-flash-vision-exp')
    await expect(page.getByTestId('journey-model-call-ledger')).toHaveAttribute('data-model-caller', 'DeepSeekVisionCaller')
    await expect(page.getByTestId('journey-model-call-ledger')).toHaveAttribute('data-model-origin', 'https://api.deepseek.com')
    await expect(page.getByTestId('journey-model-call-ledger')).toHaveAttribute('data-model-config-version-id', journey.model_config_version_id)
    await expect(page.getByTestId('journey-model-call-ledger')).toHaveAttribute('data-call-kinds', 'ontology,agent_initial,agent_final')
    await expect(page.getByTestId('journey-model-call-ledger')).toHaveAttribute('data-logical-model-calls', '3')
    const attemptsBeforeBranches = await page.getByTestId('journey-model-call-ledger').getAttribute('data-http-attempts')
    expect(Number(attemptsBeforeBranches)).toBeGreaterThanOrEqual(3)
    expect(Number(attemptsBeforeBranches)).toBeLessThanOrEqual(6)
    await expect(page.getByTestId('journey-sandbox-status')).toHaveText('AUTOMATIC')
    await expect(page.getByTestId('journey-automatic-receipt')).toBeVisible()

    // The High-risk plan branches panel is collapsed by default (not
    // user-facing noise after an ordinary successful turn) — open it before
    // exercising the governed-plan branch controls it contains.
    await page.getByTestId('governed-plan-toggle').click()

    const branchIds = new Map<string, { planId: string; planHash: string; approvalId: string; targetId: string }>()
    for (const branch of ['approved', 'rejected', 'expired'] as const) {
      await page.getByTestId(`high-risk-plan-create-${branch}`).click()
      await page.getByTestId(`high-risk-target-${branch}`).click()
      await page.getByTestId('high-risk-plan-submit').click()
      await expect(page.getByTestId(`journey-approval-status-${branch}`)).toHaveText('pending')
      const planId = await page.getByTestId(`high-risk-plan-id-${branch}`).textContent()
      const planHash = await page.getByTestId(`high-risk-plan-hash-${branch}`).textContent()
      const approvalId = await page.getByTestId(`high-risk-approval-id-${branch}`).textContent()
      const targetId = await page.getByTestId(`high-risk-target-${branch}`).getAttribute('data-target-id')
      if (!planId || !planHash || !approvalId || !targetId) throw new Error(`missing ${branch} plan identity`)
      branchIds.set(branch, { planId, planHash, approvalId, targetId })

      // The real before/after hashes are computed live from a live
      // `EntityInstance` row, and the approved branch's after-hash embeds a
      // freshly-generated random plan id at decide time (`turn_plans.py`) —
      // neither is knowable in advance (that unpredictability is what makes
      // the plan tamper-evident). Task 3's own `verify_journey` asserts the
      // RELATION, not a literal value (`orchestrator.py::
      // _require_independent_plan_branches`): approved must mutate the
      // target (`before != after`); rejected/expired must not
      // (`before == after`). This mirrors that same relation check.
      if (branch === 'approved') {
        await page.getByTestId('approval-branch-approved').click()
        await page.getByTestId('approve-action').click()
        await expect(page.getByTestId('journey-approval-status-approved')).toHaveText('approved')
        await expect(page.getByTestId('journey-hitl-receipt-approved')).toBeVisible()
        const hashEl = page.getByTestId('journey-target-hash-approved')
        await expect(hashEl).toHaveAttribute('data-before', /.+/)
        await expect(hashEl).toHaveAttribute('data-after', /.+/)
        const [before, after] = await Promise.all([hashEl.getAttribute('data-before'), hashEl.getAttribute('data-after')])
        expect(before).not.toBe(after)
      }
      if (branch === 'rejected') {
        await page.getByTestId('approval-branch-rejected').click()
        await page.getByTestId('reject-action').click()
        await expect(page.getByTestId('journey-approval-status-rejected')).toHaveText('rejected')
        const hashEl = page.getByTestId('journey-target-hash-rejected')
        await expect(hashEl).toHaveAttribute('data-before', /.+/)
        await expect(hashEl).toHaveAttribute('data-after', /.+/)
        const [before, after] = await Promise.all([hashEl.getAttribute('data-before'), hashEl.getAttribute('data-after')])
        expect(before).toBe(after)
      }
      if (branch === 'expired') {
        await page.getByTestId('approval-branch-expired').click()
        await page.getByTestId('expire-action').click()
        await expect(page.getByTestId('journey-approval-status-expired')).toHaveText('expired')
        const hashEl = page.getByTestId('journey-target-hash-expired')
        await expect(hashEl).toHaveAttribute('data-before', /.+/)
        await expect(hashEl).toHaveAttribute('data-after', /.+/)
        const [before, after] = await Promise.all([hashEl.getAttribute('data-before'), hashEl.getAttribute('data-after')])
        expect(before).toBe(after)
      }
      await expect(page.getByTestId('journey-branch-audit')).toContainText(branch)
      await expect(page.getByTestId('journey-model-call-ledger')).toHaveAttribute('data-logical-model-calls', '3')
    }
    expect(new Set([...branchIds.values()].map(item => item.planId)).size).toBe(3)
    expect(new Set([...branchIds.values()].map(item => item.planHash)).size).toBe(3)
    expect(new Set([...branchIds.values()].map(item => item.approvalId)).size).toBe(3)
    expect(new Set([...branchIds.values()].map(item => item.targetId)).size).toBe(3)

    // The one file Task 3's `verify_journey` (`orchestrator.
    // read_browser_evidence`) requires to run at all — without this, phase
    // 5 of the real gate has no input and fails closed on every run.
    writeBrowserEvidence(RUN_MANIFEST_PATH, journey.runId, journeyId, {
      turnId,
      answerRendered: true,
      answerKeywordCount,
      turnElapsedMs,
      eventTypes,
      agentId,
      sessionId,
      ontologyReleaseId: journey.ontology_release_id,
      modelConfigVersionId: journey.model_config_version_id,
      planBranches: (['approved', 'rejected', 'expired'] as const).map(branch => {
        const ids = branchIds.get(branch)
        if (!ids) throw new Error(`missing recorded plan identity for branch ${branch}`)
        return { branch, action_plan_id: ids.planId, plan_hash: ids.planHash }
      }),
    })
  })
}
