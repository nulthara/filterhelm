import { describe, expect, it } from 'vitest'
import type { Change } from './api'
import { reviewedBefore } from './settings'

describe('reviewedBefore', () => {
  it('preserves false, zero, arrays and null from the reviewed diff', () => {
    const changes: Change[] = [false, 0, [], null].map((before, index) => ({
      key: String(index), label: String(index), before, after: true,
      before_display: '', after_display: 'True',
    }))
    expect(reviewedBefore(changes)).toEqual({ '0': false, '1': 0, '2': [], '3': null })
  })

  it('returns an empty map for an empty diff', () => {
    expect(reviewedBefore([])).toEqual({})
  })
})
