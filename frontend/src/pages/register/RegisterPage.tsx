import { useForm } from 'react-hook-form'
import { useNavigate, Link } from 'react-router-dom'
import { authApi } from '@/api/auth'
import { useTranslation } from 'react-i18next'
import { useState } from 'react'

export default function RegisterPage() {
  const { register, handleSubmit } = useForm<{ username: string; email: string; password: string }>()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const onSubmit = async (data: { username: string; email: string; password: string }) => {
    setLoading(true)
    setError('')
    try {
      await authApi.register(data.username, data.email, data.password)
      navigate('/login')
    } catch (e: unknown) {
      setError((e as { message?: string })?.message || '注册失败，请检查信息')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm bg-white rounded-lg shadow p-8">
        <h1 className="text-2xl font-semibold mb-6">{t('auth.register')}</h1>
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1">{t('auth.username')}</label>
            <input {...register('username', { required: true })}
              className="w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">{t('auth.email')}</label>
            <input {...register('email', { required: true })} type="email"
              className="w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">{t('auth.password')}</label>
            <input {...register('password', { required: true })} type="password"
              className="w-full border rounded-lg px-3 py-2 text-sm" />
          </div>
          {error && <p className="text-red-500 text-sm">{error}</p>}
          <button type="submit" disabled={loading}
            className="w-full bg-black text-white rounded-lg py-2 text-sm disabled:opacity-50">
            {loading ? t('common.loading') : t('auth.register')}
          </button>
        </form>
        <p className="mt-4 text-sm text-center text-gray-500">
          {t('auth.have_account')} <Link to="/login" className="text-black underline">{t('auth.login')}</Link>
        </p>
      </div>
    </div>
  )
}
