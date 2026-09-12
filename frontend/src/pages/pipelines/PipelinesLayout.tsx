import { Outlet } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

export default function PipelinesLayout() {
  const { t } = useTranslation()
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">{t('pipelines.title')}</h1>
      <Outlet />
    </div>
  )
}
