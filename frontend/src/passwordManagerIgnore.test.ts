import { describe, expect, it, vi } from 'vitest'
import { ALLOW_SELECTOR, markIgnoredFields, PASSWORD_MANAGER_IGNORE_ATTRIBUTES, watchPasswordManagerIgnore } from './passwordManagerIgnore'

class FakeElement {
  attributes = new Map<string, string>()
  children: FakeElement[] = []
  constructor(public field = true, public allowed = false) {}
  closest(selector: string) { expect(selector).toBe(ALLOW_SELECTOR); return this.allowed ? this : null }
  matches(selector: string) { expect(selector).toBe('input, textarea, select'); return this.field }
  hasAttribute(name: string) { return this.attributes.has(name) }
  setAttribute(name: string, value: string) { this.attributes.set(name, value) }
  querySelectorAll() { return this.children }
}
const mark = (root: FakeElement) => markIgnoredFields(root as unknown as HTMLElement)

describe('password manager ignore', () => {
  it('stamps fields outside the allow wrapper while preserving existing values', () => {
    const root = new FakeElement(false)
    const field = new FakeElement()
    field.attributes.set('data-bwignore', 'existing')
    const allowed = new FakeElement(true, true)
    root.children = [field, allowed]
    mark(root)
    expect([...field.attributes.keys()]).toEqual([...PASSWORD_MANAGER_IGNORE_ATTRIBUTES])
    expect(field.attributes.get('data-bwignore')).toBe('existing')
    expect(field.attributes.get('data-1p-ignore')).toBe('true')
    expect(field.attributes.get('data-lpignore')).toBe('true')
    expect(allowed.attributes.size).toBe(0)
  })
  it('stamps the root itself when it is a field', () => {
    const field = new FakeElement()
    mark(field)
    expect([...field.attributes.values()]).toEqual(['true', 'true', 'true'])
  })
  it('marks existing and newly added elements and disconnects', () => {
    const observe = vi.fn(), disconnect = vi.fn()
    let callback: MutationCallback = () => {}
    vi.stubGlobal('Element', FakeElement)
    vi.stubGlobal('MutationObserver', class {
      constructor(cb: MutationCallback) { callback = cb }
      observe = observe
      disconnect = disconnect
    })
    try {
      const root = new FakeElement()
      const stop = watchPasswordManagerIgnore(root as unknown as HTMLElement)
      expect(root.attributes.size).toBe(3)
      expect(observe).toHaveBeenCalledWith(root, { childList: true, subtree: true })
      const field = new FakeElement()
      callback([{ addedNodes: [field, {}] }] as unknown as MutationRecord[], {} as MutationObserver)
      expect(field.attributes.size).toBe(3)
      stop()
      expect(disconnect).toHaveBeenCalledOnce()
    } finally { vi.unstubAllGlobals() }
  })
})
