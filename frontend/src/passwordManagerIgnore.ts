export const PASSWORD_MANAGER_IGNORE_ATTRIBUTES = ['data-bwignore', 'data-1p-ignore', 'data-lpignore'] as const
export const ALLOW_SELECTOR = '[data-password-manager="allow"]'

export function markIgnoredFields(root: ParentNode & Partial<Element>): void {
  const selector = 'input, textarea, select'
  const fields = [...root.querySelectorAll(selector)]
  if (root.matches?.(selector)) fields.push(root as Element)
  for (const field of fields) {
    if (field.closest(ALLOW_SELECTOR) !== null) continue
    for (const attribute of PASSWORD_MANAGER_IGNORE_ATTRIBUTES) {
      if (!field.hasAttribute(attribute)) field.setAttribute(attribute, 'true')
    }
  }
}

export function watchPasswordManagerIgnore(target: HTMLElement): () => void {
  markIgnoredFields(target)
  const observer = new MutationObserver((records) => {
    for (const record of records) for (const node of record.addedNodes) {
      if (node instanceof Element) markIgnoredFields(node)
    }
  })
  observer.observe(target, { childList: true, subtree: true })
  return () => observer.disconnect()
}
