export type BulkAction = 'enable' | 'disable' | 'delete'

export interface BulkResult {
  id: string
  success: boolean
  changed: boolean
  error: string | null
}

export interface DomainRef {
  domain: string
  type: 'allow' | 'deny'
  kind: 'exact' | 'regex'
}

export interface DNSRecordRef {
  hostname: string
  address: string
}

export interface CNAMERecordRef {
  hostname: string
  target: string
}

export type Status = 'success' | 'warning' | 'error'

export interface AuthStatus {
  mode: 'local' | 'proxy'
  configured: boolean
  authenticated: boolean
  user: string | null
}

export interface ComponentVersion {
  local: string | null
  remote: string | null
  update_available: boolean | null
}

export interface PiHoleVersions {
  core: ComponentVersion
  web: ComponentVersion
  ftl: ComponentVersion
  docker: ComponentVersion
  compatibility: 'compatible' | 'incompatible' | 'untested' | 'unknown'
  compatibility_detail: string
  update_available: boolean | null
}

export interface HealthMemory {
  total: number
  used: number
  available: number
  percent: number
}

export interface HealthSwap {
  total: number
  used: number
  percent: number
}

export interface HealthCpu {
  cores: number
  percent: number
  load: [number, number, number]
}

export interface HealthTemperature {
  value: number
  limit: number | null
  unit: string
}

export interface HealthFtl {
  cpu_percent: number
  mem_percent: number
}

export interface HealthDatabase {
  size: number
  queries: number
  sqlite_version: string
}

export type HealthStatus = 'healthy' | 'warning' | 'critical'

export interface InstanceHealth {
  status: HealthStatus
  reasons: string[]
  host_uptime: number
  ftl_uptime: number
  memory: HealthMemory
  swap: HealthSwap
  cpu: HealthCpu
  temperature: HealthTemperature | null
  ftl: HealthFtl
  database: HealthDatabase
}

export interface Instance {
  name: string
  base_url: string
  notes: string
  verify_ssl: boolean
  status: Status
  status_detail: string
  versions: PiHoleVersions | null
  health: InstanceHealth | null
}

export interface InstanceInput {
  name: string
  base_url: string
  app_password: string
  notes: string
  verify_ssl: boolean
}

export interface InstanceUpdate {
  base_url: string
  app_password?: string
  notes: string
  verify_ssl: boolean
}

export interface SettingField {
  key: string
  label: string
  kind: 'str' | 'list' | 'lines' | 'bool' | 'int'
}

export type SettingValue = string | string[] | boolean | number | null
export type SettingsValues = Record<string, SettingValue>

export interface Change {
  key: string
  label: string
  before: SettingValue
  after: SettingValue
  before_display: string
  after_display: string
}

export interface CNAMERecord {
  hostname: string
  target: string
  ttl: number | null
  raw: string
}

export interface CNAMERow {
  hostname: string
  instances: Record<string, CNAMERecord[]>
  state: 'consistent' | 'missing' | 'conflict'
}

export interface CNAMERecordInput {
  instance: string
  hostname: string
  target: string
  ttl?: number | null
}

export interface CNAMERecordUpdate extends CNAMERecordInput {
  old_hostname: string
  old_target: string
  old_ttl?: number | null
}

export interface DNSRecord {
  hostname: string
  address: string
  raw: string
}

export interface DNSRow {
  hostname: string
  instances: Record<string, DNSRecord[]>
  state: 'consistent' | 'missing' | 'conflict'
}

export interface DNSRecordInput {
  instance: string
  hostname: string
  address: string
}

export interface BlockingStatus {
  name: string
  reachable: boolean
  enabled: boolean | null
  timer: number | null
  error: string | null
}

export interface BlockingResult {
  name: string
  success: boolean
  enabled: boolean | null
  timer: number | null
  error: string | null
}

export interface Adlist {
  address: string
  comment: string
  enabled: boolean
  groups: number[]
  date_updated: number | null
  number: number | null
  invalid_domains: number | null
}

