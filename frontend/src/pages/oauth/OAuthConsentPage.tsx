import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Navigate, useSearchParams } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { oauthConsentApi, type OAuthClientPublic, type OAuthConsentParams } from '@/api/oauthConsent'

export default function OAuthConsentPage() {
  const { t } = useTranslation()
  const token = useAuthStore(s => s.token)
  const [searchParams] = useSearchParams()
  const [client, setClient] = useState<OAuthClientPublic | null>(null)
  const [error, setError] = useState('')
  const [deciding, setDeciding] = useState(false)

  const clientId = searchParams.get('client_id') ?? ''
  const params: OAuthConsentParams = {
    client_id: clientId,
    redirect_uri: searchParams.get('redirect_uri') ?? '',
    code_challenge: searchParams.get('code_challenge') ?? '',
    code_challenge_method: searchParams.get('code_challenge_method') ?? '',
    scope: searchParams.get('scope') ?? undefined,
    state: searchParams.get('state') ?? undefined,
  }
  const scopes = (params.scope ?? '').split(' ').filter(Boolean)

  useEffect(() => {
    if (!token || !clientId) return
    oauthConsentApi.getClient(clientId)
      .then(setClient)
      .catch(() => setError(t('oauth.error_invalid_request')))
  }, [token, clientId, t])

  if (!token) {
    const returnTo = encodeURIComponent(`${location.pathname}${location.search}`)
    return <Navigate to={`/login?returnTo=${returnTo}`} replace />
  }

  const decide = async (decision: 'allow' | 'deny') => {
    setDeciding(true)
    try {
      const result = await oauthConsentApi.decide(params, decision)
      window.location.href = result.redirect_uri
    } catch {
      setError(t('oauth.error_invalid_request'))
      setDeciding(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50" data-testid="oauth-consent-page">
      <div className="w-full max-w-sm bg-white rounded-lg shadow p-8">
        <h1 className="text-xl font-semibold mb-4">{t('oauth.consent_title')}</h1>
        {error && <p className="text-red-500 text-sm mb-4" data-testid="oauth-consent-error">{error}</p>}
        {!error && !client && <p className="text-sm text-gray-500">{t('oauth.loading')}</p>}
        {!error && client && (
          <>
            <p className="text-sm mb-4">
              <span className="font-medium" data-testid="oauth-client-name">{client.client_name}</span>{' '}
              {t('oauth.consent_wants')}
            </p>
            {scopes.length > 0 && (
              <ul className="text-sm text-gray-600 mb-6 list-disc list-inside" data-testid="oauth-scope-list">
                {scopes.map(scope => <li key={scope}>{scope}</li>)}
              </ul>
            )}
            <div className="flex gap-3">
              <button type="button" disabled={deciding} onClick={() => decide('deny')}
                data-testid="oauth-deny" className="flex-1 border rounded-lg py-2 text-sm font-medium disabled:opacity-50">
                {t('oauth.deny')}
              </button>
              <button type="button" disabled={deciding} onClick={() => decide('allow')}
                data-testid="oauth-allow" className="flex-1 bg-black text-white rounded-lg py-2 text-sm font-medium disabled:opacity-50">
                {t('oauth.allow')}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
