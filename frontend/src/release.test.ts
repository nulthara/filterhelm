import { expect, it } from 'vitest'
import { releaseUrl } from './release'

it('links to the version tag', () => {
  expect(releaseUrl('0.2.0')).toBe('https://github.com/nulthara/filterhelm/releases/tag/v0.2.0')
})
