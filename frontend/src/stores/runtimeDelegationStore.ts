import { create } from 'zustand'

interface RuntimeDelegationState {
  token: string | null
  setToken: (token: string) => void
  clear: () => void
}

// Deliberately memory-only: a Runtime delegation is short-lived and must not
// be stored with the ordinary browser session or survive a page reload.
export const useRuntimeDelegationStore = create<RuntimeDelegationState>()((set) => ({
  token: null,
  setToken: (token) => set({ token }),
  clear: () => set({ token: null }),
}))
