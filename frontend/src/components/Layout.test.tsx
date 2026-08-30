import '@/i18n'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import Layout from './Layout'
import { useAuthStore } from '@/stores/authStore'
import { useRuntimeDelegationStore } from '@/stores/runtimeDelegationStore'

// Critical-severity regression test: a Runtime delegation token minted by
// one user in a shared browser tab must never survive that user's logout
// (or the next user's login) — see runtimeDelegationStore.ts and
// RuntimeDelegationGate.tsx. Before the fix, nothing ever called
// useRuntimeDelegationStore's `clear()`, so the token from a previous
// session silently attributed a next user's Runtime requests to the
// previous user.

const server = setupServer(
  http.post('*/api/v1/auth/logout', () => HttpResponse.json({ data: null })),
)

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

beforeEach(() => {
  useAuthStore.setState({ user: { id: 'u-1', username: 'alice', email: 'a@example.invalid', role: 'editor' } as never, token: 'session-a' })
  useRuntimeDelegationStore.setState({ token: 'runtime-token-minted-by-alice' })
})

describe('Layout logout', () => {
  it('clears the Runtime delegation store when the signed-in user logs out', async () => {
    render(
      <MemoryRouter>
        <Layout>
          <div>content</div>
        </Layout>
      </MemoryRouter>,
    )
    expect(useRuntimeDelegationStore.getState().token).toBe('runtime-token-minted-by-alice')

    const logoutButton = screen.getByText(/退出|Logout/i)
    await userEvent.click(logoutButton)

    expect(useRuntimeDelegationStore.getState().token).toBeNull()
    expect(useAuthStore.getState().user).toBeNull()
  })
})

describe('authStore.setAuth', () => {
  it('clears a stale Runtime delegation left over from a previous session in the same tab', () => {
    expect(useRuntimeDelegationStore.getState().token).toBe('runtime-token-minted-by-alice')

    useAuthStore.getState().setAuth(
      { id: 'u-2', username: 'bob', email: 'b@example.invalid', role: 'viewer' } as never,
      'session-b',
    )

    expect(useRuntimeDelegationStore.getState().token).toBeNull()
    expect(useAuthStore.getState().user?.username).toBe('bob')
  })
})
