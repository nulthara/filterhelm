export const REPOSITORY_URL = 'https://github.com/nulthara/filterhelm'

export const releaseUrl = (version: string) =>
  `${REPOSITORY_URL}/releases/tag/v${encodeURIComponent(version)}`
