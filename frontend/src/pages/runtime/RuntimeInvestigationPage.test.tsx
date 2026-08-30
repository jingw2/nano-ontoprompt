import '@/i18n'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import RuntimeInvestigationPage from './RuntimeInvestigationPage'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPage() {
  return render(
    <MemoryRouter>
      <RuntimeInvestigationPage />
    </MemoryRouter>,
  )
}

describe('RuntimeInvestigationPage', () => {
  it('shows snapshot evidence and an ALLOW decision', async () => {
    server.use(
      http.post('*/api/v2/runtime/investigate', () =>
        HttpResponse.json({
          decision: 'ALLOW',
          reason_code: 'ALLOW',
          semantic_snapshot_id: 'snap-valid-001',
          ontology_release_id: 'release-001',
          evidence_citations: [{ source_id: 's-1', source_type: 'dataset', locator: 'row-1', content_hash: 'h' }],
          rule_outcome: [{ rule_id: 'r-1', result: 'matched', reason_code: 'ALLOW' }],
          result: [{ id: 'row-1' }],
          correlation_id: 'corr-1',
        })),
    )
    renderPage()
    expect(await screen.findByTestId('semantic-snapshot-id')).toHaveTextContent('snap-valid-001')
    expect(screen.getByTestId('investigation-decision')).toHaveTextContent('ALLOW')
  })

  it('distinguishes an authorized empty result from a denial', async () => {
    server.use(
      http.post('*/api/v2/runtime/investigate', () =>
        HttpResponse.json({
          decision: 'ALLOW',
          reason_code: 'ALLOW',
          semantic_snapshot_id: 'snap-empty-001',
          ontology_release_id: 'release-001',
          evidence_citations: [],
          rule_outcome: [],
          result: [],
          correlation_id: 'corr-2',
        })),
    )
    renderPage()
    expect(await screen.findByTestId('investigation-decision')).toHaveTextContent('ALLOW')
    expect(screen.getByTestId('investigation-result-count')).toHaveTextContent('0')
    expect(screen.queryByTestId('investigation-denied')).toBeNull()
  })

  it('renders a DENY decision with no result rows and no crash', async () => {
    server.use(
      http.post('*/api/v2/runtime/investigate', () =>
        HttpResponse.json(
          {
            decision: 'DENY',
            reason_code: 'POLICY_DENIED',
            semantic_snapshot_id: 'snap-denied-001',
            ontology_release_id: 'release-001',
            evidence_citations: [],
            rule_outcome: [],
            result: null,
            correlation_id: 'corr-3',
          },
          { status: 403 },
        )),
    )
    renderPage()
    expect(await screen.findByTestId('investigation-decision')).toHaveTextContent('DENY')
    expect(screen.getByTestId('investigation-denied')).toBeTruthy()
    expect(screen.queryByTestId('investigation-result-count')).toBeNull()
  })
})
