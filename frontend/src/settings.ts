import type { Change, SettingsValues } from './api'

export const reviewedBefore = (changes: Change[]): SettingsValues =>
  Object.fromEntries(changes.map((change) => [change.key, change.before]))
