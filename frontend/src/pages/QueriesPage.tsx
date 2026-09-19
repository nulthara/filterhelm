import { useEffect, useEffectEvent, useRef, useState, type ReactNode } from 'react'
import { FilterOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App as AntApp, Button, Card, Divider, Input, Select, Space, Statistic, Table, Tabs, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import { api, type QueryLogFilters, type QueryLogResponse, type QueryLogRow, type QuerySummary, type TopClient, type TopDomain, type UpstreamStat } from '../api'

const DEFAULT_FILTERS: QueryLogFilters = { window: 3600, length: 100, domain: '', client: '', status: '' }
const WINDOW_OPTIONS = [
  { value: 300, label: '5 minutes' }, { value: 900, label: '15 minutes' },
  { value: 3600, label: '1 hour' }, { value: 21600, label: '6 hours' },
  { value: 86400, label: '24 hours' },
]
const QUERY_STATUS_OPTIONS = [
  'UNKNOWN', 'GRAVITY', 'FORWARDED', 'CACHE', 'REGEX', 'DENYLIST',
  'EXTERNAL_BLOCKED_IP', 'EXTERNAL_BLOCKED_NULL', 'EXTERNAL_BLOCKED_NXRA',
  'GRAVITY_CNAME', 'REGEX_CNAME', 'DENYLIST_CNAME', 'RETRIED', 'RETRIED_DNSSEC',
  'IN_PROGRESS', 'DBBUSY', 'SPECIAL_DOMAIN', 'CACHE_STALE', 'EXTERNAL_BLOCKED_EDE15',
]
const STATUS_OPTIONS = [
  { value: '', label: 'Any status' }, { value: 'blocked', label: 'Blocked (any reason)' },
  { value: 'divider', label: <Divider style={{ margin: 0 }} />, disabled: true },
  ...QUERY_STATUS_OPTIONS.map((value) => ({ value, label: value })),
]

function share(count: number, total: number): string {
  return total === 0 ? '—' : `${(count * 100 / total).toFixed(1)}%`
}

interface QuerySummaryCardProps {
  title: string
  summary: QuerySummary
  extra?: ReactNode
}

function QuerySummaryCard({ title, summary, extra }: QuerySummaryCardProps) {
  return <Card title={title} extra={extra}>
    <Statistic title="Queries" value={summary.total} />
    <Statistic title="Blocked" value={summary.blocked} suffix={`(${summary.percent_blocked.toFixed(1)}%)`} />
    <Statistic title="Active clients" value={summary.active_clients} />
    <Statistic title="Domains on lists" value={summary.gravity_domains} />
  </Card>
}

interface TopPairCardProps<T> {
  title: string
  caption: string
  permitted: T[]
  blocked: T[]
  columns: TableProps<T>['columns']
  rowKey: keyof T & string
}

function TopPairCard<T extends object>({ title, caption, permitted, blocked, columns, rowKey }: TopPairCardProps<T>) {
  return <Card title={title} className="dns-error">
    <Typography.Paragraph type="secondary">{caption}</Typography.Paragraph>
    <div className="query-top-pair">
      <Table<T> title={() => 'Permitted'} dataSource={permitted} columns={columns} rowKey={rowKey} pagination={false} size="small" scroll={{ x: 'max-content' }} locale={{ emptyText: 'No permitted queries' }} />
      <Table<T> title={() => 'Blocked'} dataSource={blocked} columns={columns} rowKey={rowKey} pagination={false} size="small" scroll={{ x: 'max-content' }} locale={{ emptyText: 'No blocked queries' }} />
    </div>
  </Card>
}

function clientLabel(name: string | null, ip: string): ReactNode {
  return name === null ? ip : <Space direction="vertical" size={0}><Typography.Text>{name}</Typography.Text><Typography.Text type="secondary">{ip}</Typography.Text></Space>
}

const LOG_COLUMNS: TableProps<QueryLogRow>['columns'] = [
  { title: 'Time', dataIndex: 'time', render: (time: number) => <span title={new Date(time * 1000).toLocaleString()}>{new Date(time * 1000).toLocaleTimeString()}</span> },
  { title: 'Instance', dataIndex: 'instance', render: (name: string) => <Tag>{name}</Tag> },
  { title: 'Domain', dataIndex: 'domain', render: (domain: string, row) => <Space direction="vertical" size={0}><Typography.Text>{domain}</Typography.Text>{row.cname ? <Typography.Text type="secondary">{row.cname}</Typography.Text> : null}</Space> },
  { title: 'Type', dataIndex: 'type' },
  { title: 'Status', dataIndex: 'status', render: (status: string | null, row) => {
    const color = row.blocked ? 'error' : ['FORWARDED', 'CACHE', 'CACHE_STALE'].includes(status ?? '') ? 'success' : 'default'
    return <Tag color={color}>{status ?? '—'}</Tag>
  } },
  { title: 'Client', render: (_, row) => clientLabel(row.client_name, row.client_ip) },
  { title: 'Upstream', dataIndex: 'upstream', render: (upstream: string | null) => upstream ?? '—' },
  { title: 'Reply', render: (_, row) => <Space direction="vertical" size={0}><Typography.Text>{row.reply_type ?? '—'}</Typography.Text>{row.reply_time !== null ? <Typography.Text type="secondary">{(row.reply_time * 1000).toFixed(1)} ms</Typography.Text> : null}</Space> },
]

export default function QueriesPage() {
  const { message } = AntApp.useApp()
  const [draft, setDraft] = useState<QueryLogFilters>(DEFAULT_FILTERS)
  const [applied, setApplied] = useState<QueryLogFilters>(DEFAULT_FILTERS)
  const [result, setResult] = useState<QueryLogResponse>()
  const [loading, setLoading] = useState(false)
  const [tab, setTab] = useState('log')
  const controllerRef = useRef<AbortController | undefined>(undefined)
  const sequenceRef = useRef(0)
  const cancelled = useRef(false)

  const load = async (filters: QueryLogFilters) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const sequence = ++sequenceRef.current
    setApplied(filters)
    setLoading(true)
    try {
      const nextResult = await api.queryLog(filters, controller.signal)
      if (!cancelled.current && !controller.signal.aborted && sequence === sequenceRef.current) setResult(nextResult)
    } catch (error) {
      if (!cancelled.current && !controller.signal.aborted && sequence === sequenceRef.current) {
        message.error(error instanceof Error ? error.message : 'Unable to load query log')
      }
    } finally {
      if (!cancelled.current && !controller.signal.aborted && sequence === sequenceRef.current) setLoading(false)
    }
  }
  const onOpen = useEffectEvent(() => { void load(DEFAULT_FILTERS) })
  useEffect(() => {
    cancelled.current = false
    let disposed = false
    queueMicrotask(() => { if (!disposed) onOpen() })
    return () => {
      disposed = true
      cancelled.current = true
      controllerRef.current?.abort()
    }
  }, [])

  const names = result?.instance_names.filter((name) => result.instances[name]) ?? []
  const domainCards = names.map((name) => {
    const { summary, top_domains: domains } = result!.instances[name]
    const columns: TableProps<TopDomain>['columns'] = [
      { title: '#', render: (_, __, index) => index + 1 },
      { title: 'Domain', dataIndex: 'domain' },
      { title: 'Queries', dataIndex: 'count' },
      { title: 'Share', render: (_, row) => share(row.count, domains.permitted.includes(row) ? summary.total - summary.blocked : summary.blocked) },
    ]
    return <TopPairCard key={name} title={name} caption={`Top ${result!.top_count} in the last 24 hours`} permitted={domains.permitted} blocked={domains.blocked} columns={columns} rowKey="domain" />
  })
  const clientCards = names.map((name) => {
    const { summary, top_clients: clients } = result!.instances[name]
    const columns: TableProps<TopClient>['columns'] = [
      { title: '#', render: (_, __, index) => index + 1 },
      { title: 'Client', render: (_, row) => clientLabel(row.name, row.ip) },
      { title: 'Queries', dataIndex: 'count' },
      { title: 'Share', render: (_, row) => share(row.count, clients.permitted.includes(row) ? summary.total - summary.blocked : summary.blocked) },
    ]
    return <TopPairCard key={name} title={name} caption={`Top ${result!.top_count} in the last 24 hours`} permitted={clients.permitted} blocked={clients.blocked} columns={columns} rowKey="ip" />
  })
  const upstreams = names.flatMap((instance) => result!.instances[instance].upstreams.map((row) => ({ ...row, instance })))
    .sort((a, b) => a.instance.localeCompare(b.instance) || b.count - a.count)
  const upstreamColumns: TableProps<UpstreamStat & { instance: string }>['columns'] = [
    { title: 'Instance', dataIndex: 'instance', render: (name: string) => <Tag>{name}</Tag> },
    { title: 'Destination', render: (_, row) => <Space direction="vertical" size={0}><Typography.Text>{row.name ?? row.ip ?? '—'}</Typography.Text>{row.port !== -1 && row.ip !== null && row.ip !== row.name ? <Typography.Text type="secondary">{row.ip}:{row.port}</Typography.Text> : null}</Space> },
    { title: 'Queries', dataIndex: 'count' },
    { title: 'Share', render: (_, row) => share(row.count, result!.instances[row.instance].summary.total) },
    { title: 'Avg response', render: (_, row) => row.port === -1 ? '—' : `${(row.response * 1000).toFixed(1)} ms ± ${(row.variance * 1000).toFixed(1)} ms` },
  ]
  const log = <>
    <Table<QueryLogRow> dataSource={result?.queries} columns={LOG_COLUMNS} rowKey={(row) => `${row.instance}:${row.id}`} size="small" scroll={{ x: 'max-content' }} pagination={{ pageSize: 50, showSizeChanger: false }} loading={loading} locale={{ emptyText: 'No queries matched these filters' }} />
    {names.map((name) => {
      const instance = result!.instances[name]
      return <div key={name}>
        {instance.returned < instance.records_filtered ? <Typography.Paragraph type="secondary">{name}: showing the newest {instance.returned} of {instance.records_filtered} matching queries. Narrow the window or filters to see the rest.</Typography.Paragraph> : null}
        {instance.earliest_timestamp !== null && instance.earliest_timestamp > result!.since ? <Typography.Paragraph type="secondary">{name}: in-memory history starts at {new Date(instance.earliest_timestamp * 1000).toLocaleString()}, later than the selected window.</Typography.Paragraph> : null}
      </div>
    })}
  </>

  return <>
    <div className="page-heading">
      <div><Typography.Title level={2}>Queries</Typography.Title><Typography.Paragraph type="secondary">Recent DNS queries and 24-hour statistics across every Pi-hole instance.</Typography.Paragraph></div>
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(applied)}>Refresh</Button>
    </div>
    <div className="query-filters">
      <Select aria-label="Window" value={draft.window} options={WINDOW_OPTIONS} onChange={(window) => setDraft({ ...draft, window })} />
      <Select aria-label="Status" value={draft.status} options={STATUS_OPTIONS} onChange={(status) => setDraft({ ...draft, status })} />
      <Input aria-label="Domain" allowClear placeholder="*.doubleclick.net" value={draft.domain} onChange={(event) => setDraft({ ...draft, domain: event.target.value })} onPressEnter={() => void load(draft)} />
      <Input aria-label="Client" title="Hostnames containing only hexadecimal letters and dots, such as cafe.dead, are treated as IP patterns." allowClear placeholder="192.168.1.* or hostname" value={draft.client} onChange={(event) => setDraft({ ...draft, client: event.target.value })} onPressEnter={() => void load(draft)} />
      <Select aria-label="Per-instance limit" value={draft.length} options={[50, 100, 250, 500].map((value) => ({ value, label: value }))} onChange={(length) => setDraft({ ...draft, length })} />
      <Button type="primary" icon={<FilterOutlined />} onClick={() => void load(draft)}>Apply</Button>
    </div>
    <Typography.Paragraph type="secondary">Filters and the window apply to the query log. Statistics always cover the last 24 hours.</Typography.Paragraph>
    {Object.entries(result?.errors ?? {}).map(([name, error]) => <Alert key={name} type="error" showIcon className="dns-error" message={`${name} unavailable`} description={error} />)}
    <div className="query-summary">
      {result?.combined && result.instance_names.length > 1 ? <Card title="All instances" extra="last 24 hours">
        <Statistic title="Queries" value={result.combined.total} />
        <Statistic title="Blocked" value={result.combined.blocked} suffix={`(${result.combined.percent_blocked.toFixed(1)}%)`} />
        {result.combined.instances_reporting !== result.instance_names.length ? <Typography.Text type="secondary">{result.combined.instances_reporting} of {result.instance_names.length} instances reporting</Typography.Text> : null}
      </Card> : null}
      {names.map((name) => <QuerySummaryCard key={name} title={name} summary={result!.instances[name].summary} extra="last 24 hours" />)}
    </div>
    <Tabs activeKey={tab} onChange={setTab} items={[
      { key: 'log', label: 'Query log', children: log },
      { key: 'domains', label: 'Top domains', children: domainCards },
      { key: 'clients', label: 'Top clients', children: clientCards },
      { key: 'upstreams', label: 'Upstreams', children: <Table dataSource={upstreams} columns={upstreamColumns} rowKey={(row) => `${row.instance}:${row.name ?? ''}:${row.ip ?? ''}:${row.port}`} pagination={false} size="small" scroll={{ x: 'max-content' }} /> },
    ]} />
  </>
}
