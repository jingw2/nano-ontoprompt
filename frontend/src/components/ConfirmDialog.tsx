import { useTranslation } from 'react-i18next'

interface Props {
  open: boolean
  title: string
  message: string
  onConfirm: () => void
  onCancel: () => void
  confirmLabel?: string
  error?: string
}

export default function ConfirmDialog({ open, title, message, onConfirm, onCancel, confirmLabel, error }: Props) {
  const { t } = useTranslation()
  if (!open) return null
  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-lg p-6 w-96">
        <h3 className="font-semibold text-lg mb-2">{title}</h3>
        <p className="text-gray-600 text-sm mb-6">{message}</p>
        {error && <p className="text-red-600 text-sm mb-4">{error}</p>}
        <div className="flex justify-end gap-3">
          <button onClick={onCancel} className="px-4 py-2 border rounded-lg text-sm hover:bg-gray-50">{t('common.cancel')}</button>
          <button onClick={onConfirm} className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm hover:bg-red-700">{confirmLabel ?? t('common.confirm_delete')}</button>
        </div>
      </div>
    </div>
  )
}
