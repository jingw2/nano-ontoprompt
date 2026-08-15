import '@/i18n'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const server = setupServer()

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

const CLIENT_PATH = resolve(__dirname, '../../../api/ontologyLifecycle.ts')
const PANEL_PATH = resolve(__dirname, './OntologyPublicationPanel.tsx')

function emptyReleases() {
  return HttpResponse.json({ data: { items: [], next_cursor: null, has_more: false }, message: 'ok' })
}

describe('P1D-UI', () => {
  it('red contract: requires the lifecycle client and publication panel', () => {
    const failures: string[] = []
    if (!existsSync(CLIENT_PATH)) failures.push('missing src/api/ontologyLifecycle.ts client')
    if (!existsSync(PANEL_PATH)) failures.push('missing OntologyPublicationPanel.tsx')
    if (failures.length) throw new Error('RED_P1D_UI: ' + failures.join('; '))
  })

  it('shows a loading state while releases are fetched', async () => {
    server.use(
      http.get('*/api/v1/ontologies/onto-1/releases', () => new Promise(() => {})),
    )
    const { default: Panel } = await import('./OntologyPublicationPanel')
    render(<Panel ontologyId="onto-1" status="created" />)
    expect(await screen.findByText('加载中...')).toBeTruthy()
  })

  it('surfaces the releases load failure with a retry instead of infinite loading', async () => {
    let fail = true
    server.use(
      http.get('*/api/v1/ontologies/onto-1/releases', () => {
        if (fail) return HttpResponse.json({ error: { code: 'ONTOLOGY_NOT_FOUND' } }, { status: 404 })
        return emptyReleases()
      }),
    )
    const { default: Panel } = await import('./OntologyPublicationPanel')
    render(<Panel ontologyId="onto-1" status="created" />)
    // error state (with the RELEASES_LOAD_FAILED message + retry) replaces the spinner
    expect(await screen.findByText('发布记录加载失败')).toBeTruthy()
    expect(screen.queryByText('加载中...')).toBeNull()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    // retry refetches; once releases load, the lifecycle controls render
    fail = false
    await userEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByRole('button', { name: '发布' })).toBeTruthy()
  })

  it('shows published + pending-changes badges from releases and dirty prop', async () => {
    server.use(
      http.get('*/api/v1/ontologies/onto-1/releases', () =>
        HttpResponse.json({
          data: {
            items: [
              { id: 'r1', version_no: 1, version: 'v1', created_by: 'u1', created_at: '2026-08-01T00:00:00Z' },
              { id: 'r2', version_no: 2, version: 'v2', created_by: 'u1', created_at: '2026-08-02T00:00:00Z' },
            ],
            next_cursor: null,
            has_more: false,
          },
          message: 'ok',
        })),
    )
    const { default: Panel } = await import('./OntologyPublicationPanel')
    render(<Panel ontologyId="onto-1" status="created" isDirty />)
    expect(await screen.findByText('已发布 (2)')).toBeTruthy()
    expect(screen.getByText('有未发布修改')).toBeTruthy()
    expect(screen.getByText('v2')).toBeTruthy()
    expect(screen.getByText('v1')).toBeTruthy()
  })

  it('marks a draft ontology as created via the lifecycle API', async () => {
    server.use(
      http.get('*/api/v1/ontologies/onto-1/releases', emptyReleases),
      http.post('*/api/v1/ontologies/onto-1/mark-created', ({ request }) => {
        expect(request.headers.get('Idempotency-Key')).toMatch(/^[\x21-\x7e]{16,128}$/)
        return HttpResponse.json({ data: { ontology_id: 'onto-1', status: 'created' }, message: 'ok' })
      }),
    )
    const { default: Panel } = await import('./OntologyPublicationPanel')
    render(<Panel ontologyId="onto-1" status="draft" />)
    const button = await screen.findByRole('button', { name: '标记为已创建' })
    await userEvent.click(button)
    await waitFor(() => expect(screen.getByText('已创建')).toBeTruthy())
  })

  it('publishes through the dialog and renders the receipt with impact', async () => {
    server.use(
      http.get('*/api/v1/ontologies/onto-1/releases', emptyReleases),
      http.post('*/api/v1/ontologies/onto-1/publish', ({ request }) => {
        expect(request.headers.get('Idempotency-Key')).toMatch(/^[\x21-\x7e]{16,128}$/)
        return HttpResponse.json({
          data: {
            ontology_id: 'onto-1',
            release: { version_no: 1, version: 'v1' },
            schema_hash: 'ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd34ef56ab12cd',
            entities: [{ id: 'e1' }, { id: 'e2' }],
            relations: [{ id: 'r1' }],
          },
          message: 'ok',
        })
      }),
    )
    const { default: Panel } = await import('./OntologyPublicationPanel')
    render(<Panel ontologyId="onto-1" status="created" />)
    await userEvent.click(await screen.findByRole('button', { name: '发布' }))
    await userEvent.type(await screen.findByPlaceholderText('变更说明（可选）'), 'first release')
    await userEvent.click(screen.getByRole('button', { name: '确认发布' }))
    expect(await screen.findByText('发布成功')).toBeTruthy()
    expect(screen.getByTestId('receipt-version').textContent).toBe('v1')
    expect(screen.getByTestId('receipt-impact').textContent).toBe('实体 2 · 关系 1')
  })

  it('renders publish rejection findings from the API error detail', async () => {
    server.use(
      http.get('*/api/v1/ontologies/onto-1/releases', emptyReleases),
      http.post('*/api/v1/ontologies/onto-1/publish', () =>
        HttpResponse.json(
          { data: null, message: 'RELEASE_BLOCKED', detail: [{ code: 'LABEL_COLLISION', path: 'entities/2', message: 'duplicate label' }] },
          { status: 422 },
        )),
    )
    const { default: Panel } = await import('./OntologyPublicationPanel')
    render(<Panel ontologyId="onto-1" status="created" />)
    await userEvent.click(await screen.findByRole('button', { name: '发布' }))
    await userEvent.click(screen.getByRole('button', { name: '确认发布' }))
    expect(await screen.findByText(/LABEL_COLLISION/)).toBeTruthy()
    expect(screen.getByText(/duplicate label/)).toBeTruthy()
  })
})
