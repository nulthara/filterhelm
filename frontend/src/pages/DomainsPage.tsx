import { useCallback, useEffect, useState } from 'react'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Empty, Form, Input, List, Modal, Popconfirm, Radio, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import BulkActionBar from './BulkActionBar'
import { api, type BulkAction, type BulkResult, type DomainRow, type GroupRow } from '../api'

type DomainType = 'allow' | 'deny'
type DomainKind = 'exact' | 'regex'
type AddValues = { instances: string[]; domain: string; type: DomainType; kind: DomainKind; comment: string }

const stateTag = {
  consistent: <Tag color="success">Consistent</Tag>,
  missing: <Tag color="warning">Missing</Tag>,
  conflict: <Tag color="error">Conflict</Tag>,
}

function groupNameMap(rows: GroupRow[]): Record<string, Record<number, string>> {
  const result: Record<string, Record<number, string>> = {}
  for (const row of rows) for (const [instance, group] of Object.entries(row.instances)) if (group.id != null) {
    result[instance] ??= {}; result[instance][group.id] = row.name
  }
  return result
}

export default function DomainsPage() {
  const { message, modal } = App.useApp()
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [bulkInstances, setBulkInstances] = useState<string[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const [rows, setRows] = useState<DomainRow[]>([])
  const [groupRows, setGroupRows] = useState<GroupRow[]>([])
  const [editing, setEditing] = useState<{ instance: string; row: DomainRow; groups: number[] }>()
  const [instanceNames, setInstanceNames] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<AddValues>()

  const applyResult = useCallback((result: Awaited<ReturnType<typeof api.listDomains>>, groups: Awaited<ReturnType<typeof api.listGroups>>) => {
    setRows(result.domains)
    setInstanceNames(result.instance_names)
    setBulkInstances(result.instance_names)
    setGroupRows(groups.groups)
    setErrors({ ...groups.errors, ...result.errors })
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setCurrentPage(1)
    setSelectedKeys([])
    try {
      const result = await api.listDomains()
      const groups = await api.listGroups()
      applyResult(result, groups)
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to load domains')
    } finally {
      setLoading(false)
    }
  }, [applyResult, message])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const result = await api.listDomains()
        if (cancelled) return
        const groups = await api.listGroups()
        if (!cancelled) applyResult(result, groups)
      } catch (error) {
        if (!cancelled) message.error(error instanceof Error ? error.message : 'Unable to load domains')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [applyResult, message])

  const groupNamesByInstance = groupNameMap(groupRows)

  const showAdd = (instance?: string, source?: DomainRow) => {
    form.resetFields()
    const comment = source ? Object.values(source.instances).find((item) => item.comment)?.comment ?? '' : ''
    form.setFieldsValue({
      instances: instance ? [instance] : [],
      domain: source?.domain ?? '',
      type: source?.type,
      kind: source?.kind ?? 'exact',
      comment,
    })
    setOpen(true)
  }

  const add = async () => {
    const values = await form.validateFields()
    const confirmed = await modal.confirm({
      title: `Add domain to ${values.instances.length} instance${values.instances.length === 1 ? '' : 's'}?`,
      content: values.domain,
      okText: 'Add domain',
    })
    if (!confirmed) return
    setWorking(true)
    try {
      const { results } = await api.addDomain(values.instances, values.domain, values.type, values.kind, values.comment ?? '')
      const failed = results.filter((result) => !result.success)
      if (failed.length) message.warning(`${results.length - failed.length} succeeded; ${failed.length} failed: ${failed.map((item) => item.name).join(', ')}`)
      else message.success(`Domain added to ${results.length} instance${results.length === 1 ? '' : 's'}`)
      setOpen(false)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to add domain')
    } finally {
      setWorking(false)
    }
  }

  const toggle = async (instance: string, row: DomainRow, enabled: boolean) => {
    const confirmed = await modal.confirm({
      title: `${enabled ? 'Enable' : 'Disable'} this domain on ${instance}?`,
      content: row.domain,
      okText: enabled ? 'Enable domain' : 'Disable domain',
    })
    if (!confirmed) return
    setWorking(true)
    try {
      await api.updateDomain(instance, row.domain, row.type, row.kind, enabled)
      message.success(`Domain ${enabled ? 'enabled' : 'disabled'} on ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to update domain')
    } finally {
      setWorking(false)
    }
  }

  const editGroups = async () => {
    if (!editing) return
    setWorking(true)
    try {
      await api.updateDomain(editing.instance, editing.row.domain, editing.row.type, editing.row.kind, undefined, editing.groups)
      message.success(`Groups updated on ${editing.instance}`)
      setEditing(undefined)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to update groups')
    } finally {
      setWorking(false)
    }
  }

  const remove = async (instance: string, row: DomainRow) => {
    setWorking(true)
    try {
      await api.deleteDomain(instance, row.domain, row.type, row.kind)
      message.success(`Domain removed from ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to remove domain')
    } finally {
      setWorking(false)
    }
  }

  const applyBulk = async (action: BulkAction) => {
    const targets = bulkInstances.map((instance) => ({
      instance,
      domains: rows.filter((row) => selectedKeys.includes(`${row.type}:${row.kind}:${row.domain}`) && row.instances[instance]).map((row) => ({ domain: row.domain, type: row.type, kind: row.kind })),
    })).filter((target) => target.domains.length > 0)
    if (!targets.length) {
      message.warning('No selected records exist on the target instances')
      return
    }
    const confirmed = await modal.confirm({
      title: `${action === 'delete' ? 'Remove' : action === 'enable' ? 'Enable' : 'Disable'} selected domains?`,
      okText: 'Apply changes',
      okButtonProps: { danger: action === 'delete' },
      content: (
        <div>
          {action === 'delete' ? <Typography.Paragraph type="warning">Every record listed below will be removed.</Typography.Paragraph> : null}
          <Typography.Paragraph>Changes apply sequentially, one instance at a time.</Typography.Paragraph>
          {targets.map((target) => (
            <div key={target.instance}>
              <Typography.Text strong>{target.instance}: {target.domains.length} records</Typography.Text>
              <List size="small" dataSource={target.domains} renderItem={(item) => <List.Item>{`${item.type}:${item.kind}:${item.domain}`}</List.Item>} />
            </div>
          ))}
        </div>
      ),
    })
    if (!confirmed) return
    setWorking(true)
    try {
      const results: Array<BulkResult & { instance: string }> = []
      const instanceFailures: string[] = []
      // Invariant #1: await one instance at a time; concurrent logins can race into a 401.
      for (const target of targets) {
        try {
          const response = await api.bulkDomains(target.instance, action, target.domains)
          results.push(...response.results.map((result) => ({ ...result, instance: target.instance })))
        } catch (error) {
          instanceFailures.push(`${target.instance}: ${error instanceof Error ? error.message : 'Unable to apply bulk action'}`)
        }
      }
      const changed = results.filter((result) => result.changed).length
      const unchanged = results.filter((result) => result.success && !result.changed).length
      const failures = [
        ...results.filter((result) => !result.success).map((result) => `${result.instance} / ${result.id}: ${result.error}`),
        ...instanceFailures,
      ]
      const summary = `${selectedKeys.length} selected, ${changed} changed${unchanged ? `, ${unchanged} already in that state` : ''}`
      if (failures.length) message.warning(`${summary}; failed: ${failures.join('; ')}`, 10)
      else message.success(summary)
      setSelectedKeys([])
      await load()
    } finally {
      setWorking(false)
    }
  }

  const columns: TableProps<DomainRow>['columns'] = [
    {
      title: 'Domain', dataIndex: 'domain', fixed: 'left', render: (domain, row) => {
        const comment = Object.values(row.instances).find((item) => item.comment)?.comment
        return (
          <Space direction="vertical" size={0}>
            <Space wrap size={4}>
              <Typography.Text copyable>{domain}</Typography.Text>
              <Tag color={row.type === 'allow' ? 'green' : 'red'}>{row.type === 'allow' ? 'Allow' : 'Deny'}</Tag>
              <Tag>{row.kind === 'exact' ? 'Exact' : 'Regex'}</Tag>
            </Space>
            {comment ? <Typography.Text type="secondary">{comment}</Typography.Text> : null}
          </Space>
        )
      },
    },
    ...instanceNames.map((instance) => ({
      title: instance,
      key: instance,
      render: (_: unknown, row: DomainRow) => {
        const item = row.instances[instance]
        if (!item) return errors[instance]
          ? <Tag color="error">Unavailable</Tag>
          : <Button size="small" type="dashed" onClick={() => showAdd(instance, row)}>Copy here</Button>
        return (
          <Space direction="vertical" size={2}>
            <Space>
              <Switch size="small" checked={item.enabled} disabled={working} onChange={(enabled) => void toggle(instance, row, enabled)} />
              <Button size="small" disabled={working} onClick={() => setEditing({ instance, row, groups: [...item.groups] })}>Edit groups</Button>
              <Typography.Text>{item.enabled ? 'Enabled' : 'Disabled'}</Typography.Text>
              <Popconfirm
                title={`Remove this domain from ${instance}?`}
                okText="Remove domain"
                okButtonProps={{ danger: true }}
                onConfirm={() => remove(instance, row)}
              >
                <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={`Remove domain from ${instance}`} />
              </Popconfirm>
            </Space>
            <Space wrap>{item.groups.map((id) => <Tag key={id}>{groupNamesByInstance[instance]?.[id] ?? `#${id}`}</Tag>)}</Space>
          </Space>
        )
      },
    })),
    { title: 'State', dataIndex: 'state', fixed: 'right', render: (state: DomainRow['state']) => stateTag[state] },
  ]

  const editOptions = editing ? Object.entries(groupNamesByInstance[editing.instance] ?? {}).map(([id, name]) => ({ value: Number(id), label: name })) : []

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Domains</Typography.Title>
          <Typography.Paragraph type="secondary">Manage individual allow and deny overrides across Pi-hole instances.</Typography.Paragraph>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>Refresh</Button>
          <Button type="primary" icon={<PlusOutlined />} disabled={!instanceNames.length} onClick={() => showAdd()}>Add domain</Button>
        </Space>
      </div>
      {Object.entries(errors).map(([instance, error]) => (
        <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />
      ))}
      <BulkActionBar
        selectedCount={selectedKeys.length}
        instanceNames={instanceNames}
        selectedInstances={bulkInstances}
        onInstancesChange={setBulkInstances}
        actions={[{ key: 'enable', label: 'Enable' }, { key: 'disable', label: 'Disable' }, { key: 'delete', label: 'Remove', danger: true }]}
        onAction={(action) => void applyBulk(action)}
        onClear={() => setSelectedKeys([])}
        disabled={working}
      />
      <Table
        rowSelection={{ selectedRowKeys: selectedKeys, onChange: (keys) => setSelectedKeys(keys.map(String)), getCheckboxProps: () => ({ disabled: working }) }}
        rowKey={(row) => `${row.type}:${row.kind}:${row.domain}`}
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ current: currentPage, pageSize: 25, hideOnSinglePage: true, onChange: (page) => { setCurrentPage(page); setSelectedKeys([]) } }}
        scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description="No domain overrides configured" /> }}
      />
      <Modal title={`Edit groups on ${editing?.instance ?? ''}`} open={Boolean(editing)} onCancel={() => setEditing(undefined)} onOk={() => void editGroups()} confirmLoading={working} okText="Save groups" destroyOnHidden>
        <Select mode="multiple" style={{ width: '100%' }} options={editOptions} value={editing?.groups} onChange={(groups) => setEditing((current) => current ? { ...current, groups } : current)} />
      </Modal>
      <Modal title="Add domain" open={open} onCancel={() => setOpen(false)} onOk={() => void add()} confirmLoading={working} okText="Review and add" destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="instances" label="Instances" rules={[{ required: true, message: 'Select at least one instance' }]}>
            <Select mode="multiple" options={instanceNames.map((name) => ({ value: name, label: name }))} />
          </Form.Item>
          <Form.Item name="domain" label="Domain or pattern" rules={[{ required: true }]}>
            <Input placeholder="ads.example.com" />
          </Form.Item>
          <Form.Item name="type" label="Type" rules={[{ required: true, message: 'Choose allow or deny' }]}>
            <Radio.Group options={[{ label: 'Allow', value: 'allow' }, { label: 'Deny', value: 'deny' }]} />
          </Form.Item>
          <Form.Item name="kind" label="Kind" initialValue="exact" rules={[{ required: true }]}>
            <Radio.Group options={[{ label: 'Exact', value: 'exact' }, { label: 'Regex', value: 'regex' }]} />
          </Form.Item>
          <Form.Item name="comment" label="Comment" initialValue="">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
