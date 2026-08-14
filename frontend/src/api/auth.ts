import { apiClient, readCookie } from './client'
import type { User } from '@/types/auth'

export const authApi = {
  login: (username: string, password: string) =>
    apiClient.post<{ access_token: string; token_type: string }>('/auth/login', { username, password }),
  register: (username: string, email: string, password: string) =>
    apiClient.post<User>('/auth/register', { username, email, password }),
  profile: () => apiClient.get<User>('/auth/profile'),
  changePassword: (current_password: string, new_password: string) =>
    apiClient.put('/auth/password', { current_password, new_password }),
  // Best-effort server logout: revokes the refresh family and clears the
  // HttpOnly refresh cookie so a later reload cannot restore the session.
  logout: async () => {
    try {
      const csrf = readCookie('csrf_token') ?? ''
      await apiClient.post('/auth/logout', null, { headers: { 'X-CSRF-Token': csrf } })
    } catch {
      // network/4xx failures must not block the client-side state clear
    }
  },
}
