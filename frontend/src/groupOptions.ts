import type { GroupRow } from './api'

export const DEFAULT_GROUP = 'Default'

export function commonGroupNames(rows: GroupRow[], instances: string[]): string[] {
  if (!instances.length) return []
  return rows.filter((row) => instances.every((instance) => row.instances[instance]?.id != null))
    .map((row) => row.name)
    .sort((a, b) => a === DEFAULT_GROUP ? -1 : b === DEFAULT_GROUP ? 1 : a.localeCompare(b))
}

export function sourceGroupNames(groupIds: number[], namesById: Record<number, string> | undefined): string[] {
  return groupIds.flatMap((id) => namesById?.[id] === undefined ? [] : [namesById[id]])
}

export function groupNameMap(rows: GroupRow[]): Record<string, Record<number, string>> {
  const result: Record<string, Record<number, string>> = {}
  for (const row of rows) for (const [instance, group] of Object.entries(row.instances)) if (group.id != null) {
    result[instance] ??= {}; result[instance][group.id] = row.name
  }
  return result
}
