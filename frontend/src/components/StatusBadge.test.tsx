import '@/i18n'
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import StatusBadge from './StatusBadge'

describe('StatusBadge', () => {
  it('renders the published label with the published color under the zh locale', () => {
    render(<StatusBadge status="published" />)
    expect(screen.getByText('已发布')).toBeTruthy()
    const badge = screen.getByText('已发布')
    expect(badge.className).toContain('bg-emerald-100')
  })

  it('falls back to the raw status when no label exists', () => {
    render(<StatusBadge status="unknown-state" />)
    expect(screen.getByText('unknown-state')).toBeTruthy()
  })
})
