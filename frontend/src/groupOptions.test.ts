import { describe, expect, it } from 'vitest'
import type { GroupRow } from './api'
import { commonGroupNames, groupNameMap, sourceGroupNames } from './groupOptions'

const row = (name: string, a: number | null, b?: number | null): GroupRow => ({
  name, state: 'consistent', instances: Object.fromEntries(
    Object.entries({ a, b }).filter(([, id]) => id !== undefined).map(([instance, id]) => [instance, {
      id: id ?? null, comment: '', enabled: true, date_added: null, date_modified: null,
    }]),
  ),
})

describe('group options', () => {
  const rows = [row('Zoo', 2, 4), row('Kids', 3, 7), row('Default', 0, 0), row('Missing', 8), row('No ID', 9, null)]
  it('intersects names, with Default first and then alphabetical order', () => {
    expect(commonGroupNames(rows, ['a', 'b'])).toEqual(['Default', 'Kids', 'Zoo'])
    expect(commonGroupNames(rows, ['b'])).toEqual(['Default', 'Kids', 'Zoo'])
    expect(commonGroupNames(rows, [])).toEqual([])
  })
  it('maps source IDs and drops unknown IDs', () => {
    const names = groupNameMap(rows)
    expect(sourceGroupNames([7, 0, 999], names.b)).toEqual(['Kids', 'Default'])
    expect(sourceGroupNames([0], undefined)).toEqual([])
    expect(names.b[3]).toBeUndefined()
  })
})
