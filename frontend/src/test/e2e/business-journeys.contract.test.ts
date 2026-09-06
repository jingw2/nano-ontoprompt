import { describe, expect, it } from 'vitest'
import specSource from './business-journeys.spec.ts?raw'

describe('business journey browser contract', () => {
  it('uses a bounded wait for the real model answer', () => {
    expect(specSource).toMatch(
      /await expect\(page\.getByTestId\('journey-answer'\)\)\.toContainText\(\s*journey\.semantic_minima\.keywords\[0\]\s*,\s*\{\s*timeout:\s*90_000\s*\}\s*\)/,
    )
  })
})
