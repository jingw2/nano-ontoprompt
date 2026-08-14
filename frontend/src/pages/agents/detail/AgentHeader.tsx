import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import type { AgentDetail } from '@/api/agentDetail'

interface Props {
  agent: AgentDetail
  dirty: boolean
  saving: boolean
}

export default function AgentHeader({ agent, dirty, saving }: Props) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-3">
        <button type="button" onClick={() => navigate('/agents')}
          className="text-sm text-gray-500 hover:text-black">
          &lt; {t('agent.detail.back', 'Agents')}
        </button>
        <h2 className="text-xl font-semibold">{agent.name ?? agent.agent_id.slice(0, 8)}</h2>
        <span className={`px-2 py-0.5 rounded text-xs ${agent.status === 'archived' ? 'bg-gray-100 text-gray-500' : 'bg-green-50 text-green-700'}`}>
          {agent.status === 'archived' ? t('agent.list.status_archived', '已归档') : t('agent.list.status_active', '活跃')}
        </span>
        <span className="text-sm text-gray-500">v{agent.version_no ?? '—'}</span>
        {dirty && <span className="text-xs text-amber-600">{t('agent.detail.dirty', '有未保存修改')}</span>}
        {saving && <span className="text-xs text-gray-500">{t('agent.detail.saving', '保存中…')}</span>}
      </div>
    </div>
  )
}
