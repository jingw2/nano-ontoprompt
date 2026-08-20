import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { describe, it, expect, beforeAll, afterEach, afterAll } from 'vitest'
import OAuthConsentPage from './OAuthConsentPage'
import { useAuthStore } from '@/stores/authStore'

const server = setupServer()
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => {
  server.resetHandlers()
  useAuthStore.getState().logout()
})
afterAll(() => server.close())

const CONSENT_QS =
  '?client_id=c-1&redirect_uri=https%3A%2F%2Fclient.example%2Fcb&code_challenge=abc&code_challenge_method=S256&scope=ontology%3Aread&state=xyz'

function renderConsentPage(initialPath = `/oauth/consent${CONSENT_QS}`) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/login" element={<div data-testid="login-page">login</div>} />
        <Route path="/oauth/consent" element={<OAuthConsentPage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('OAuthConsentPage', () => {
  it('redirects to /login with a returnTo when not authenticated', async () => {
    renderConsentPage()
    await waitFor(() => expect(screen.getByTestId('login-page')).toBeTruthy())
  })

  it('shows the client name and scopes, and Allow navigates to the redirect_uri', async () => {
    useAuthStore.getState().setAuth(
      { id: 'u-1', username: 'u', email: 'u@t.com', role: 'editor', is_active: true, created_at: '2026-01-01T00:00:00Z' },
      'token',
    )
    server.use(
      http.get('*/api/v1/oauth/clients/c-1', () =>
        HttpResponse.json({ data: { client_id: 'c-1', client_name: 'Test MCP Client' }, message: 'ok' })),
      http.post('*/api/v1/oauth/consent', () =>
        HttpResponse.json({ data: { redirect_uri: 'https://client.example/cb?code=xyz&state=xyz' }, message: 'ok' })),
    )
    const originalLocation = window.location
    // @ts-expect-error -- test-only override so the assertion doesn't navigate jsdom away
    delete window.location
    window.location = { ...originalLocation, href: originalLocation.href } as Location

    renderConsentPage()
    expect(await screen.findByTestId('oauth-client-name')).toHaveTextContent('Test MCP Client')
    expect(screen.getByTestId('oauth-scope-list')).toHaveTextContent('ontology:read')
    await userEvent.click(screen.getByTestId('oauth-allow'))
    await waitFor(() => expect(window.location.href).toBe('https://client.example/cb?code=xyz&state=xyz'))

    window.location = originalLocation
  })

  it('Deny navigates to the redirect_uri with an access_denied error', async () => {
    useAuthStore.getState().setAuth(
      { id: 'u-1', username: 'u', email: 'u@t.com', role: 'editor', is_active: true, created_at: '2026-01-01T00:00:00Z' },
      'token',
    )
    server.use(
      http.get('*/api/v1/oauth/clients/c-1', () =>
        HttpResponse.json({ data: { client_id: 'c-1', client_name: 'Test MCP Client' }, message: 'ok' })),
      http.post('*/api/v1/oauth/consent', () =>
        HttpResponse.json({ data: { redirect_uri: 'https://client.example/cb?error=access_denied&state=xyz' }, message: 'ok' })),
    )
    const originalLocation = window.location
    // @ts-expect-error -- test-only override
    delete window.location
    window.location = { ...originalLocation, href: originalLocation.href } as Location

    renderConsentPage()
    await screen.findByTestId('oauth-client-name')
    await userEvent.click(screen.getByTestId('oauth-deny'))
    await waitFor(() => expect(window.location.href).toBe('https://client.example/cb?error=access_denied&state=xyz'))

    window.location = originalLocation
  })
})
