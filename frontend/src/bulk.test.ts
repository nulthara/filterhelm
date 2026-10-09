import { describe, expect, it, vi } from 'vitest'
import type { BulkResult } from './api'
import { runBulkSequentially, summarizeBulk } from './bulk'

describe('runBulkSequentially', () => {
  it('waits for each instance before starting the next', async () => {
    let resolveA!: (value: { results: BulkResult[] }) => void
    let resolveB!: (value: { results: BulkResult[] }) => void
    const send = vi.fn()
      .mockImplementationOnce(() => new Promise<{ results: BulkResult[] }>((resolve) => { resolveA = resolve }))
      .mockImplementationOnce(() => new Promise<{ results: BulkResult[] }>((resolve) => { resolveB = resolve }))
    const pending = runBulkSequentially([{ instance: 'a', items: [1] }, { instance: 'b', items: [2] }], send)
    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenNthCalledWith(1, 'a', [1])
    resolveA({ results: [] })
    await Promise.resolve()
    expect(send).toHaveBeenCalledTimes(2)
    expect(send).toHaveBeenNthCalledWith(2, 'b', [2])
    resolveB({ results: [] })
    await expect(pending).resolves.toEqual({ results: [], instanceFailures: [] })
  })

  it('sends every item in one call without a chunk size', async () => {
    const items = Array.from({ length: 450 }, (_, index) => index)
    const send = vi.fn().mockResolvedValue({ results: [] })
    await runBulkSequentially([{ instance: 'a', items }], send)
    expect(send).toHaveBeenCalledTimes(1)
    expect(send).toHaveBeenCalledWith('a', items)
  })

  it('splits items into chunks in order', async () => {
    const items = Array.from({ length: 450 }, (_, index) => index)
    const send = vi.fn().mockResolvedValue({ results: [] })
    await runBulkSequentially([{ instance: 'a', items }], send, 200)
    expect(send).toHaveBeenCalledTimes(3)
    expect(send).toHaveBeenNthCalledWith(1, 'a', items.slice(0, 200))
    expect(send).toHaveBeenNthCalledWith(2, 'a', items.slice(200, 400))
    expect(send).toHaveBeenNthCalledWith(3, 'a', items.slice(400))
  })

  it("stops an instance's chunks after a throw and continues with the next instance", async () => {
    const items = Array.from({ length: 450 }, (_, index) => index)
    const first = { id: 'first', success: true, changed: true, error: null }
    const next = { id: 'next', success: true, changed: false, error: null }
    const send = vi.fn()
      .mockResolvedValueOnce({ results: [first] })
      .mockRejectedValueOnce(new Error('rollback failed'))
      .mockResolvedValueOnce({ results: [next] })
    const run = await runBulkSequentially([{ instance: 'a', items }, { instance: 'b', items: [450] }], send, 200)
    expect(send).toHaveBeenCalledTimes(3)
    expect(send).toHaveBeenNthCalledWith(1, 'a', items.slice(0, 200))
    expect(send).toHaveBeenNthCalledWith(2, 'a', items.slice(200, 400))
    expect(send).toHaveBeenNthCalledWith(3, 'b', [450])
    expect(run.results).toEqual([{ ...first, instance: 'a' }, { ...next, instance: 'b' }])
    expect(run.instanceFailures).toEqual(['a: rollback failed'])
  })

  it('reports a non-Error rejection with the fallback message', async () => {
    const send = vi.fn().mockRejectedValue('nope')
    const run = await runBulkSequentially([{ instance: 'a', items: [1] }], send)
    expect(run.instanceFailures).toEqual(['a: Unable to apply bulk action'])
  })
})

describe('summarizeBulk', () => {
  it('counts changed and unchanged results', () => {
    const results = [true, false, false].map((changed, index) => ({ instance: 'a', id: String(index), success: true, changed, error: null }))
    expect(summarizeBulk(3, { results, instanceFailures: [] })).toEqual({ text: '3 selected, 1 changed, 2 already in that state', failed: false })
  })

  it('omits the unchanged clause when there is none', () => {
    const results = [{ instance: 'a', id: 'x', success: true, changed: true, error: null }]
    expect(summarizeBulk(1, { results, instanceFailures: [] })).toEqual({ text: '1 selected, 1 changed', failed: false })
  })

  it('uses a custom unchanged label', () => {
    const results = [{ instance: 'a', id: 'x', success: true, changed: false, error: null }]
    expect(summarizeBulk(1, { results, instanceFailures: [] }, 'already removed')).toEqual({ text: '1 selected, 0 changed, 1 already removed', failed: false })
  })

  it('lists result failures before instance failures', () => {
    const results = [{ instance: 'a', id: 'x', success: false, changed: false, error: 'gone' }]
    expect(summarizeBulk(2, { results, instanceFailures: ['b: down'] })).toEqual({ text: '2 selected, 0 changed; failed: a / x: gone; b: down', failed: true })
  })
})