export interface AdlistRow {
  address: string
  instances: Record<string, Adlist>
  state: 'consistent' | 'missing' | 'conflict'
}

export interface Domain {
  comment: string
  enabled: boolean
  groups: number[]
  date_added: number | null
  date_modified: number | null
}

export interface DomainRow {
  domain: string
  type: 'allow' | 'deny'
  kind: 'exact' | 'regex'
  instances: Record<string, Domain>
  state: 'consistent' | 'missing' | 'conflict'
}

export interface Group {
  id: number | null
  comment: string
  enabled: boolean
  date_added: number | null
  date_modified: number | null
}

export type OrphanAction = 'ignore' | 'delete' | 'reassign'

export interface GroupOrphanAdlist {
  address: string
  comment: string
  enabled: boolean
}

export interface GroupOrphanDomain {
  domain: string
  type: 'allow' | 'deny'
  kind: 'exact' | 'regex'
  comment: string
  enabled: boolean
}

export interface GroupOrphans {
  instance: string
  name: string
  group_id: number
  adlists: GroupOrphanAdlist[]
  domains: GroupOrphanDomain[]
}

export interface OrphanResult {
  kind: 'adlist' | 'domain'
  id: string
  success: boolean
  error: string | null
}

export interface GroupRow {
  name: string
  instances: Record<string, Group>
  state: 'consistent' | 'missing' | 'conflict'
}

export interface Client {
  comment: string
  groups: number[]
  date_added: number | null
  date_modified: number | null
}

export interface ClientRow {
  client: string
  instances: Record<string, Client>
  state: 'consistent' | 'missing'
}

export interface LookupDomainMatch {
  domain: string
  type: 'allow' | 'deny'
  kind: 'exact' | 'regex'
  comment: string
  enabled: boolean
  groups: number[]
}

export interface LookupGravityMatch {
  domain: string
  address: string
  type: 'allow' | 'block'
  comment: string
  enabled: boolean
  groups: number[]
}

export interface LookupCounts {
  domains_exact: number
  domains_regex: number
  gravity_allow: number
  gravity_block: number
  total: number
}

export interface LookupInstanceResult {
  verdict: 'blocked' | 'allowed' | 'unlisted' | null
  reason: string | null
  domains: LookupDomainMatch[]
  gravity: LookupGravityMatch[]
  counts: LookupCounts
  truncated: boolean
}

export interface LookupResponse {
  term: string
  partial: boolean
  limit: number
  instance_names: string[]
  instances: Record<string, LookupInstanceResult>
  errors: Record<string, string>
}

export interface QuerySummary {
  total: number
  blocked: number
  percent_blocked: number
  unique_domains: number
  forwarded: number
  cached: number
  active_clients: number
  total_clients: number
  gravity_domains: number
  gravity_last_update: number
}

export interface TopDomain {
  domain: string
  count: number
}

export interface TopClient {
  ip: string
  name: string | null
  count: number
}

export interface UpstreamStat {
  ip: string | null
  name: string | null
  port: number
  count: number
  response: number
  variance: number
}

export interface QueryLogInstance {
  summary: QuerySummary
  top_domains: { permitted: TopDomain[]; blocked: TopDomain[] }
  top_clients: { permitted: TopClient[]; blocked: TopClient[] }
  upstreams: UpstreamStat[]
  records_filtered: number
  returned: number
  earliest_timestamp: number | null
}

export interface QueryLogRow {
  instance: string
  id: number
  time: number
  type: string
  domain: string
  cname: string | null
  status: string | null
  blocked: boolean
  client_ip: string
  client_name: string | null
  upstream: string | null
  reply_type: string | null
  reply_time: number | null
}

export interface QueryLogCombined {
  total: number
  blocked: number
  percent_blocked: number
  instances_reporting: number
}

export interface QueryLogFilters {
  window: number
  length: number
  domain: string
  client: string
  status: string
}

export interface QueryLogResponse extends QueryLogFilters {
  since: number
  top_count: number
  instance_names: string[]
  instances: Record<string, QueryLogInstance>
  combined: QueryLogCombined | null
  queries: QueryLogRow[]
  errors: Record<string, string>
}

