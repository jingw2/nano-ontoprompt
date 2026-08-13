import { create } from 'zustand'
import type { User } from '@/types/auth'

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
  setAuth: (user, token) => set({ user, token }),
  setToken: (token) => set({ token }),
  logout: () => set({ user: null, token: null }),
}))
