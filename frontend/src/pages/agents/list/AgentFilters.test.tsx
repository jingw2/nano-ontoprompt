import '@/i18n'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AgentFilters from './AgentFilters'

describe('P2C-LIST filters', () => {
  it('debounces search input into the onChange callback', async () => {
    const onChange = vi.fn()
    render(<AgentFilters search="" status="" onChange={onChange} />)
    await userEvent.type(screen.getByPlaceholderText('搜索 Agent 名称…'), 'sup')
    expect(onChange).not.toHaveBeenCalled()
    await new Promise(r => setTimeout(r, 350))
    expect(onChange).toHaveBeenCalledWith({ search: 'sup' })
  })

  it('emits status changes immediately', async () => {
    const onChange = vi.fn()
    render(<AgentFilters search="" status="" onChange={onChange} />)
    await userEvent.selectOptions(screen.getByRole('combobox'), 'archived')
    expect(onChange).toHaveBeenCalledWith({ status: 'archived' })
  })
})
