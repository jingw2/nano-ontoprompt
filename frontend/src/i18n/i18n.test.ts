import { describe, expect, it } from 'vitest'
import zh from './zh.json'
import en from './en.json'

/** Issue 3 regression: agent workspace UI must be localized in both locales —
 * the detail tabs and the labels every agents page renders through t(). */
const REQUIRED_AGENT_KEYS = [
  'agent.detail.tab_basic',
  'agent.detail.tab_prompt',
  'agent.detail.tab_tools',
  'agent.detail.tab_memory',
  'agent.detail.tab_application',
  'agent.list.title',
  'agent.list.create',
  'agent.list.retry',
  'agent.list.filter',
  'agent.list.clear_filters',
  'agent.list.name_filter',
  'agent.list.created_from',
  'agent.list.created_to',
  'agent.app.send',
  'agent.app.new_session',
  'agent.app.trace',
  'agent.app.ontology_access',
  'agent.prompt.generate',
  'agent.prompt.append',
  'agent.prompt.replace',
  'agent.recon.title',
  'agent.recon.evidence',
  'agent.tools.external',
  'agent.tools.available_later',
  'agent.tools.no_external_connections',
  'agent.tools.bind_failed',
  'agent.tools.unbind_failed',
  'agent.memory.short_term_group',
  'agent.memory.long_term_group',
  'agent.memory.message_pairs',
  'agent.memory.summary_threshold',
  'agent.memory.summary_token_budget',
  'agent.memory.recall_token_budget',
  'agent.memory.recall_count',
  'agent.memory.long_term_inert_note',
  'agent.memory.available_later',
  'agent.create.partial_tool_bind_failure',
  'agent.create.go_to_detail',
]

function hasPath(obj: Record<string, unknown>, dotted: string): boolean {
  return dotted.split('.').reduce<unknown>((acc, part) => {
    if (acc && typeof acc === 'object') return (acc as Record<string, unknown>)[part]
    return undefined
  }, obj) !== undefined
}

describe('i18n agent keys', () => {
  for (const locale of [zh, en]) {
    it(`${locale === zh ? 'zh.json' : 'en.json'} defines every agent.* key used by the UI`, () => {
      const missing = REQUIRED_AGENT_KEYS.filter(key => !hasPath(locale as Record<string, unknown>, key))
      expect(missing).toEqual([])
    })
  }

  it('localizes the detail tabs to Chinese under the zh locale', () => {
    expect(zh.agent.detail.tab_basic).toBe('基本信息')
    expect(zh.agent.detail.tab_prompt).toBe('系统提示词')
    expect(zh.agent.detail.tab_tools).toBe('工具')
    expect(zh.agent.detail.tab_memory).toBe('记忆')
    expect(zh.agent.detail.tab_application).toBe('智能体应用')
  })

  it('keeps English values in en.json for the same keys', () => {
    expect(en.agent.detail.tab_basic).toBe('Basic')
    expect(en.agent.detail.tab_prompt).toBe('System Prompt')
    expect(en.agent.detail.tab_application).toBe('Agent Application')
  })
})
