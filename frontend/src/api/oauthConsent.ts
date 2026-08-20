import { apiClient } from './client'

export interface OAuthClientPublic {
  client_id: string
  client_name: string
}

export interface OAuthConsentParams {
  client_id: string
  redirect_uri: string
  code_challenge: string
  code_challenge_method: string
  scope?: string
  state?: string
}

export interface OAuthConsentResult {
  redirect_uri: string
}

export const oauthConsentApi = {
  getClient: (clientId: string) =>
    apiClient.get<OAuthClientPublic>(`/oauth/clients/${clientId}`),
  decide: (params: OAuthConsentParams, decision: 'allow' | 'deny') =>
    apiClient.post<OAuthConsentResult>('/oauth/consent', { ...params, decision }),
}
