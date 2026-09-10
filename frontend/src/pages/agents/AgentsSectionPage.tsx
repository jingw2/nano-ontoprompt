import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/authStore'
import AgentListPage from './list/AgentListPage'
import ApprovalsPage from '@/pages/admin/ApprovalsPage'
import ToolConnectionsPage from '@/pages/admin/ToolConnectionsPage'

type SectionKey = 'management' | 'approvals' | 'tool-connections'

const SECTIONS: { key: SectionKey; labelKey: string; fallback: string; adminOnly?: boolean }[] = [
  { key: 'management', labelKey: 'agent.list.title', fallback: '智能体管理' },
  { key: 'approvals', labelKey: 'nav.approvals', fallback: '审批', adminOnly: true },
  { key: 'tool-connections', labelKey: 'toolConnections.nav_label', fallback: '工具连接', adminOnly: true },
]

/** Approvals and Tool Connections are admin-only operator surfaces that
 * previously lived as standalone sidebar entries; they're consolidated here
 * as tabs under the Agent workspace (their own domain) rather than cluttering
 * the top-level nav. Each tab renders the existing page component unchanged.
 * `section=` picks the tab (kept distinct from ApprovalsPage's own internal
 * `tab=` param so `/agents?section=approvals&tab=mcp` deep-links correctly). */
export default function AgentsSectionPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const role = useAuthStore(s => s.user?.role)
  const isAdmin = role === 'admin'
  const visibleSections = SECTIONS.filter(s => !s.adminOnly || isAdmin)
  const requested = searchParams.get('section') as SectionKey | null
  const initialSection: SectionKey = visibleSections.some(s => s.key === requested) ? requested! : 'management'
  const [activeSection, setActiveSection] = useState<SectionKey>(initialSection)

  return (
    <div data-testid="agents-section-page">
      <div className="flex gap-1 border-b mb-4">
        {visibleSections.map(section => (
          <button key={section.key} type="button" onClick={() => setActiveSection(section.key)}
            data-testid={`agents-section-${section.key}`}
            className={`px-4 py-2 text-sm border-b-2 ${activeSection === section.key ? 'border-black font-medium' : 'border-transparent text-gray-500 hover:text-black'}`}>
            {t(section.labelKey, section.fallback)}
          </button>
        ))}
      </div>
      {activeSection === 'management' && <AgentListPage />}
      {activeSection === 'approvals' && isAdmin && <ApprovalsPage />}
      {activeSection === 'tool-connections' && isAdmin && <ToolConnectionsPage />}
    </div>
  )
}
