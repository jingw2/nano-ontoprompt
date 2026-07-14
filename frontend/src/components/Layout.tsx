import { useState } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { useAuthStore } from '@/stores/authStore'
import { useUIStore } from '@/stores/uiStore'
import { useTranslation } from 'react-i18next'
import {
  LayoutDashboard, Network, Cpu, Settings, LogOut,
  Database, ChevronLeft, ChevronRight, GitBranch, Table2,
} from 'lucide-react'

interface SubItem {
  to: string
  icon: React.ElementType
  label: string
}

interface NavItem {
  to: string
  icon: React.ElementType
  label: string
  subItems?: SubItem[]
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const logout = useAuthStore(s => s.logout)
  const navigate = useNavigate()
  const location = useLocation()
  const { t } = useTranslation()
  const { lang, setLang } = useUIStore()
  const [collapsed, setCollapsed] = useState(false)

  const navItems: NavItem[] = [
    { to: '/overview', icon: LayoutDashboard, label: t('nav.overview') },
    {
      to: '/data',
      icon: Database,
      label: t('nav.data_management'),
      subItems: [
        { to: '/data/pipelines', icon: GitBranch, label: t('nav.data_pipelines') },
        { to: '/data/structured', icon: Table2, label: t('nav.structured_data') },
      ],
    },
    { to: '/ontologies', icon: Network, label: t('nav.ontologies') },
    { to: '/models', icon: Cpu, label: t('nav.models') },
    { to: '/settings', icon: Settings, label: t('nav.settings') },
  ]

  const isActive = (to: string) => location.pathname === to || location.pathname.startsWith(to + '/')
  const isGroupActive = (item: NavItem) =>
    isActive(item.to) || (item.subItems?.some(s => isActive(s.to)) ?? false)

  return (
    <div className="flex h-screen bg-gray-50">
      <aside className={`bg-white border-r flex flex-col transition-all duration-200 ${collapsed ? 'w-16' : 'w-56'}`}>
        <div className={`p-4 border-b flex items-center ${collapsed ? 'justify-center' : 'justify-between'}`}>
          {!collapsed && <h1 className="font-bold text-lg">OntoPrompt</h1>}
        </div>

        <nav className="flex-1 p-2 space-y-0.5 overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon
            const groupActive = isGroupActive(item)

            if (item.subItems) {
              return (
                <div key={item.to}>
                  {/* Group header — clicking navigates to the overview page */}
                  <Link
                    to={item.to}
                    className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors
                      ${groupActive ? 'bg-black text-white' : 'hover:bg-gray-100 text-gray-700'}`}
                    title={collapsed ? item.label : undefined}
                  >
                    <Icon size={16} className="shrink-0" />
                    {!collapsed && <span className="flex-1">{item.label}</span>}
                  </Link>

                  {/* Sub-items — visible when group is active and sidebar is expanded */}
                  {groupActive && !collapsed && (
                    <div className="ml-4 mt-0.5 space-y-0.5 border-l border-gray-200 pl-2">
                      {item.subItems.map((sub) => {
                        const SubIcon = sub.icon
                        const subActive = isActive(sub.to)
                        return (
                          <Link
                            key={sub.to}
                            to={sub.to}
                            className={`flex items-center gap-2 px-2 py-1.5 rounded-lg text-xs transition-colors
                              ${subActive ? 'bg-gray-100 text-black font-medium' : 'text-gray-500 hover:text-black hover:bg-gray-50'}`}
                          >
                            <SubIcon size={13} className="shrink-0" />
                            <span>{sub.label}</span>
                          </Link>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            }

            return (
              <Link
                key={item.to}
                to={item.to}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm transition-colors
                  ${isActive(item.to) ? 'bg-black text-white' : 'hover:bg-gray-100 text-gray-700'}`}
                title={collapsed ? item.label : undefined}
              >
                <Icon size={16} className="shrink-0" />
                {!collapsed && <span>{item.label}</span>}
              </Link>
            )
          })}
        </nav>

        {/* Language toggle */}
        {!collapsed && (
          <div className="px-4 py-2 border-t">
            <button
              onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')}
              className="text-xs text-gray-400 hover:text-black"
            >
              {lang === 'zh' ? 'EN' : '中文'}
            </button>
          </div>
        )}

        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center p-2 border-t text-gray-400 hover:text-black"
        >
          {collapsed ? <ChevronRight size={16} /> : <ChevronLeft size={16} />}
        </button>

        <button
          onClick={() => { logout(); navigate('/login') }}
          className={`flex items-center gap-2 p-4 text-sm text-gray-500 hover:text-black border-t ${collapsed ? 'justify-center' : ''}`}
        >
          <LogOut size={16} /> {!collapsed && t('nav.logout')}
        </button>
      </aside>

      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  )
}
