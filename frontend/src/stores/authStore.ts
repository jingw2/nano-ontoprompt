import { create } from 'zustand'
import type { User } from '@/types/auth'
import { useRuntimeDelegationStore } from '@/stores/runtimeDelegationStore'

interface AuthState {
  user: User | null
  token: string | null
  setAuth: (user: User, token: string) => void
  setToken: (token: string) => void
  logout: () => void
}

// Memory-only bearer: the access token never touches localStorage, cookies
// readable by JavaScript, or persisted Zustand state.  After a reload the
// client reacquires it through the same-origin rotating refresh cookie.
export const useAuthStore = create<AuthState>()((set) => ({
  user: null,
  token: null,
  // A fresh login must never inherit a stale Runtime delegation minted by
  // whichever session (possibly a different user) was previously active in
  // this tab — see `runtimeDelegationStore`'s memory-only discipline.
  setAuth: (user, token) => { useRuntimeDelegationStore.getState().clear(); set({ user, token }) },
  setToken: (token) => set({ token }),
  logout: () => set({ user: null, token: null }),
}))
