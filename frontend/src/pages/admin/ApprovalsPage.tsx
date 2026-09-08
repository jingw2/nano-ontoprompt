import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import AgentReconciliationPage from './AgentReconciliationPage'
import McpWriteRequestsPage from '@/pages/mcp/McpWriteRequestsPage'

type TabKey = 'reconciliation' | 'mcp'

const TABS: { key: TabKey; labelKey: string; fallback: string }[] = [
  { key: 'reconciliation', labelKey: 'nav.reconciliation', fallback: '和解操作' },
  { key: 'mcp', labelKey: 'mcp.nav_label', fallback: 'MCP 待审批' },
]

/** Merged operator approvals surface (和解操作 + MCP 待审批): both are
 * human-approval queues, so they live under one admin-only menu with a tab
 * switcher rather than two separate sidebar entries. Each tab renders the
 * existing page component unchanged — this is navigation consolidation,
 * not a rewrite of either approval flow. */
export default function ApprovalsPage() {
  const { t } = useTranslation()
  const [searchParams] = useSearchParams()
  const initialTab: TabKey = searchParams.get('tab') === 'mcp' ? 'mcp' : 'reconciliation'
  const [activeTab, setActiveTab] = useState<TabKey>(initialTab)

  return (
    <div data-testid="approvals-page">
      <div className="flex gap-1 border-b mb-4">
        {TABS.map(tab => (
          <button key={tab.key} type="button" onClick={() => setActiveTab(tab.key)}
            data-testid={`approvals-tab-${tab.key}`}
            className={`px-4 py-2 text-sm border-b-2 ${activeTab === tab.key ? 'border-black font-medium' : 'border-transparent text-gray-500 hover:text-black'}`}>
            {t(tab.labelKey, tab.fallback)}
          </button>
        ))}
      </div>
      {activeTab === 'reconciliation' && <AgentReconciliationPage />}
      {activeTab === 'mcp' && <McpWriteRequestsPage />}
    </div>
  )
}
