import '@/i18n'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import AgentFilters, { type AgentFilterValues } from './AgentFilters'

const EMPTY: AgentFilterValues = { id: '', name: '', createdFrom: '', createdTo: '' }

describe('P2C-LIST filters', () => {
  it('collects ID/name/UTC-from/to inputs and applies them on Filter', async () => {
    const onApply = vi.fn()
    render(<AgentFilters values={EMPTY} onApply={onApply} onClear={vi.fn()} />)
    await userEvent.type(screen.getByLabelText('ID'), 'a-1')
    await userEvent.type(screen.getByLabelText('Name'), 'Support')
    await userEvent.type(screen.getByLabelText('Created from'), '2026-08-01T00:00:00Z')
    await userEvent.type(screen.getByLabelText('Created to'), '2026-08-31T23:59:59Z')
    await userEvent.click(screen.getByRole('button', { name: 'Filter' }))
    expect(onApply).toHaveBeenCalledWith({
      id: 'a-1', name: 'Support',
      createdFrom: '2026-08-01T00:00:00Z', createdTo: '2026-08-31T23:59:59Z',
    })
  })

  it('keeps Filter disabled until a value differs from the applied filters', async () => {
    const onApply = vi.fn()
    render(<AgentFilters values={{ id: 'a-1', name: '', createdFrom: '', createdTo: '' }} onApply={onApply} onClear={vi.fn()} />)
    expect((screen.getByRole('button', { name: 'Filter' }) as HTMLButtonElement).disabled).toBe(true)
    await userEvent.type(screen.getByLabelText('Name'), 'Support')
    await userEvent.click(screen.getByRole('button', { name: 'Filter' }))
    expect(onApply).toHaveBeenCalledWith({ id: 'a-1', name: 'Support', createdFrom: '', createdTo: '' })
  })

  it('emits clear filters', async () => {
    const onClear = vi.fn()
    render(<AgentFilters values={EMPTY} onApply={vi.fn()} onClear={onClear} />)
    await userEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(onClear).toHaveBeenCalled()
  })
})
