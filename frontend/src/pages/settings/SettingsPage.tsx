import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useTranslation } from 'react-i18next'
import { settingsApi, usersApi, promptApi } from '@/api/ontologies'
import { Trash2, Plus, Pencil, X, Check, Sparkles, Search, Loader2 } from 'lucide-react'
import {
  EXTRACTION_RULES,
  VALIDATION_RULES,
  loadRuleStates,
  saveRuleStates,
  loadValidationStates,
  saveValidationStates,
  type ExtractionRuleState,
} from '@/utils/extractionRules'

type ActiveTab = 'rules' | 'extraction_rules' | 'users' | 'prompts'

export default function SettingsPage() {
  const { t, i18n } = useTranslation()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState<ActiveTab>('rules')
  const [ruleValues, setRuleValues] = useState<Record<string, string>>({})
  const [extractStates, setExtractStates] = useState<Record<string, ExtractionRuleState>>(loadRuleStates)
  const [validationStates, setValidationStates] = useState<Record<string, boolean>>(loadValidationStates)
  const [showCreateUser, setShowCreateUser] = useState(false)
  const [userMsg, setUserMsg] = useState('')
  const [editingUserId, setEditingUserId] = useState<string | null>(null)

  // Prompts tab state
  const [showPromptModal, setShowPromptModal] = useState(false)
  const [editingPrompt, setEditingPrompt] = useState<any | null>(null)
  const [promptMsg, setPromptMsg] = useState('')
  const [promptName, setPromptName] = useState('')
  const [promptDomain, setPromptDomain] = useState('通用')
  const [promptContent, setPromptContent] = useState('')
  const [promptVersion, setPromptVersion] = useState('1.0')
  const [isGenerating, setIsGenerating] = useState(false)
  const [promptSaving, setPromptSaving] = useState(false)
  const [promptSearch, setPromptSearch] = useState('')
  const [promptDomainFilter, setPromptDomainFilter] = useState('')
  const [deletePromptTarget, setDeletePromptTarget] = useState<any | null>(null)

  const { register: regUser, handleSubmit: handleUserSubmit, reset: resetUser } =
    useForm<{ username: string; email: string; password: string; role: string }>()
  const { register: regEdit, handleSubmit: handleEditSubmit, reset: resetEdit } =
    useForm<{ username: string; email: string; password: string; role: string }>()

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['settings-rules'],
    queryFn: async () => {
      const data = await settingsApi.getRules() as any[]
      const vals: Record<string, string> = {}
      data.forEach((r: any) => { vals[r.rule_key] = r.rule_value })
      setRuleValues(vals)
      return data
    },
  })

  const { data: users = [], isLoading: usersLoading } = useQuery({
    queryKey: ['users'],
    queryFn: () => usersApi.list() as any,
    enabled: activeTab === 'users',
  })

  const updateMut = useMutation({
    mutationFn: () => settingsApi.updateRules(
      Object.entries(ruleValues).map(([rule_key, rule_value]) => ({ rule_key, rule_value }))
    ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings-rules'] }),
  })

  const createUserMut = useMutation({
    mutationFn: (data: { username: string; email: string; password: string; role: string }) =>
      usersApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      setShowCreateUser(false)
      resetUser()
      setUserMsg(t('settings.user_created'))
      setTimeout(() => setUserMsg(''), 3000)
    },
    onError: (e: any) => setUserMsg(t('settings.create_failed', { error: e?.detail || '' })),
  })

  const updateUserMut = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) => usersApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      setEditingUserId(null)
      setUserMsg(t('settings.user_updated'))
      setTimeout(() => setUserMsg(''), 3000)
    },
    onError: (e: any) => setUserMsg(t('settings.update_failed', { error: e?.detail || '' })),
  })

  const deleteUserMut = useMutation({
    mutationFn: (id: string) => usersApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['users'] }),
  })

  const { data: prompts = [], isLoading: promptsLoading } = useQuery({
    queryKey: ['prompts'],
    queryFn: () => promptApi.list() as any,
    enabled: activeTab === 'prompts',
  })

  const deletePromptMut = useMutation({
    mutationFn: (id: string) => promptApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['prompts'] })
      setDeletePromptTarget(null)
    },
  })

  function openCreatePrompt() {
    setEditingPrompt(null)
    setPromptName(''); setPromptDomain('通用'); setPromptContent(''); setPromptVersion('1.0')
    setPromptMsg(''); setShowPromptModal(true)
  }

  function openEditPrompt(p: any) {
    setEditingPrompt(p)
    setPromptName(p.name); setPromptDomain(p.domain); setPromptContent(p.content); setPromptVersion(p.version || '1.0')
    setPromptMsg(''); setShowPromptModal(true)
  }

  async function handleSavePrompt() {
    if (!promptName.trim() || !promptContent.trim()) return
    setPromptSaving(true)
    try {
      const body = { name: promptName.trim(), domain: promptDomain, content: promptContent.trim(), version: promptVersion }
      if (editingPrompt) {
        await promptApi.update(editingPrompt.id, body)
      } else {
        await promptApi.create(body)
      }
      qc.invalidateQueries({ queryKey: ['prompts'] })
      setShowPromptModal(false)
      setPromptMsg(editingPrompt ? '提示词已更新' : '提示词创建成功')
      setTimeout(() => setPromptMsg(''), 3000)
    } catch (e: any) {
      setPromptMsg(`保存失败：${e?.detail || e?.message || ''}`)
    } finally {
      setPromptSaving(false)
    }
  }

  async function handleGenerateTemplate() {
    if (!promptDomain) return
    setIsGenerating(true)
    try {
      const result = await promptApi.generateTemplate(promptDomain) as any
      setPromptContent(result.content ?? result)
    } catch (e: any) {
      setPromptMsg(`生成失败：${e?.detail || e?.message || ''}`)
    } finally {
      setIsGenerating(false)
    }
  }

  function startEditUser(u: any) {
    setEditingUserId(u.id)
    resetEdit({ username: u.username, email: u.email ?? '', password: '', role: u.role })
  }

  function updateExtractRule(id: string, patch: Partial<ExtractionRuleState>) {
    setExtractStates(prev => {
      const next = { ...prev, [id]: { ...prev[id], ...patch } }
      saveRuleStates(next)
      return next
    })
  }

  function toggleValidationRule(id: string) {
    setValidationStates(prev => {
      const next = { ...prev, [id]: !prev[id] }
      saveValidationStates(next)
      return next
    })
  }

  const tabs: { key: ActiveTab; label: string }[] = [
    { key: 'rules', label: t('settings.rules') },
    { key: 'extraction_rules', label: t('settings.tab_extraction') },
    { key: 'users', label: t('settings.tab_users') },
    { key: 'prompts', label: '提示词模版' },
  ]

  return (
    <div>
      <h2 className="text-xl font-semibold mb-6">{t('settings.title')}</h2>

      <div className="border-b mb-6">
        <div className="flex gap-1">
          {tabs.map(tab => (
            <button key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`px-4 py-2 text-sm font-medium border-b-2 ${activeTab === tab.key ? 'border-black' : 'border-transparent text-gray-500'}`}>
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {activeTab === 'rules' && (
        <div className="max-w-lg">
          <div className="bg-white border rounded-lg p-6 space-y-4">
            {isLoading ? <p className="text-gray-400">{t('common.loading')}</p> : (rules as any[]).map((r: any) => (
              <div key={r.rule_key} className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-medium">{r.rule_label_cn}</p>
                  <p className="text-xs text-gray-400">{r.rule_label_en}</p>
                </div>
                {r.editable ? (
                  <input
                    value={ruleValues[r.rule_key] ?? r.rule_value}
                    onChange={e => setRuleValues(prev => ({ ...prev, [r.rule_key]: e.target.value }))}
                    className="w-24 border rounded-lg px-2 py-1 text-sm text-right"
                  />
                ) : (
                  <span className="text-sm text-gray-500">{r.rule_value}</span>
                )}
              </div>
            ))}
            <div className="pt-2 flex justify-end">
              <button onClick={() => updateMut.mutate()} disabled={updateMut.isPending}
                className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50">
                {t('settings.save')}
              </button>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'extraction_rules' && (
        <div className="max-w-2xl space-y-6">
          <div>
            <h3 className="text-sm font-semibold mb-1">{t('settings.llm_constraints')}</h3>
            <p className="text-xs text-gray-500 mb-3">{t('settings.llm_constraints_desc')}</p>
            <div className="bg-white border rounded-lg divide-y">
              {EXTRACTION_RULES.map(rule => {
                const state = extractStates[rule.id] ?? { enabled: rule.default_enabled, value: rule.default_value }
                return (
                  <div key={rule.id} className="p-4 flex items-start gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">{rule.label_cn}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{rule.description_cn}</p>
                      {rule.has_value && state.enabled && (
                        <div className="flex items-center gap-2 mt-2">
                          <span className="text-xs text-gray-500">
                            {rule.id === 'min_confidence' ? t('settings.min_confidence') : t('settings.min_docs')}
                          </span>
                          <input
                            type="number"
                            min={rule.id === 'min_confidence' ? 0.1 : 2}
                            max={rule.id === 'min_confidence' ? 1 : 10}
                            step={rule.id === 'min_confidence' ? 0.05 : 1}
                            value={state.value ?? rule.default_value}
                            onChange={e => updateExtractRule(rule.id, { value: Number(e.target.value) })}
                            className="w-20 border rounded px-2 py-0.5 text-sm"
                          />
                        </div>
                      )}
                    </div>
                    <button
                      onClick={() => updateExtractRule(rule.id, { enabled: !state.enabled })}
                      className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors ${state.enabled ? 'bg-black' : 'bg-gray-200'}`}>
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${state.enabled ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                )
              })}
            </div>
            <p className="text-xs text-gray-400 mt-2">{t('settings.docs_hint')}</p>
          </div>

          <div>
            <h3 className="text-sm font-semibold mb-1">{t('settings.quality_rules')}</h3>
            <p className="text-xs text-gray-500 mb-3">{t('settings.quality_rules_desc')}</p>
            <div className="bg-white border rounded-lg divide-y">
              {VALIDATION_RULES.map(rule => {
                const enabled = validationStates[rule.id] ?? true
                return (
                  <div key={rule.id} className="p-4 flex items-start gap-4">
                    <div className="flex-1">
                      <p className="text-sm font-medium">{rule.label_cn}</p>
                      <p className="text-xs text-gray-400 mt-0.5">{rule.description_cn}</p>
                    </div>
                    <button
                      onClick={() => toggleValidationRule(rule.id)}
                      className={`relative inline-flex h-5 w-9 flex-shrink-0 items-center rounded-full transition-colors ${enabled ? 'bg-black' : 'bg-gray-200'}`}>
                      <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${enabled ? 'translate-x-[18px]' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}

      {activeTab === 'prompts' && (
        <div>
          {/* Toolbar */}
          <div className="flex items-center gap-3 mb-4 flex-wrap">
            <div className="relative">
              <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={promptSearch}
                onChange={e => setPromptSearch(e.target.value)}
                placeholder="按名称 / ID 筛选"
                className="pl-8 pr-7 py-1.5 border rounded-lg text-sm w-52"
              />
              {promptSearch && (
                <button onClick={() => setPromptSearch('')} className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-400 hover:text-black">
                  <X size={12} />
                </button>
              )}
            </div>
            <select
              value={promptDomainFilter}
              onChange={e => setPromptDomainFilter(e.target.value)}
              className="border rounded-lg px-3 py-1.5 text-sm"
            >
              <option value="">全部领域</option>
              {['供应链', '法律', '医疗', 'HR', '财务', '教育', '通用', '其他'].map(d => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
            <div className="flex-1" />
            {promptMsg && (
              <span className={`text-xs ${promptMsg.includes('成功') || promptMsg.includes('更新') ? 'text-green-600' : 'text-red-500'}`}>
                {promptMsg}
              </span>
            )}
            <button
              onClick={openCreatePrompt}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm"
            >
              <Plus size={14} /> 新建提示词
            </button>
          </div>

          {/* Table */}
          <div className="border rounded-xl overflow-hidden bg-white">
            {promptsLoading ? (
              <p className="text-center text-gray-400 py-8 text-sm">加载中...</p>
            ) : (prompts as any[]).filter((p: any) => {
              const q = promptSearch.toLowerCase()
              const matchSearch = !q || p.name?.toLowerCase().includes(q) || p.id?.toLowerCase().includes(q)
              const matchDomain = !promptDomainFilter || p.domain === promptDomainFilter
              return matchSearch && matchDomain
            }).length === 0 ? (
              <p className="text-center text-gray-400 py-8 text-sm">
                {(prompts as any[]).length === 0 ? '暂无提示词模版' : '没有匹配的模版'}
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">模版 ID</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">名称</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">业务域</th>
                    <th className="text-left px-4 py-2.5 text-xs font-medium text-gray-500">版本号</th>
                    <th className="px-4 py-2.5 text-xs font-medium text-gray-500 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {(prompts as any[])
                    .filter((p: any) => {
                      const q = promptSearch.toLowerCase()
                      const matchSearch = !q || p.name?.toLowerCase().includes(q) || p.id?.toLowerCase().includes(q)
                      const matchDomain = !promptDomainFilter || p.domain === promptDomainFilter
                      return matchSearch && matchDomain
                    })
                    .map((p: any) => (
                      <tr key={p.id} className="hover:bg-gray-50">
                        <td className="px-4 py-3 font-mono text-xs text-gray-400" title={p.id}>
                          {p.id?.slice(0, 8)}
                        </td>
                        <td className="px-4 py-3 font-medium text-gray-800 max-w-[200px] truncate">{p.name}</td>
                        <td className="px-4 py-3">
                          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">{p.domain}</span>
                        </td>
                        <td className="px-4 py-3 text-xs text-gray-500">v{p.version}</td>
                        <td className="px-4 py-3 text-right">
                          <div className="flex items-center gap-2 justify-end">
                            <button
                              onClick={() => openEditPrompt(p)}
                              className="p-1.5 rounded hover:bg-gray-100 text-gray-500 hover:text-black"
                              title="编辑"
                            >
                              <Pencil size={13} />
                            </button>
                            <button
                              onClick={() => setDeletePromptTarget(p)}
                              className="p-1.5 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
                              title="删除"
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Create / Edit Modal */}
          {showPromptModal && (
            <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-6" onClick={() => setShowPromptModal(false)}>
              <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl flex flex-col" style={{ maxHeight: 'calc(100vh - 3rem)' }} onClick={e => e.stopPropagation()}>
                <div className="flex items-center justify-between px-6 py-4 border-b">
                  <h3 className="font-semibold">{editingPrompt ? '编辑提示词模版' : '新建提示词模版'}</h3>
                  <button onClick={() => setShowPromptModal(false)} className="text-gray-400 hover:text-black"><X size={16} /></button>
                </div>
                <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">名称 *</label>
                      <input
                        value={promptName}
                        onChange={e => setPromptName(e.target.value)}
                        placeholder="提示词模版名称"
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-600 mb-1">业务域 *</label>
                      <select
                        value={promptDomain}
                        onChange={e => setPromptDomain(e.target.value)}
                        className="w-full border rounded-lg px-3 py-2 text-sm"
                      >
                        {['供应链', '法律', '医疗', 'HR', '财务', '教育', '通用', '其他'].map(d => (
                          <option key={d} value={d}>{d}</option>
                        ))}
                      </select>
                    </div>
                  </div>
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <label className="text-xs font-medium text-gray-600">内容 *</label>
                      <button
                        type="button"
                        onClick={handleGenerateTemplate}
                        disabled={isGenerating}
                        className="flex items-center gap-1 px-2.5 py-1 border border-gray-300 rounded text-xs text-gray-700 hover:bg-gray-100 disabled:opacity-50"
                      >
                        {isGenerating ? <Loader2 size={11} className="animate-spin" /> : <Sparkles size={11} />}
                        {isGenerating ? '生成中...' : '一键生成模版'}
                      </button>
                    </div>
                    <textarea
                      value={promptContent}
                      onChange={e => setPromptContent(e.target.value)}
                      placeholder="输入提示词内容，或点击右上角一键生成..."
                      rows={10}
                      className="w-full border rounded-lg px-3 py-2 text-sm font-mono resize-y"
                    />
                  </div>
                  {promptMsg && showPromptModal && (
                    <p className="text-xs text-red-500">{promptMsg}</p>
                  )}
                </div>
                <div className="flex justify-end gap-3 px-6 py-4 border-t">
                  <button onClick={() => setShowPromptModal(false)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                  <button
                    onClick={handleSavePrompt}
                    disabled={promptSaving || !promptName.trim() || !promptContent.trim()}
                    className="flex items-center gap-1.5 px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {promptSaving && <Loader2 size={13} className="animate-spin" />}
                    {promptSaving ? '保存中...' : '确认保存'}
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Delete confirm */}
          {deletePromptTarget && (
            <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center">
              <div className="bg-white rounded-xl shadow-lg p-6 w-96">
                <h3 className="font-semibold mb-2">删除提示词模版</h3>
                <p className="text-sm text-gray-600 mb-5">
                  确认删除「{deletePromptTarget.name}」？此操作不可撤销。
                </p>
                <div className="flex justify-end gap-3">
                  <button onClick={() => setDeletePromptTarget(null)} className="px-4 py-2 border rounded-lg text-sm">取消</button>
                  <button
                    onClick={() => deletePromptMut.mutate(deletePromptTarget.id)}
                    disabled={deletePromptMut.isPending}
                    className="px-4 py-2 bg-red-600 text-white rounded-lg text-sm disabled:opacity-50"
                  >
                    {deletePromptMut.isPending ? '删除中...' : '确认删除'}
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === 'users' && (
        <div className="max-w-2xl">
          <div className="flex items-center justify-between mb-4">
            <p className="text-sm text-gray-500">{t('settings.users_desc')}</p>
            <button
              onClick={() => { setShowCreateUser(v => !v); setUserMsg('') }}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-black text-white rounded-lg text-sm">
              <Plus size={14} /> {t('settings.new_user')}
            </button>
          </div>

          {userMsg && (
            <p className={`text-xs mb-3 ${userMsg === t('settings.user_created') || userMsg === t('settings.user_updated') ? 'text-green-600' : 'text-red-500'}`}>{userMsg}</p>
          )}

          {showCreateUser && (
            <div className="bg-gray-50 border rounded-lg p-4 mb-4">
              <h4 className="font-medium text-sm mb-3">{t('settings.create_user')}</h4>
              <form onSubmit={handleUserSubmit(d => createUserMut.mutate(d))} className="grid grid-cols-2 gap-3">
                <input {...regUser('username', { required: true })} placeholder={t('settings.username_required')}
                  className="border rounded-lg px-3 py-2 text-sm" />
                <input {...regUser('email')} placeholder={t('settings.email_optional')} type="email"
                  className="border rounded-lg px-3 py-2 text-sm" />
                <input {...regUser('password', { required: true })} placeholder={t('settings.password_required')} type="password"
                  className="border rounded-lg px-3 py-2 text-sm" />
                <select {...regUser('role')} className="border rounded-lg px-3 py-2 text-sm">
                  <option value="user">{t('settings.role_user')}</option>
                  <option value="admin">{t('settings.role_admin')}</option>
                </select>
                <div className="col-span-2 flex gap-2 justify-end">
                  <button type="button" onClick={() => setShowCreateUser(false)}
                    className="px-3 py-1.5 border rounded-lg text-sm">{t('common.cancel')}</button>
                  <button type="submit" disabled={createUserMut.isPending}
                    className="px-3 py-1.5 bg-black text-white rounded-lg text-sm disabled:opacity-50">
                    {createUserMut.isPending ? t('settings.creating') : t('settings.confirm_create')}
                  </button>
                </div>
              </form>
            </div>
          )}

          <div className="bg-white border rounded-lg overflow-hidden">
            {usersLoading ? (
              <p className="text-center text-gray-400 py-6 text-sm">{t('common.loading')}</p>
            ) : (
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    {[t('settings.col_username'), t('settings.col_email'), t('settings.col_role'), t('settings.col_created'), t('settings.col_actions')].map(h => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(users as any[]).map((u: any) => editingUserId === u.id ? (
                    <tr key={u.id} className="border-b bg-gray-50">
                      <td colSpan={5} className="px-4 py-3">
                        <form onSubmit={handleEditSubmit(d => {
                          const payload: any = { username: d.username, email: d.email, role: d.role }
                          if (d.password) payload.password = d.password
                          updateUserMut.mutate({ id: u.id, data: payload })
                        })} className="grid grid-cols-4 gap-2 items-end">
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">{t('settings.col_username')}</label>
                            <input {...regEdit('username', { required: true })}
                              className="w-full border rounded px-2 py-1.5 text-sm" />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">{t('settings.col_email')}</label>
                            <input {...regEdit('email')} type="email"
                              className="w-full border rounded px-2 py-1.5 text-sm" />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">{t('settings.new_password_label')}</label>
                            <input {...regEdit('password')} type="password" placeholder={t('settings.password_placeholder')}
                              className="w-full border rounded px-2 py-1.5 text-sm" />
                          </div>
                          <div>
                            <label className="block text-xs text-gray-500 mb-1">{t('settings.col_role')}</label>
                            <select {...regEdit('role')} className="w-full border rounded px-2 py-1.5 text-sm">
                              <option value="user">{t('settings.role_user')}</option>
                              <option value="admin">{t('settings.role_admin')}</option>
                            </select>
                          </div>
                          <div className="col-span-4 flex justify-end gap-2 mt-1">
                            <button type="button" onClick={() => setEditingUserId(null)}
                              className="flex items-center gap-1 px-3 py-1.5 border rounded text-sm text-gray-600">
                              <X size={13} /> {t('common.cancel')}
                            </button>
                            <button type="submit" disabled={updateUserMut.isPending}
                              className="flex items-center gap-1 px-3 py-1.5 bg-black text-white rounded text-sm disabled:opacity-50">
                              <Check size={13} /> {t('common.save')}
                            </button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  ) : (
                    <tr key={u.id} className="border-b last:border-0">
                      <td className="px-4 py-3 font-medium">{u.username}</td>
                      <td className="px-4 py-3 text-gray-500">{u.email || '—'}</td>
                      <td className="px-4 py-3">
                        <span className={`text-xs px-2 py-0.5 rounded-full ${u.role === 'admin' ? 'bg-black text-white' : 'bg-gray-100 text-gray-600'}`}>
                          {u.role === 'admin' ? t('settings.role_admin') : t('settings.role_user')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-500">
                        {u.created_at ? new Date(u.created_at).toLocaleDateString(i18n.language === 'zh' ? 'zh-CN' : 'en-US') : '—'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <button onClick={() => startEditUser(u)}
                            className="text-gray-500 hover:text-black">
                            <Pencil size={14} />
                          </button>
                          <button onClick={() => {
                            if (confirm(t('settings.confirm_delete_user', { name: u.username }))) deleteUserMut.mutate(u.id)
                          }} className="text-red-500 hover:text-red-700">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

    </div>
  )
}
