import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { ReloadOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Input, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import { api, type AuditEntry, type AuditLogFilters, type AuditLogResponse } from '../api'

const DEFAULT_FILTERS: AuditLogFilters = { instance: '', action: '', actor: '', text: '', limit: 200 }
const COLUMNS: TableProps<AuditEntry>['columns'] = [
  { title: 'Time', dataIndex: 'ts', render: (ts: string) => {
    const date = new Date(ts)
    return Number.isNaN(date.getTime()) ? ts : date.toLocaleString()
  } },
  { title: 'Instance', dataIndex: 'instance' },
  { title: 'Action', dataIndex: 'action', render: (action: string) => <Tag>{action}</Tag> },
  { title: 'Actor', dataIndex: 'actor', render: (actor: string | null) => actor === null ? <Typography.Text type="secondary">—</Typography.Text> : actor },
]

export default function AuditPage() {
  const [filters, setFilters] = useState<AuditLogFilters>(DEFAULT_FILTERS)
  const [result, setResult] = useState<AuditLogResponse>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const controllerRef = useRef<AbortController | undefined>(undefined)

  const load = async (nextFilters: AuditLogFilters) => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setLoading(true)
    setError(undefined)
    try {
      const nextResult = await api.auditLog(nextFilters, controller.signal)
      if (!controller.signal.aborted) setResult(nextResult)
    } catch (error) {
      if (!controller.signal.aborted) setError(error instanceof Error ? error.message : 'Unable to load audit log')
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }
  const onOpen = useEffectEvent(() => { void load(DEFAULT_FILTERS) })
  useEffect(() => {
    let disposed = false
    queueMicrotask(() => { if (!disposed) onOpen() })
    return () => {
      disposed = true
      controllerRef.current?.abort()
    }
  }, [])

  const changeFilter = (key: 'instance' | 'action' | 'actor', value: string | undefined) => {
    const nextFilters = { ...filters, [key]: value ?? '' }
    setFilters(nextFilters)
    void load(nextFilters)
  }
  const rows = result?.entries.map((entry, index) => ({ ...entry, key: `${index}-${entry.ts}` })) ?? []

  return <>
    <div className="page-heading">
      <div><Typography.Title level={2}>Audit log</Typography.Title><Typography.Paragraph type="secondary">Every change and sign-in this app has recorded, newest first. Read-only.</Typography.Paragraph></div>
    </div>
    <Space wrap style={{ marginBottom: 16 }}>
      <Select aria-label="Instance" allowClear placeholder="All instances" style={{ minWidth: 180 }} value={filters.instance || undefined} options={result?.instances.map((value) => ({ value, label: value }))} onChange={(value) => changeFilter('instance', value)} />
      <Select aria-label="Action" allowClear placeholder="All actions" style={{ minWidth: 180 }} value={filters.action || undefined} options={result?.actions.map((value) => ({ value, label: value }))} onChange={(value) => changeFilter('action', value)} />
      <Select aria-label="Actor" allowClear placeholder="All actors" style={{ minWidth: 180 }} value={filters.actor || undefined} options={result?.actors.map((value) => ({ value, label: value }))} onChange={(value) => changeFilter('actor', value)} />
      <Input.Search aria-label="Search values" placeholder="Search values" value={filters.text} onChange={(event) => setFilters({ ...filters, text: event.target.value })} onSearch={() => void load(filters)} />
      <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(filters)}>Refresh</Button>
    </Space>
    {error ? <Alert type="error" message="Audit log unavailable" description={error} className="dns-error" /> : null}
    {result && result.skipped > 0 ? <Alert type="warning" message={`${result.skipped} unreadable line(s) in the audit log were skipped.`} className="dns-error" /> : null}
    {result ? <Typography.Text type="secondary">Showing {result.returned} of {result.matched} matching entries{result.matched > result.returned ? ` (newest ${result.limit})` : ''}</Typography.Text> : null}
    <Table dataSource={rows} columns={COLUMNS} rowKey="key" size="small" pagination={{ pageSize: 50 }} loading={loading}
      locale={{ emptyText: loading ? null : <Empty description="No audit entries match" /> }}
      expandable={{ expandedRowRender: (entry) => <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
        {(['before', 'after'] as const).map((key) => <div key={key}>
          <Typography.Title level={5}>{key === 'before' ? 'Before' : 'After'}</Typography.Title>
          {Object.keys(entry[key]).length ? <pre style={{ overflowX: 'auto' }}>{JSON.stringify(entry[key], null, 2)}</pre> : <Typography.Text type="secondary">(empty)</Typography.Text>}
        </div>)}
      </div> }}
    />
  </>
}
