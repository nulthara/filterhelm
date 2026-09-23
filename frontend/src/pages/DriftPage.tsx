import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { ArrowRightOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Col, Empty, Input, Row, Segmented, Space, Statistic, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import { api, type DriftCategory, type DriftItem, type DriftResponse, type DriftState } from '../api'
import type { DNSTab } from './DNSPage'

export interface DriftTarget {
  page: 'dns' | 'adlists' | 'domains' | 'groups' | 'clients' | 'settings'
  dnsTab?: DNSTab
}

interface DriftPageProps {
  onNavigate: (target: DriftTarget) => void
}

const DESTINATIONS: Record<DriftCategory, { target: DriftTarget; label: string }> = {
  dns: { target: { page: 'dns', dnsTab: 'hosts' }, label: 'Local DNS' },
  cname: { target: { page: 'dns', dnsTab: 'cnames' }, label: 'Local DNS › CNAME' },
  adlist: { target: { page: 'adlists' }, label: 'Block Lists' },
  domain: { target: { page: 'domains' }, label: 'Domains' },
  group: { target: { page: 'groups' }, label: 'Groups' },
  client: { target: { page: 'clients' }, label: 'Clients' },
  setting: { target: { page: 'settings' }, label: 'Settings' },
}

export default function DriftPage({ onNavigate }: DriftPageProps) {
  const { message } = App.useApp()
  const [data, setData] = useState<DriftResponse>()
  const [loading, setLoading] = useState(false)
  const [category, setCategory] = useState<DriftCategory | 'all'>('all')
  const [state, setState] = useState<DriftState | 'all'>('all')
  const [search, setSearch] = useState('')
  const controllerRef = useRef<AbortController | undefined>(undefined)

  const load = async () => {
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    setLoading(true)
    try {
      const response = await api.drift(controller.signal)
      if (!controller.signal.aborted) setData(response)
    } catch (error) {
      if (!controller.signal.aborted) {
        message.error(error instanceof Error ? error.message : 'Unable to load drift')
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false)
    }
  }
  const onOpen = useEffectEvent(() => { void load() })
  useEffect(() => {
    let disposed = false
    queueMicrotask(() => { if (!disposed) onOpen() })
    return () => {
      disposed = true
      controllerRef.current?.abort()
    }
  }, [])

  const term = search.toLowerCase()
  const rows = data?.items.filter((item) =>
    (category === 'all' || item.category === category)
    && (state === 'all' || item.state === state)
    && (item.key.toLowerCase().includes(term) || item.detail.toLowerCase().includes(term)),
  ) ?? []
  const labels = Object.fromEntries(data?.categories.map((summary) => [summary.category, summary.label]) ?? [])
  const instanceColumns: TableProps<DriftItem>['columns'] = (data?.reachable ?? []).map((name) => ({
    title: name,
    key: `instance:${name}`,
    render: (_, item) => item.values[name] ?? <Typography.Text type="secondary">—</Typography.Text>,
  }))
  const columns: TableProps<DriftItem>['columns'] = [
    { title: 'Category', key: 'category', render: (_, item) => <Tag>{labels[item.category]}</Tag> },
    {
      title: 'Item', key: 'item', render: (_, item) => (
        <Space direction="vertical" size={0}>
          <Typography.Text>{item.key}</Typography.Text>
          {item.detail ? <Typography.Text type="secondary">{item.detail}</Typography.Text> : null}
        </Space>
      ),
    },
    { title: 'State', key: 'state', render: (_, item) => item.state === 'conflict' ? <Tag color="orange">Differs</Tag> : <Tag color="blue">Missing</Tag> },
    ...instanceColumns,
    { title: 'Fix in', key: 'open', render: (_, item) => <Button type="link" size="small" icon={<ArrowRightOutlined />} onClick={() => onNavigate(DESTINATIONS[item.category].target)}>{DESTINATIONS[item.category].label}</Button> },
  ]
  const truncated = data?.categories.filter((summary) => summary.truncated) ?? []
  const emptyText = data?.comparable && data.items.length === 0
    ? <Empty description="Every instance agrees on every managed entry." />
    : <Empty description={data ? 'No drifting entries match these filters.' : 'Refresh to load drift.'} />

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Drift</Typography.Title>
          <Typography.Paragraph type="secondary">Compare managed entries across reachable Pi-hole instances.</Typography.Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>Refresh</Button>
      </div>
      {Object.entries(data?.errors ?? {}).map(([instance, error]) => <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />)}
      {data && !data.comparable ? <Alert className="dns-error" type="warning" showIcon message="Fewer than two instances answered, so this is not a cross-instance comparison." /> : null}
      <Row gutter={[16, 16]} className="dns-error">
        {data?.categories.map((summary) => (
          <Col key={summary.category} xs={24} sm={12} lg={8} xl={6}>
            <Card
              size="small"
              hoverable
              role="button"
              tabIndex={0}
              aria-pressed={category === summary.category}
              style={{ borderColor: category === summary.category ? '#1677ff' : undefined }}
              onClick={() => setCategory(category === summary.category ? 'all' : summary.category)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault()
                  setCategory(category === summary.category ? 'all' : summary.category)
                }
              }}
            >
              <Statistic title={summary.label} value={summary.drifting} suffix={`/ ${summary.total}`} styles={{ content: { color: summary.drifting > 0 ? '#cf1322' : undefined } }} />
            </Card>
          </Col>
        ))}
      </Row>
      <Space wrap className="dns-error">
        <Segmented<DriftState | 'all'> value={state} onChange={setState} options={[{ label: 'All', value: 'all' }, { label: 'Missing', value: 'missing' }, { label: 'Differs', value: 'conflict' }]} />
        <Input value={search} onChange={(event) => setSearch(event.target.value)} allowClear placeholder="Filter items and details" aria-label="Filter items and details" />
      </Space>
      <Table<DriftItem> rowKey="id" size="small" scroll={{ x: 'max-content' }} columns={columns} dataSource={rows} loading={loading} locale={{ emptyText }} />
      {truncated.map((summary) => <Typography.Paragraph key={summary.category} type="secondary">{summary.label}: showing the first {data?.items.filter((item) => item.category === summary.category).length} of {summary.drifting} drifting entries.</Typography.Paragraph>)}
    </>
  )
}