type SessionExpiredHandler = () => void
let sessionExpiredHandlers: SessionExpiredHandler[] = []

export function onSessionExpired(handler: SessionExpiredHandler): () => void {
  sessionExpiredHandlers.push(handler)
  return () => {
    sessionExpiredHandlers = sessionExpiredHandlers.filter((existing) => existing !== handler)
  }
}

function errorDetail(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  const message = (item: unknown): string | null => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object' && typeof (item as { msg?: unknown }).msg === 'string') {
      return (item as { msg: string }).msg
    }
    return null
  }
  if (Array.isArray(detail)) {
    const messages = detail.map(message).filter((entry): entry is string => entry !== null)
    if (messages.length) return messages.join('; ')
  }
  return message(detail) ?? fallback
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: init?.body ? { 'Content-Type': 'application/json', ...init.headers } : init?.headers,
  })
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      detail = errorDetail(body?.detail, detail)
    } catch {
      // The status text remains the useful fallback for a non-JSON response.
    }
    if (response.status === 401 && !path.startsWith('/api/auth/login')) {
      sessionExpiredHandlers.forEach((handler) => handler())
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export type DriftCategory = 'dns' | 'cname' | 'adlist' | 'domain' | 'group' | 'client' | 'setting'
export type DriftState = 'missing' | 'conflict'

export interface DriftCategorySummary {
  category: DriftCategory
  label: string
  total: number
  drifting: number
  truncated: boolean
}

export interface DriftItem {
  id: string
  category: DriftCategory
  key: string
  detail: string
  state: DriftState
  values: Record<string, string | null>
}

export interface DriftResponse {
  instance_names: string[]
  reachable: string[]
  comparable: boolean
  errors: Record<string, string>
  categories: DriftCategorySummary[]
  items: DriftItem[]
}

export const api = {
  drift: (signal?: AbortSignal) => request<DriftResponse>('/api/drift', { signal }),
  queryLog: (filters: QueryLogFilters, signal?: AbortSignal) => {
    const params = new URLSearchParams({
      window: String(filters.window),
      length: String(filters.length),
      domain: filters.domain,
      client: filters.client,
      status: filters.status,
    })
    return request<QueryLogResponse>(`/api/query-log?${params.toString()}`, { signal })
  },

  bulkAdlists: (instance: string, action: BulkAction, addresses: string[]) =>
    request<{ instance: string; action: BulkAction; results: BulkResult[]; gravity_pending: boolean }>(
      `/api/adlists/${encodeURIComponent(instance)}/bulk`,
      { method: 'POST', body: JSON.stringify({ action, addresses }) },
    ),
  bulkDomains: (instance: string, action: BulkAction, domains: DomainRef[]) =>
    request<{ instance: string; action: BulkAction; results: BulkResult[] }>(
      `/api/domains/${encodeURIComponent(instance)}/bulk`,
      { method: 'POST', body: JSON.stringify({ action, domains }) },
    ),
  bulkDNSRecords: (instance: string, records: DNSRecordRef[]) =>
    request<{ instance: string; action: 'delete'; results: BulkResult[] }>('/api/dns/records/bulk', {
      method: 'POST', body: JSON.stringify({ instance, action: 'delete', records }),
    }),
  bulkDNSCnames: (instance: string, records: CNAMERecordRef[]) =>
    request<{ instance: string; action: 'delete'; results: BulkResult[] }>('/api/dns/cnames/bulk', {
      method: 'POST',
      body: JSON.stringify({ instance, action: 'delete', records }),
    }),
  onSessionExpired,
  authStatus: () => request<AuthStatus>('/api/auth/status'),
  login: (username: string, password: string) =>
    request<{ user: string }>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  logout: () => request<void>('/api/auth/logout', { method: 'POST' }),
  listInstances: (options?: { health?: boolean }) =>
    request<{ instances: Instance[] }>(options?.health ? '/api/instances?health=true' : '/api/instances'),
  saveInstance: (input: InstanceInput) =>
    request<Instance>('/api/instances', { method: 'POST', body: JSON.stringify(input) }),
  updateInstance: (name: string, input: InstanceUpdate) =>
    request<Instance>(`/api/instances/${encodeURIComponent(name)}`, { method: 'PATCH', body: JSON.stringify(input) }),
  deleteInstance: (name: string) =>
    request<void>(`/api/instances/${encodeURIComponent(name)}`, { method: 'DELETE' }),
  testInstance: (name: string) =>
    request<{ status: Status; detail: string; versions: PiHoleVersions | null }>(`/api/instances/${encodeURIComponent(name)}/test`, { method: 'POST' }),
  status: (options?: { background?: boolean }) =>
    request<{ instances: BlockingStatus[] }>(
      '/api/status',
      options?.background ? { headers: { 'X-Pihole-Manager-Poll': '1' } } : undefined,
    ),
  updateBlocking: (instances: string[], enabled: boolean, duration?: 10 | 30 | 300) =>
    request<{ results: BlockingResult[] }>('/api/blocking', {
      method: 'POST', body: JSON.stringify({ instances, enabled, duration }),
    }),
  listAdlists: () => request<{
    instance_names: string[]
    adlists: AdlistRow[]
    errors: Record<string, string>
    gravity_pending: string[]
  }>('/api/adlists'),
  addAdlist: (instances: string[], address: string, comment: string) =>
    request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/adlists', {
      method: 'POST', body: JSON.stringify({ instances, address, comment }),
    }),
  updateAdlist: (instance: string, address: string, enabled?: boolean, groups?: number[]) =>
    request<{ instance: string; address: string; enabled: boolean; groups: number[]; gravity_pending: boolean }>(`/api/adlists/${encodeURIComponent(instance)}`, {
      method: 'PATCH', body: JSON.stringify({ address, ...(enabled !== undefined ? { enabled } : {}), ...(groups ? { groups } : {}) }),
    }),
  deleteAdlist: (instance: string, address: string) =>
    request<{ instance: string; address: string; gravity_pending: boolean }>(`/api/adlists/${encodeURIComponent(instance)}`, {
      method: 'DELETE', body: JSON.stringify({ address }),
    }),
  updateGravity: (instance: string) =>
    request<{ instance: string; gravity_pending: boolean }>(`/api/adlists/${encodeURIComponent(instance)}/gravity`, { method: 'POST' }),
  listDomains: () => request<{
    instance_names: string[]
    domains: DomainRow[]
    errors: Record<string, string>
  }>('/api/domains'),
  lookupDomain: (domain: string, partial: boolean, signal?: AbortSignal) =>
    request<LookupResponse>(
      `/api/lookup?domain=${encodeURIComponent(domain)}&partial=${partial}`,
      { signal },
    ),
  addDomain: (instances: string[], domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex', comment: string) =>
    request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/domains', {
      method: 'POST', body: JSON.stringify({ instances, domain, type, kind, comment }),
    }),
  updateDomain: (instance: string, domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex', enabled?: boolean, groups?: number[]) =>
    request<{ instance: string; domain: string; type: string; kind: string; enabled: boolean; groups: number[] }>(
      `/api/domains/${encodeURIComponent(instance)}`,
      { method: 'PATCH', body: JSON.stringify({ domain, type, kind, ...(enabled !== undefined ? { enabled } : {}), ...(groups ? { groups } : {}) }) },
    ),
  deleteDomain: (instance: string, domain: string, type: 'allow' | 'deny', kind: 'exact' | 'regex') =>
    request<{ instance: string; domain: string; type: string; kind: string }>(
      `/api/domains/${encodeURIComponent(instance)}`,
      { method: 'DELETE', body: JSON.stringify({ domain, type, kind }) },
    ),
  listGroups: () => request<{
    instance_names: string[]
    groups: GroupRow[]
    errors: Record<string, string>
  }>('/api/groups'),
  addGroup: (instances: string[], name: string, comment: string) =>
    request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/groups', {
      method: 'POST', body: JSON.stringify({ instances, name, comment }),
    }),
  updateGroup: (instance: string, name: string, enabled: boolean) =>
    request<{ instance: string; name: string; enabled: boolean }>(
      `/api/groups/${encodeURIComponent(instance)}`,
      { method: 'PATCH', body: JSON.stringify({ name, enabled }) },
    ),
  groupOrphans: (instance: string, name: string, signal?: AbortSignal) =>
    request<GroupOrphans>(
      `/api/groups/${encodeURIComponent(instance)}/orphans?name=${encodeURIComponent(name)}`,
      { signal },
    ),
  deleteGroup: (instance: string, name: string, orphans: OrphanAction = 'ignore', confirmDefault = false) =>
    request<{ instance: string; name: string; deleted: boolean; orphans: OrphanResult[]; gravity_pending: boolean }>(
      `/api/groups/${encodeURIComponent(instance)}`,
      { method: 'DELETE', body: JSON.stringify({ name, orphans, confirm_default: confirmDefault }) },
    ),
  listClients: () => request<{
    instance_names: string[]
    clients: ClientRow[]
    errors: Record<string, string>
  }>('/api/clients'),
  addClient: (instances: string[], client: string, comment: string) =>
    request<{ results: Array<{ name: string; success: boolean; error: string | null }> }>('/api/clients', {
      method: 'POST', body: JSON.stringify({ instances, client, comment }),
    }),
  updateClientGroups: (instance: string, client: string, groups: number[]) =>
    request<{ instance: string; client: string; groups: number[] }>(
      `/api/clients/${encodeURIComponent(instance)}`,
      { method: 'PATCH', body: JSON.stringify({ client, groups }) },
    ),
  deleteClient: (instance: string, client: string) =>
    request<{ instance: string; client: string }>(
      `/api/clients/${encodeURIComponent(instance)}`,
      { method: 'DELETE', body: JSON.stringify({ client }) },
    ),
  listDNSCnames: () =>
    request<{ instance_names: string[]; records: CNAMERow[]; errors: Record<string, string> }>('/api/dns/cnames'),
  addDNSCname: (input: CNAMERecordInput) =>
    request<{ instance: string; record: string }>('/api/dns/cnames', { method: 'POST', body: JSON.stringify(input) }),
  deleteDNSCname: (input: CNAMERecordInput) =>
    request<{ deleted: string }>('/api/dns/cnames', { method: 'DELETE', body: JSON.stringify(input) }),
  updateDNSCname: (input: CNAMERecordUpdate) =>
    request<{ changed: boolean; record: string }>('/api/dns/cnames', { method: 'PATCH', body: JSON.stringify(input) }),
  listDNSRecords: () =>
    request<{ instance_names: string[]; records: DNSRow[]; errors: Record<string, string> }>('/api/dns/records'),
  addDNSRecord: (input: DNSRecordInput) =>
    request<{ instance: string; record: string }>('/api/dns/records', { method: 'POST', body: JSON.stringify(input) }),
  deleteDNSRecord: (input: DNSRecordInput) =>
    request<{ deleted: string }>('/api/dns/records', { method: 'DELETE', body: JSON.stringify(input) }),
  updateDNSRecord: (input: DNSRecordInput & { old_hostname: string; old_address: string }) =>
    request<{ changed: boolean; record: string }>('/api/dns/records', { method: 'PATCH', body: JSON.stringify(input) }),
  getSettings: (name: string) =>
    request<{ instance: string; fields: SettingField[]; values: SettingsValues }>(`/api/settings/${encodeURIComponent(name)}`),
  diffSettings: (name: string, values: SettingsValues) =>
    request<{ changes: Change[] }>(`/api/settings/${encodeURIComponent(name)}/diff`, {
      method: 'POST', body: JSON.stringify({ values }),
    }),
  applySettings: (name: string, values: SettingsValues) =>
    request<{ applied: number; changes: Change[] }>(`/api/settings/${encodeURIComponent(name)}/apply`, {
      method: 'POST', body: JSON.stringify({ values }),
    }),
}
