import { useCallback, useEffect, useState } from 'react'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Empty, Form, Input, List, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import BulkActionBar from './BulkActionBar'
import { api, type BulkAction, type BulkResult, type Adlist, type AdlistRow, type GroupRow } from '../api'

type AddValues = { instances: string[]; address: string; comment: string }

const stateTag = {
  consistent: <Tag color="success">Consistent</Tag>,
  missing: <Tag color="warning">Missing</Tag>,
  conflict: <Tag color="error">Conflict</Tag>,
}

function updatedLabel(timestamp: number | null): string {
  return timestamp ? new Date(timestamp * 1000).toLocaleString() : 'Never updated'
}

function groupNameMap(rows: GroupRow[]): Record<string, Record<number, string>> {
  const result: Record<string, Record<number, string>> = {}
  for (const row of rows) for (const [instance, group] of Object.entries(row.instances)) if (group.id != null) {
    result[instance] ??= {}; result[instance][group.id] = row.name
  }
  return result
}

export default function AdlistsPage() {
  const { message, modal } = App.useApp()
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [bulkInstances, setBulkInstances] = useState<string[]>([])
  const [currentPage, setCurrentPage] = useState(1)
  const [rows, setRows] = useState<AdlistRow[]>([])
  const [groupRows, setGroupRows] = useState<GroupRow[]>([])
  const [editing, setEditing] = useState<{ instance: string; row: AdlistRow; groups: number[] }>()
  const [instanceNames, setInstanceNames] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [gravityRunning, setGravityRunning] = useState<string>()
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm<AddValues>()

  const applyResult = useCallback((result: Awaited<ReturnType<typeof api.listAdlists>>, groups: Awaited<ReturnType<typeof api.listGroups>>) => {
    setRows(result.adlists)
    setInstanceNames(result.instance_names)
    setBulkInstances(result.instance_names)
    setGroupRows(groups.groups)
    setErrors({ ...groups.errors, ...result.errors })
    setPending(result.gravity_pending.filter((name) => result.instance_names.includes(name)))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setCurrentPage(1)
    setSelectedKeys([])
    try {
      const result = await api.listAdlists()
      const groups = await api.listGroups()
      applyResult(result, groups)
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to load block lists')
    } finally {
      setLoading(false)
    }
  }, [applyResult, message])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      try {
        const result = await api.listAdlists()
        if (cancelled) return
        const groups = await api.listGroups()
        if (!cancelled) applyResult(result, groups)
      } catch (error) {
        if (!cancelled) message.error(error instanceof Error ? error.message : 'Unable to load block lists')
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [applyResult, message])

  const groupNamesByInstance = groupNameMap(groupRows)

  const showAdd = (instance?: string, source?: Adlist) => {
    form.resetFields()
    form.setFieldsValue({ instances: instance ? [instance] : [], address: source?.address ?? '', comment: source?.comment ?? '' })
    setOpen(true)
  }

  const add = async () => {
    const values = await form.validateFields()
    const confirmed = await modal.confirm({
      title: `Add block list to ${values.instances.length} instance${values.instances.length === 1 ? '' : 's'}?`,
      content: values.address,
      okText: 'Add list',
    })
    if (!confirmed) return
    setWorking(true)
    try {
      const { results } = await api.addAdlist(values.instances, values.address, values.comment ?? '')
      const failed = results.filter((result) => !result.success)
      if (failed.length) message.warning(`${results.length - failed.length} succeeded; ${failed.length} failed: ${failed.map((item) => item.name).join(', ')}`)
      else message.success(`Block list added to ${results.length} instance${results.length === 1 ? '' : 's'}`)
      setOpen(false)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to add block list')
    } finally {
      setWorking(false)
    }
  }

  const toggle = async (instance: string, adlist: Adlist, enabled: boolean) => {
    const confirmed = await modal.confirm({
      title: `${enabled ? 'Enable' : 'Disable'} this list on ${instance}?`,
      content: adlist.address,
      okText: enabled ? 'Enable list' : 'Disable list',
    })
    if (!confirmed) return
    setWorking(true)
    try {
      await api.updateAdlist(instance, adlist.address, enabled)
      message.success(`Block list ${enabled ? 'enabled' : 'disabled'} on ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to update block list')
    } finally {
      setWorking(false)
    }
  }

  const editGroups = async () => {
    if (!editing) return
    setWorking(true)
    try {
      await api.updateAdlist(editing.instance, editing.row.address, undefined, editing.groups)
      message.success(`Groups updated on ${editing.instance}`)
      setEditing(undefined)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to update groups')
    } finally {
      setWorking(false)
    }
  }

  const remove = async (instance: string, address: string) => {
    setWorking(true)
    try {
      await api.deleteAdlist(instance, address)
      message.success(`Block list removed from ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to remove block list')
    } finally {
      setWorking(false)
    }
  }

  const gravity = async (instance: string) => {
    setGravityRunning(instance)
    try {
      await api.updateGravity(instance)
      message.success(`Gravity updated on ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Gravity update failed')
    } finally {
      setGravityRunning(undefined)
    }
  }

  const applyBulk = async (action: BulkAction) => {
    const targets = bulkInstances.map((instance) => ({
      instance,
      addresses: rows.filter((row) => selectedKeys.includes(row.address) && row.instances[instance]).map((row) => row.instances[instance].address),
    })).filter((target) => target.addresses.length > 0)
    if (!targets.length) {
      message.warning('No selected records exist on the target instances')
      return
    }
    const confirmed = await modal.confirm({
      title: `${action === 'delete' ? 'Remove' : action === 'enable' ? 'Enable' : 'Disable'} selected block lists?`,
      okText: 'Apply changes',
      okButtonProps: { danger: action === 'delete' },
      content: (
        <div>
          {action === 'delete' ? <Typography.Paragraph type="warning">Every record listed below will be removed.</Typography.Paragraph> : null}
          <Typography.Paragraph>Gravity will not run automatically.</Typography.Paragraph>
          <Typography.Paragraph>Changes apply sequentially, one instance at a time.</Typography.Paragraph>
          {targets.map((target) => (
            <div key={target.instance}>
              <Typography.Text strong>{target.instance}: {target.addresses.length} records</Typography.Text>
              <List size="small" dataSource={target.addresses} renderItem={(item) => <List.Item>{item}</List.Item>} />
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
          const response = await api.bulkAdlists(target.instance, action, target.addresses)
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

  const columns: TableProps<AdlistRow>['columns'] = [
    {
      title: 'Block list', dataIndex: 'address', fixed: 'left', render: (address, row) => {
        const comment = Object.values(row.instances).find((item) => item.comment)?.comment
        return <Space direction="vertical" size={0}><Typography.Text copyable>{address}</Typography.Text>{comment ? <Typography.Text type="secondary">{comment}</Typography.Text> : null}</Space>
      },
    },
    ...instanceNames.map((instance) => ({
      title: instance,
      key: instance,
      render: (_: unknown, row: AdlistRow) => {
        const adlist = row.instances[instance]
        if (!adlist) return errors[instance]
          ? <Tag color="error">Unavailable</Tag>
          : <Button size="small" type="dashed" onClick={() => showAdd(instance, Object.values(row.instances)[0])}>Copy here</Button>
        return (
          <Space direction="vertical" size={2}>
            <Space>
              <Switch size="small" checked={adlist.enabled} disabled={working} onChange={(enabled) => void toggle(instance, adlist, enabled)} />
              <Button size="small" disabled={working} onClick={() => setEditing({ instance, row, groups: [...adlist.groups] })}>Edit groups</Button>
              <Typography.Text>{adlist.enabled ? 'Enabled' : 'Disabled'}</Typography.Text>
              <Popconfirm
                title={`Remove this list from ${instance}?`}
                description="Gravity will not run automatically."
                okText="Remove list"
                okButtonProps={{ danger: true }}
                onConfirm={() => remove(instance, adlist.address)}
              >
                <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={`Remove list from ${instance}`} />
              </Popconfirm>
            </Space>
            <Space wrap>{adlist.groups.map((id) => <Tag key={id}>{groupNamesByInstance[instance]?.[id] ?? `#${id}`}</Tag>)}</Space>
            <Tooltip title={`Last update: ${updatedLabel(adlist.date_updated)}${adlist.invalid_domains ? `; ${adlist.invalid_domains} invalid` : ''}`}>
              <Typography.Text type="secondary">{adlist.number?.toLocaleString() ?? 'Unknown'} domains</Typography.Text>
            </Tooltip>
          </Space>
        )
      },
    })),
    { title: 'State', dataIndex: 'state', fixed: 'right', render: (state: AdlistRow['state']) => stateTag[state] },
  ]

  const editOptions = editing ? Object.entries(groupNamesByInstance[editing.instance] ?? {}).map(([id, name]) => ({ value: Number(id), label: name })) : []

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Block Lists</Typography.Title>
          <Typography.Paragraph type="secondary">Manage subscribed block lists across Pi-hole instances. List changes require an explicit gravity update.</Typography.Paragraph>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>Refresh</Button>
          <Button type="primary" icon={<PlusOutlined />} disabled={!instanceNames.length} onClick={() => showAdd()}>Add list</Button>
        </Space>
      </div>
      {Object.entries(errors).map(([instance, error]) => (
        <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />
      ))}
      {pending.length ? (
        <Alert
          className="dns-error"
          type="warning"
          showIcon
          message="Gravity update required"
          description={(
            <Space wrap>
              <Typography.Text>Apply pending block-list changes:</Typography.Text>
              {pending.map((instance) => (
                <Popconfirm key={instance} title={`Update gravity on ${instance}?`} description="This can take several minutes." onConfirm={() => gravity(instance)}>
                  <Button size="small" loading={gravityRunning === instance} disabled={Boolean(gravityRunning)}>Update {instance}</Button>
                </Popconfirm>
              ))}
            </Space>
          )}
        />
      ) : null}
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
        rowKey="address"
        columns={columns}
        dataSource={rows}
        loading={loading}
        pagination={{ current: currentPage, pageSize: 25, hideOnSinglePage: true, onChange: (page) => { setCurrentPage(page); setSelectedKeys([]) } }}
        scroll={{ x: 'max-content' }}
        locale={{ emptyText: <Empty description="No block lists configured" /> }}
      />
      <Modal title={`Edit groups on ${editing?.instance ?? ''}`} open={Boolean(editing)} onCancel={() => setEditing(undefined)} onOk={() => void editGroups()} confirmLoading={working} okText="Save groups" destroyOnHidden>
        <Select mode="multiple" style={{ width: '100%' }} options={editOptions} value={editing?.groups} onChange={(groups) => setEditing((current) => current ? { ...current, groups } : current)} />
      </Modal>
      <Modal title="Add block list" open={open} onCancel={() => setOpen(false)} onOk={() => void add()} confirmLoading={working} okText="Review and add" destroyOnHidden>
        <Form form={form} layout="vertical">
          <Form.Item name="instances" label="Instances" rules={[{ required: true, message: 'Select at least one instance' }]}>
            <Select mode="multiple" options={instanceNames.map((name) => ({ value: name, label: name }))} />
          </Form.Item>
          <Form.Item name="address" label="List URL" rules={[{ required: true }, { type: 'url', message: 'Enter a complete HTTP or HTTPS URL' }]}>
            <Input placeholder="https://example.com/blocklist.txt" />
          </Form.Item>
          <Form.Item name="comment" label="Comment" initialValue="">
            <Input.TextArea rows={3} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
