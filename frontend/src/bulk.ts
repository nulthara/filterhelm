import type { BulkResult } from './api'

export const BULK_CHUNK_SIZE = 200

export interface BulkTarget<T> {
  instance: string
  items: T[]
}

export type InstanceBulkResult = BulkResult & { instance: string }

export interface BulkRun {
  results: InstanceBulkResult[]
  instanceFailures: string[]
}

export async function runBulkSequentially<T>(
  targets: BulkTarget<T>[],
  send: (instance: string, items: T[]) => Promise<{ results: BulkResult[] }>,
  chunkSize?: number,
): Promise<BulkRun> {
  const results: InstanceBulkResult[] = []
  const instanceFailures: string[] = []
  // Invariant #1: await one instance at a time; concurrent logins can race into a 401.
  for (const target of targets) {
    try {
      if (chunkSize === undefined) {
        const response = await send(target.instance, target.items)
        results.push(...response.results.map((result) => ({ ...result, instance: target.instance })))
      } else {
        for (let offset = 0; offset < target.items.length; offset += chunkSize) {
          const response = await send(target.instance, target.items.slice(offset, offset + chunkSize))
          results.push(...response.results.map((result) => ({ ...result, instance: target.instance })))
        }
      }
    } catch (error) {
      // Stop this instance's chunks after a throw; a rollback failure forbids more writes.
      instanceFailures.push(`${target.instance}: ${error instanceof Error ? error.message : 'Unable to apply bulk action'}`)
    }
  }
  return { results, instanceFailures }
}

export function summarizeBulk(
  selectedCount: number,
  run: BulkRun,
  unchangedLabel = 'already in that state',
): { text: string; failed: boolean } {
  const changed = run.results.filter((result) => result.changed).length
  const unchanged = run.results.filter((result) => result.success && !result.changed).length
  const failures = [
    ...run.results.filter((result) => !result.success).map((result) => `${result.instance} / ${result.id}: ${result.error}`),
    ...run.instanceFailures,
  ]
  const summary = `${selectedCount} selected, ${changed} changed${unchanged ? `, ${unchanged} ${unchangedLabel}` : ''}`
  if (failures.length) return { text: `${summary}; failed: ${failures.join('; ')}`, failed: true }
  return { text: summary, failed: false }
}
