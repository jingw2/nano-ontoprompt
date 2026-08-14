import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import { agentDetailApi, type AgentDetail, type AgentVersion } from '@/api/agentDetail'
import AgentHeader from './AgentHeader'
import AgentInfoTab from './AgentInfoTab'
import SystemPromptTab from './SystemPromptTab'
import ToolConfigTab from './ToolConfigTab'
import MemoryConfigTab from './MemoryConfigTab'
import AgentApplicationTab from '../application/AgentApplicationTab'

type TabKey = 'basic' | 'prompt' | 'tools' | 'memory' | 'application'

const TABS: { key: TabKey; labelKey: string; fallback: string }[] = [
  { key: 'basic', labelKey: 'agent.detail.tab_basic', fallback: 'Basic' },
  { key: 'prompt', labelKey: 'agent.detail.tab_prompt', fallback: 'System Prompt' },
  { key: 'tools', labelKey: 'agent.detail.tab_tools', fallback: 'Tools' },
  { key: 'memory', labelKey: 'agent.detail.tab_memory', fallback: 'Memory' },
  { key: 'application', labelKey: 'agent.detail.tab_application', fallback: 'Agent Application' },
]

export default function AgentDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const { t } = useTranslation()
  const role = useAuthStore(s => s.user?.role)
  const [agent, setAgent] = useState<AgentDetail | null>(null)
  const [versions, setVersions] = useState<AgentVersion[]>([])
  const [activeTab, setActiveTab] = useState<TabKey>('basic')
  const [loadError, setLoadError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [reloadTick, setReloadTick] = useState(0)

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => { if (!cancelled) { setAgent(null); setLoadError('') } })
    Promise.all([agentDetailApi.get(id), agentDetailApi.versions(id)])
      .then(([detail, page]) => {
        if (cancelled) return
        setAgent(detail)
        setVersions(Array.isArray(page.items) ? page.items : [])
        setDirty(false)
      })
      .catch(() => { if (!cancelled) setLoadError('AGENTS_DETAIL_LOAD_FAILED') })
    return () => { cancelled = true }
  }, [id, reloadTick])

  const reload = useCallback(() => setReloadTick(n => n + 1), [])

  const activeVersion = versions.find(v => v.version_no === agent?.version_no) ?? null
  const canEdit = role === 'editor' || role === 'admin'

  const handleSaved = useCallback(() => {
    setDirty(false)
    setReloadTick(n => n + 1)
  }, [])

  const setTab = useCallback((key: TabKey) => {
    if (dirty && !window.confirm(t('agent.detail.confirm_discard', '有未保存的修改，确定离开？'))) return
    setActiveTab(key)
  }, [dirty, t])

  if (loadError) {
    return (
      <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-600" role="alert">
        <p>{t('agent.detail.load_failed', '加载失败')}</p>
        <button type="button" onClick={reload} className="mt-2 px-3 py-1 text-xs border border-red-300 rounded hover:bg-red-100">
          {t('agent.list.retry', 'Retry')}
        </button>
      </div>
    )
  }
  if (!agent) {
    return <div className="p-6 text-gray-400" data-testid="agent-detail-loading">{t('common.loading', '加载中...')}</div>
  }

  return (
    <div>
      <AgentHeader agent={agent} dirty={dirty} saving={false} />
      <div className="flex gap-1 border-b mb-4">
        {TABS.map(tab => (
          <button key={tab.key} type="button" onClick={() => setTab(tab.key)}
            className={`px-4 py-2 text-sm border-b-2 ${activeTab === tab.key ? 'border-black font-medium' : 'border-transparent text-gray-500 hover:text-black'}`}>
            {t(tab.labelKey, tab.fallback)}
          </button>
        ))}
      </div>

      {activeTab === 'basic' && (
        <AgentInfoTab agentId={id} activeVersion={activeVersion} versions={versions} canEdit={canEdit}
          onSaved={handleSaved} onReload={reload} onDirtyChange={setDirty} />
      )}
      {activeTab === 'prompt' && (
        <SystemPromptTab agentId={id} activeVersion={activeVersion} canEdit={canEdit}
          onSaved={handleSaved} onDirtyChange={setDirty} />
      )}
      {activeTab === 'tools' && (
        <ToolConfigTab agentId={id} canEdit={canEdit} onDirtyChange={setDirty} />
      )}
      {activeTab === 'memory' && (
        <MemoryConfigTab agentId={id} activeVersion={activeVersion} canEdit={canEdit}
          onSaved={handleSaved} onDirtyChange={setDirty} />
      )}
      {activeTab === 'application' && (
        <AgentApplicationTab agentId={id} />
      )}
    </div>
  )
}
