import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import specSource from './business-journeys.spec.ts?raw'
import fixtureSource from './fixtures/businessJourneys.ts?raw'
import playwrightConfigSource from '../../../playwright.config.ts?raw'

const repoRoot = existsSync(resolve(process.cwd(), 'backend'))
  ? process.cwd() : resolve(process.cwd(), '..')
const runtimeCallerSource = readFileSync(resolve(
  repoRoot, 'backend/app/services/model_callers/deepseek_vision.py',
), 'utf8')
const deepseekClientSource = readFileSync(resolve(
  repoRoot, 'backend/evals/business_journeys/deepseek_client.py',
), 'utf8')

describe('business journey browser contract', () => {
  it('uses a bounded wait for the real model answer', () => {
    expect(specSource).toMatch(
      /await expect\(page\.getByTestId\('journey-answer'\)\)\.toContainText\(\s*journey\.semantic_minima\.keywords\[0\]\s*,\s*\{\s*timeout:\s*REAL_MODEL_ANSWER_WAIT_TIMEOUT_MS\s*\}\s*,?\s*\)/,
    )
  })

  it('waits beyond the server model timeout with a small buffer', () => {
    const serverTimeoutMatch = runtimeCallerSource.match(
      /timeout_seconds:\s*float\s*=\s*(\d+(?:\.\d+)?)/,
    )
    const maxAttemptsMatch = deepseekClientSource.match(
      /_MAX_HTTP_ATTEMPTS\s*=\s*(\d[\d_]*)/,
    )
    const serverConstantMatch = specSource.match(
      /const REAL_MODEL_REQUEST_TIMEOUT_MS\s*=\s*(\d[\d_]*)/,
    )
    const preflightCountMatch = specSource.match(
      /const REAL_MODEL_PREFLIGHT_REQUEST_COUNT\s*=\s*(\d[\d_]*)/,
    )
    const completionCountMatch = specSource.match(
      /const REAL_MODEL_COMPLETION_COUNT\s*=\s*(\d[\d_]*)/,
    )
    const maxAttemptsConstantMatch = specSource.match(
      /const REAL_MODEL_MAX_HTTP_ATTEMPTS\s*=\s*(\d[\d_]*)/,
    )
    const bufferMatch = specSource.match(
      /const REAL_MODEL_ANSWER_WAIT_BUFFER_MS\s*=\s*(\d[\d_]*)/,
    )
    const browserWaitExpression = specSource.match(
      /const REAL_MODEL_ANSWER_WAIT_TIMEOUT_MS\s*=\s*REAL_MODEL_REQUEST_TIMEOUT_MS\s*\*\s*REAL_MODEL_PREFLIGHT_REQUEST_COUNT\s*\+\s*REAL_MODEL_REQUEST_TIMEOUT_MS\s*\*\s*REAL_MODEL_COMPLETION_COUNT\s*\*\s*REAL_MODEL_MAX_HTTP_ATTEMPTS\s*\+\s*REAL_MODEL_ANSWER_WAIT_BUFFER_MS/,
    )
    const testTimeoutMatch = playwrightConfigSource.match(
      /^\s*timeout:\s*(\d[\d_]*)/m,
    )
    expect(serverTimeoutMatch).not.toBeNull()
    expect(maxAttemptsMatch).not.toBeNull()
    expect(serverConstantMatch).not.toBeNull()
    expect(preflightCountMatch).not.toBeNull()
    expect(completionCountMatch).not.toBeNull()
    expect(maxAttemptsConstantMatch).not.toBeNull()
    expect(bufferMatch).not.toBeNull()
    expect(browserWaitExpression).not.toBeNull()
    expect(testTimeoutMatch).not.toBeNull()
    const serverTimeoutMs = Number(serverTimeoutMatch?.[1]) * 1000
    const configuredServerTimeoutMs = Number(serverConstantMatch?.[1].replaceAll('_', ''))
    const preflightRequestCount = Number(preflightCountMatch?.[1].replaceAll('_', ''))
    const completionCount = Number(completionCountMatch?.[1].replaceAll('_', ''))
    const maxHttpAttempts = Number(maxAttemptsMatch?.[1].replaceAll('_', ''))
    const configuredMaxHttpAttempts = Number(maxAttemptsConstantMatch?.[1].replaceAll('_', ''))
    const browserWaitBufferMs = Number(bufferMatch?.[1].replaceAll('_', ''))
    const browserWaitMs = configuredServerTimeoutMs * preflightRequestCount
      + configuredServerTimeoutMs * completionCount * configuredMaxHttpAttempts
      + browserWaitBufferMs
    const testTimeoutMs = Number(testTimeoutMatch?.[1].replaceAll('_', ''))
    expect(configuredServerTimeoutMs).toBe(serverTimeoutMs)
    expect(preflightRequestCount).toBe(1)
    expect(completionCount).toBe(2)
    expect(configuredMaxHttpAttempts).toBe(maxHttpAttempts)
    expect(maxHttpAttempts).toBe(2)
    expect(browserWaitMs).toBeGreaterThanOrEqual(
      serverTimeoutMs * preflightRequestCount
      + serverTimeoutMs * completionCount * maxHttpAttempts
      + 30_000,
    )
    expect(testTimeoutMs).toBeGreaterThanOrEqual(browserWaitMs + 60_000)
  })

  it('leaves enough total test time for setup and the bounded real-model wait', () => {
    expect(playwrightConfigSource).toMatch(/timeout:\s*990_000/)
  })

  it('records sanitized answer quality, ordered process, and elapsed time', () => {
    expect(specSource).toMatch(/const turnStartedAt = Date\.now\(\)/)
    expect(specSource).toMatch(/answerKeywordCount/)
    expect(specSource).toMatch(/journey-process-order/)
    expect(specSource).toMatch(/turnElapsedMs/)
  })

  it('renders the governed query descriptor rather than the first catalog descriptor', () => {
    expect(specSource).toMatch(
      /const governedQueryDescriptorId = `query:\$\{journey\.ontology_id\}`/,
    )
    expect(specSource).toMatch(
      /expect\(journey\.mcp_descriptor_ids\)\.toContain\(governedQueryDescriptorId\)/,
    )
    expect(specSource).toMatch(
      /getByTestId\('journey-tool-trace'\)\)\.toContainText\(governedQueryDescriptorId\)/,
    )
  })

  it('maps the preparation ontology id into journey data for the query descriptor', () => {
    expect(fixtureSource).toMatch(
      /export interface JourneyData \{[\s\S]*?ontology_id: string/,
    )
    expect(fixtureSource).toMatch(
      /return \{[\s\S]*?ontology_id: preparation\.ontology_id/,
    )
  })
})
