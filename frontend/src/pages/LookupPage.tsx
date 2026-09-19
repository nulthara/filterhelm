import { useEffect, useRef, useState } from 'react'
import { SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Card, Checkbox, Empty, Input, Space, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import {
  api,
  type GroupRow,
  type LookupDomainMatch,
  type LookupGravityMatch,
  type LookupInstanceResult,
  type LookupResponse,
} from '../api'

interface LookupInstanceCardProps {
  name: string
  result: LookupInstanceResult
  partial: boolean
  groupNames: Record<number, string>
}

interface LookupTableRow {
  key: string
  source: 'domain-allow' | 'domain-deny' | 'gravity-block' | 'gravity-allow'
  entry: string
  address?: string
  kind: 'exact' | 'regex' | 'gravity'
  enabled: boolean
  groups: number[]
  comment: string
}

function groupNameMap(rows: GroupRow[]): Record<string, Record<number, string>> {
  const result: Record<string, Record<number, string>> = {}
  for (const row of rows) for (const [instance, group] of Object.entries(row.instances)) if (group.id != null) {
    result[instance] ??= {}; result[instance][group.id] = row.name
  }
  return result
}

function domainRow(match: LookupDomainMatch): LookupTableRow {
  const source = match.type === 'allow' ? 'domain-allow' : 'domain-deny'
  return {
    key: `domain:${match.type}:${match.kind}:${match.domain}`,
    source,
    entry: match.domain,
    kind: match.kind,
    enabled: match.enabled,
    groups: match.groups,
    comment: match.comment,
  }
}

function gravityRow(match: LookupGravityMatch): LookupTableRow {
  const source = match.type === 'block' ? 'gravity-block' : 'gravity-allow'
  return {
    key: `gravity:${match.address}:${match.domain}`,
    source,
    entry: match.domain,
    address: match.address,
    kind: 'gravity',
    enabled: match.enabled,
    groups: match.groups,
    comment: match.comment,
  }
}

function LookupInstanceCard({ name, result, partial, groupNames }: LookupInstanceCardProps) {
  const rows = [...result.domains.map(domainRow), ...result.gravity.map(gravityRow)]
  const shown = rows.length
  const domainCount = result.counts.domains_exact + result.counts.domains_regex
  const gravityCount = result.counts.gravity_allow + result.counts.gravity_block
  const verdict = result.verdict === 'blocked'
    ? <Tag color="error">Blocked</Tag>
    : result.verdict === 'allowed'
      ? <Tag color="success">Allowed</Tag>
      : <Tag>Not listed</Tag>
  const extra = result.verdict === null ? (
    <Typography.Text type="secondary">{domainCount} domain entries · {gravityCount} block-list entries</Typography.Text>
  ) : (
    <Space wrap>
      {verdict}
      {result.reason ? <Typography.Text type="secondary">{result.reason}</Typography.Text> : null}
    </Space>
  )
  const columns: TableProps<LookupTableRow>['columns'] = [
    {
      title: 'Source',
      dataIndex: 'source',
      render: (source: LookupTableRow['source'], row) => (
        <Space direction="vertical" size={0}>
          <Tag color={source === 'domain-allow' || source === 'gravity-allow' ? 'green' : 'red'}>
            {source === 'domain-allow' ? 'Allow list' : source === 'domain-deny' ? 'Deny list' : source === 'gravity-block' ? 'Block list' : 'Allow list (gravity)'}
          </Tag>
          {row.address ? <Typography.Text type="secondary">{row.address}</Typography.Text> : null}
        </Space>
      ),
    },
    {
      title: 'Entry',
      dataIndex: 'entry',
      render: (entry: string, row) => <Typography.Text code={row.kind === 'regex'}>{entry}</Typography.Text>,
    },
    { title: 'Kind', dataIndex: 'kind' },
    {
      title: 'Enabled',
      dataIndex: 'enabled',
      render: (enabled: boolean) => <Tag color={enabled ? 'success' : 'default'}>{enabled ? 'Enabled' : 'Disabled'}</Tag>,
    },
    {
      title: 'Groups',
      dataIndex: 'groups',
      render: (groups: number[]) => <Space wrap size={4}>{groups.map((id) => <Tag key={id}>{groupNames[id] ?? `#${id}`}</Tag>)}</Space>,
    },
    { title: 'Comment', dataIndex: 'comment', render: (comment: string) => <Typography.Text type="secondary">{comment}</Typography.Text> },
  ]

  return (
    <Card title={name} extra={extra} className="dns-error">
      <Table rowKey="key" columns={columns} dataSource={rows} pagination={false} size="small" scroll={{ x: 'max-content' }} />
      {result.truncated && !partial ? (
        <Alert
          type="warning"
          showIcon
          message={`Too many matches to decide whether this domain is blocked. Showing ${shown} of ${result.counts.total}.`}
        />
      ) : result.truncated ? (
        <Typography.Text type="secondary">Showing {shown} of {result.counts.total} matches. Narrow the term to see the rest.</Typography.Text>
      ) : null}
    </Card>
  )
}

export default function LookupPage() {
  const { message } = App.useApp()
  const [term, setTerm] = useState('')
  const [partial, setPartial] = useState(false)
  const [result, setResult] = useState<LookupResponse>()
  const [searching, setSearching] = useState(false)
  const [groupRows, setGroupRows] = useState<GroupRow[]>([])
  const [groupsLoading, setGroupsLoading] = useState(true)
  const lookupController = useRef<AbortController | undefined>(undefined)
  const lookupSequence = useRef(0)

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const groups = await api.listGroups()
        if (!cancelled) setGroupRows(groups.groups)
      } catch (error) {
        if (!cancelled) message.error(error instanceof Error ? error.message : 'Unable to load groups')
      } finally {
        if (!cancelled) setGroupsLoading(false)
      }
    })()
    return () => {
      cancelled = true
      lookupController.current?.abort()
    }
  }, [message])

  const search = async () => {
    if (groupsLoading) return
    const controller = new AbortController()
    lookupController.current?.abort()
    lookupController.current = controller
    const sequence = ++lookupSequence.current
    setSearching(true)
    try {
      const nextResult = await api.lookupDomain(term, partial, controller.signal)
      if (sequence === lookupSequence.current) setResult(nextResult)
    } catch (error) {
      if (!controller.signal.aborted && sequence === lookupSequence.current) {
        message.error(error instanceof Error ? error.message : 'Unable to look up domain')
      }
    } finally {
      if (sequence === lookupSequence.current) setSearching(false)
    }
  }

  const groupNamesByInstance = groupNameMap(groupRows)

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Lookup</Typography.Title>
          <Typography.Paragraph type="secondary">Find which allow, deny, or block-list entries match a domain on each Pi-hole instance.</Typography.Paragraph>
        </div>
      </div>
      <Space.Compact block className="dns-error">
        <Input value={term} placeholder="ads.example.com" allowClear onChange={(event) => setTerm(event.target.value)} onPressEnter={() => void search()} />
        <Checkbox checked={partial} onChange={(event) => setPartial(event.target.checked)}>Partial match</Checkbox>
        <Button type="primary" icon={<SearchOutlined />} loading={searching} disabled={groupsLoading} onClick={() => void search()}>Search</Button>
      </Space.Compact>
      {result ? (
        <>
          {Object.entries(result.errors).map(([instance, error]) => <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />)}
          {result.instance_names.map((name) => result.instances[name] ? (
            <LookupInstanceCard
              key={name}
              name={name}
              result={result.instances[name]}
              partial={result.partial}
              groupNames={groupNamesByInstance[name] ?? {}}
            />
          ) : null)}
          <Alert type="info" showIcon message="Verdicts consider every enabled entry. Entries scoped to specific groups may not apply to every client." />
        </>
      ) : <Empty description="Enter a domain to look it up on every instance" />}
    </>
  )
}
