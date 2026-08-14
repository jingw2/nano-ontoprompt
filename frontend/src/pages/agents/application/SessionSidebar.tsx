import { useTranslation } from 'react-i18next'
import type { AgentSession } from '@/api/agentSessions'

interface Props {
  sessions: AgentSession[]
  activeSessionId: string | null
  onSelect: (sessionId: string) => void
  onNew: () => void
}

export default function SessionSidebar({ sessions, activeSessionId, onSelect, onNew }: Props) {
  const { t } = useTranslation()
  return (
    <div className="border-r w-56 shrink-0 flex flex-col" data-testid="session-sidebar">
      <div className="p-3 border-b">
        <button type="button" onClick={onNew}
          className="w-full bg-black text-white rounded-lg px-3 py-1.5 text-sm hover:bg-gray-800">
          {t('agent.app.new_session', '+ New')}
        </button>
      </div>
      <div className="flex-1 overflow-auto p-2 space-y-1">
        {sessions.map(s => (
          <button key={s.id} type="button"
            onClick={() => onSelect(s.id)}
            className={`w-full text-left px-3 py-2 rounded-lg text-sm ${activeSessionId === s.id ? 'bg-black text-white' : 'hover:bg-gray-100'}`}>
            {s.id.slice(0, 8)}
            {s.status === 'closed' && <span className="ml-2 text-xs opacity-60">closed</span>}
          </button>
        ))}
        {sessions.length === 0 && (
          <p className="text-xs text-gray-400 p-3">{t('agent.app.no_sessions', '暂无会话')}</p>
        )}
      </div>
    </div>
  )
}
