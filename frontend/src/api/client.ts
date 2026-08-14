import axios, { type AxiosRequestConfig } from 'axios'
import { useAuthStore } from '@/stores/authStore'

type ApiClient = {
  get: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
  post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>
  delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => Promise<T>
}

type RetriableConfig = AxiosRequestConfig & { _retried?: boolean }

export function readCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]*)`))
  return match ? decodeURIComponent(match[1] ?? '') : null
}

let refreshing: Promise<string | null> | null = null

export function refreshAccessToken(): Promise<string | null> {
  if (refreshing) return refreshing
  refreshing = (async () => {
    try {
      const csrf = readCookie('csrf_token') ?? ''
      const response = await fetch('/api/v1/auth/refresh', {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
        credentials: 'same-origin',
      })
      if (!response.ok) return null
      const body = (await response.json()) as { data?: { access_token?: string } }
      const token = body?.data?.access_token ?? null
      if (token) useAuthStore.getState().setToken(token)
      return token
    } catch {
      return null
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

function createApiClient(baseURL: string): ApiClient {
  const client = axios.create({ baseURL })
  client.interceptors.request.use(config => {
    const token = useAuthStore.getState().token
    if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  })
  client.interceptors.response.use(
    res => (res.data?.data !== undefined ? res.data.data : res.data),
    async err => {
      const config = err.config as RetriableConfig | undefined
      const status = err.response?.status
      if (status === 401 && config && !config._retried) {
        config._retried = true
        const token = await refreshAccessToken()
        if (token) {
          config.headers = { ...config.headers, Authorization: `Bearer ${token}` }
          return client(config)
        }
      }
      if (status === 401 || status === 403) {
        useAuthStore.getState().logout()
      }
      return Promise.reject(err.response?.data ?? err)
    }
  )
  return {
    // The response interceptor unwraps ApiEnvelope data; the runtime value is T.
    get: <T = unknown>(url: string, config?: AxiosRequestConfig) => client.get<T>(url, config) as unknown as Promise<T>,
    post: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
      client.post<T>(url, data, config) as unknown as Promise<T>,
    put: <T = unknown>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
      client.put<T>(url, data, config) as unknown as Promise<T>,
    delete: <T = unknown>(url: string, config?: AxiosRequestConfig) => client.delete<T>(url, config) as unknown as Promise<T>,
  }
}

export const apiClient = createApiClient('/api/v1')
export const apiClientV2 = createApiClient('/api/v2')
