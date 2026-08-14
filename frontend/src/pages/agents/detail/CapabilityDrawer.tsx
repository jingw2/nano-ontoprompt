import { useTranslation } from 'react-i18next'

interface Props {
  open: boolean
  onClose: () => void
  capabilities: string[]
}

export default function CapabilityDrawer({ open, onClose, capabilities }: Props) {
  const { t } = useTranslation()
  if (!open) return null
  return (
    <div className="fixed inset-0 bg-black/30 z-40" onClick={onClose}>
      <div
        role="dialog"
        aria-label={t('agent.tools.capabilities', '能力')}
        className="absolute right-0 top-0 h-full w-80 bg-white shadow-xl p-4 overflow-auto"
        onClick={e => e.stopPropagation()}
        data-testid="capability-drawer"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-medium text-sm">{t('agent.tools.capabilities', '能力')}</h3>
          <button type="button" onClick={onClose} className="text-gray-400 hover:text-black">✕</button>
        </div>
        <ul className="space-y-2">
          {capabilities.map(cap => (
            <li key={cap} className="text-sm border rounded-lg px-3 py-2">{cap}</li>
          ))}
          {capabilities.length === 0 && (
            <li className="text-sm text-gray-400">{t('agent.tools.no_capabilities', '暂无能力')}</li>
          )}
        </ul>
      </div>
    </div>
  )
}
