import { useCallback, useEffect, useMemo, useState } from 'react'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Empty, Form, Input, List, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import BulkActionBar from './BulkActionBar'
import { api, type BulkAction, type BulkResult, type DNSRecord, type DNSRecordInput, type DNSRow } from '../api'

type EditingRecord = DNSRecordInput

// Shared with the CNAME tab, as both tables use the same states.
// eslint-disable-next-line react-refresh/only-export-components
export const stateTag = {
  consistent: <Tag color="success">Consistent</Tag>,
  missing: <Tag color="warning">Missing</Tag>,
  conflict: <Tag color="error">Conflict</Tag>,
}

function filterDNSRows(rows: DNSRow[], search: string): DNSRow[] {
  const query = search.trim().toLowerCase()
  if (!query) return rows
  return rows.filter((row) => {
    if (row.hostname.toLowerCase().includes(query)) return true
    return Object.entries(row.instances).some(([instance, records]) =>
      instance.toLowerCase().includes(query)
      || records.some((record) => record.address.toLowerCase().includes(query)),
    )
  })
}

export default function DNSHostsTab() {
  const { message, modal } = App.useApp()
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [bulkInstances, setBulkInstances] = useState<string[]>([])
  const [working, setWorking] = useState(false)
  const [rows, setRows] = useState<DNSRow[]>([])
  const [instanceNames, setInstanceNames] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<EditingRecord>()
  const [saving, setSaving] = useState(false)
  const [search, setSearch] = useState('')
  const [currentPage, setCurrentPage] = useState(1)
  const [form] = Form.useForm<DNSRecordInput>()
  const filteredRows = useMemo(() => filterDNSRows(rows, search), [rows, search])

  const load = useCallback(async () => {
    setLoading(true)
    setSelectedKeys([])
    try {
      const result = await api.listDNSRecords()
      setRows(result.records)
      setInstanceNames(result.instance_names)
      setBulkInstances(result.instance_names)
      setErrors(result.errors)
      setCurrentPage(1)
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to load DNS records')
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    api.listDNSRecords()
      .then((result) => {
        setRows(result.records)
        setInstanceNames(result.instance_names)
        setBulkInstances(result.instance_names)
        setErrors(result.errors)
        setCurrentPage(1)
      })
      .catch((error) => message.error(error instanceof Error ? error.message : 'Unable to load DNS records'))
      .finally(() => setLoading(false))
  }, [message])

  const showAdd = () => {
    setEditing(undefined)
    form.resetFields()
    setOpen(true)
  }

  const showEdit = (instance: string, record: DNSRecord) => {
    const value = { instance, hostname: record.hostname, address: record.address }
    setEditing(value)
    form.setFieldsValue(value)
    setOpen(true)
  }

  const showCopy = (instance: string, row: DNSRow) => {
    const source = Object.values(row.instances).flat()[0]
    if (!source) return
    setEditing(undefined)
    form.setFieldsValue({ instance, hostname: row.hostname, address: source.address })
    setOpen(true)
  }

  const save = async () => {
    const values = await form.validateFields()
    const confirmed = await modal.confirm({
      title: editing ? `Update ${editing.hostname} on ${editing.instance}?` : `Add ${values.hostname} to ${values.instance}?`,
      content: editing
        ? `${editing.address} → ${values.address}${editing.hostname !== values.hostname ? `; ${editing.hostname} → ${values.hostname}` : ''}`
        : `New record: ${values.address} ${values.hostname}`,
      okText: editing ? 'Apply change' : 'Add record',
    })
    if (!confirmed) return
    setSaving(true)
    try {
      if (editing) {
        await api.updateDNSRecord({ ...values, old_hostname: editing.hostname, old_address: editing.address })
      } else {
        await api.addDNSRecord(values)
      }
      message.success(`${values.hostname} ${editing ? 'updated' : 'added'} on ${values.instance}`)
      setOpen(false)
      setEditing(undefined)
      form.resetFields()
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to save DNS record')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (instance: string, record: DNSRecord) => {
    try {
      await api.deleteDNSRecord({ instance, hostname: record.hostname, address: record.address })
      message.success(`${record.hostname} removed from ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to remove DNS record')
    }
  }

  const applyBulk = async (action: BulkAction) => {
    const targets = bulkInstances.map((instance) => ({
      instance,
      records: rows.filter((row) => selectedKeys.includes(row.hostname)).flatMap((row) => (row.instances[instance] ?? []).map((record) => ({ hostname: record.hostname, address: record.address }))),
    })).filter((target) => target.records.length > 0)
    if (!targets.length) {
      message.warning('No selected records exist on the target instances')
      return
    }
    const confirmed = await modal.confirm({
      title: 'Remove selected DNS records?',
      okText: 'Apply changes',
      okButtonProps: { danger: action === 'delete' },
      content: (
        <div>
          {action === 'delete' ? <Typography.Paragraph type="warning">Every record listed below will be removed.</Typography.Paragraph> : null}
          <Typography.Paragraph>Changes apply sequentially, one instance at a time.</Typography.Paragraph>
          {targets.map((target) => (
            <div key={target.instance}>
              <Typography.Text strong>{target.instance}: {target.records.length} records</Typography.Text>
              <List size="small" dataSource={target.records} renderItem={(item) => <List.Item>{`${item.address} ${item.hostname}`}</List.Item>} />
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
          for (let offset = 0; offset < target.records.length; offset += 200) {
            const response = await api.bulkDNSRecords(target.instance, target.records.slice(offset, offset + 200))
            results.push(...response.results.map((result) => ({ ...result, instance: target.instance })))
          }
        } catch (error) {
          // Stop this instance's chunks after a throw; a rollback failure forbids more writes.
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

  const columns: TableProps<DNSRow>['columns'] = [
    { title: 'Hostname', dataIndex: 'hostname', fixed: 'left', render: (value) => <Typography.Text strong>{value}</Typography.Text> },
    ...instanceNames.map((instance) => ({
      title: instance,
      key: instance,
      render: (_: unknown, row: DNSRow) => {
        const records = row.instances[instance] ?? []
        if (!records.length) return errors[instance]
          ? <Tag color="error">Unavailable</Tag>
          : <Button size="small" type="dashed" onClick={() => showCopy(instance, row)}>Copy here</Button>
        return <Space direction="vertical" size={4}>{records.map((record) => (
          <Space key={record.raw}>
            <Typography.Text code>{record.address}</Typography.Text>
            <Button size="small" type="text" icon={<EditOutlined />} aria-label={`Edit ${record.hostname} on ${instance}`} onClick={() => showEdit(instance, record)} />
            <Popconfirm
              title={`Delete ${record.hostname} from ${instance}?`}
              description={`${record.address} ${record.hostname} will be removed.`}
              okText="Delete record"
              okButtonProps={{ danger: true }}
              onConfirm={() => remove(instance, record)}
            >
              <Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={`Delete ${record.hostname} from ${instance}`} />
            </Popconfirm>
          </Space>
        ))}</Space>
      },
    })),
    { title: 'State', dataIndex: 'state', fixed: 'right', render: (value: DNSRow['state']) => stateTag[value] },
  ]

  return (
    <>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={() => void load()}>Refresh</Button>
          <Button type="primary" icon={<PlusOutlined />} disabled={!instanceNames.length} onClick={showAdd}>Add record</Button>
        </Space>
      {Object.entries(errors).map(([instance, error]) => (
        <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />
      ))}
      <div className="dns-table-tools">
        <Input
          className="dns-search"
          allowClear
          prefix={<SearchOutlined />}
          placeholder="Search hostname, IP address, or instance"
          aria-label="Search DNS records"
          value={search}
          onChange={(event) => {
            setSearch(event.target.value)
            setSelectedKeys([])
            setCurrentPage(1)
          }}
        />
        {search.trim() ? (
          <Typography.Text type="secondary">
            Showing {filteredRows.length} of {rows.length} records
          </Typography.Text>
        ) : null}
      </div>
      <BulkActionBar
        selectedCount={selectedKeys.length}
        instanceNames={instanceNames}
        selectedInstances={bulkInstances}
        onInstancesChange={setBulkInstances}
        actions={[{ key: 'delete', label: 'Remove', danger: true }]}
        onAction={(action) => void applyBulk(action)}
        onClear={() => setSelectedKeys([])}
        disabled={working}
      />
      <Table
        rowSelection={{ selectedRowKeys: selectedKeys, onChange: (keys) => setSelectedKeys(keys.map(String)), getCheckboxProps: () => ({ disabled: working }) }}
        rowKey="hostname"
        columns={columns}
        dataSource={filteredRows}
        loading={loading}
        pagination={{ current: currentPage, pageSize: 25, hideOnSinglePage: true, onChange: (page) => { setCurrentPage(page); setSelectedKeys([]) } }}
        scroll={{ x: 'max-content' }}
        locale={{
          emptyText: <Empty description={search.trim() ? `No DNS records match “${search.trim()}”` : 'No local DNS records found'} />,
        }}
      />
      <Modal
        title={editing ? `Edit record on ${editing.instance}` : 'Add local DNS record'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => void save()}
        confirmLoading={saving}
        okText="Review change"
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="instance" label="Instance" rules={[{ required: true }]}>
            <Select disabled={Boolean(editing)} options={instanceNames.map((name) => ({ value: name, label: name }))} />
          </Form.Item>
          <Form.Item name="hostname" label="Hostname" rules={[{ required: true }]}>
            <Input placeholder="server.lan" />
          </Form.Item>
          <Form.Item name="address" label="IP address" rules={[{ required: true }]}>
            <Input placeholder="192.168.1.10" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
