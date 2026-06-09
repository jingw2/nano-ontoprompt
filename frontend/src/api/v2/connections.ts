import { apiClientV2 } from '@/api/client'

export interface Connection {
  id: string
  name: string
  kind: string
  status: string
}

export interface ConnectionCreate {
  name: string
  kind: string
  config: Record<string, unknown>
}

const connectionsApi = {
  list: () => apiClientV2.get<Connection[]>('/connections'),
  get: (id: string) => apiClientV2.get<Connection>(`/connections/${id}`),
  create: (body: ConnectionCreate) => apiClientV2.post<Connection>('/connections', body),
  test: (id: string) => apiClientV2.post(`/connections/${id}/test`),
  delete: (id: string) => apiClientV2.delete(`/connections/${id}`),
}

export default connectionsApi
