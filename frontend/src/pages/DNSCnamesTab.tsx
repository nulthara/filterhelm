import { useEffect, useState } from 'react'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, App, Button, Empty, Form, Input, InputNumber, List, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import BulkActionBar from './BulkActionBar'
import { api, type BulkResult, type CNAMERecord, type CNAMERecordInput, type CNAMERow } from '../api'
import { stateTag } from './DNSHostsTab'

type EditingRecord = CNAMERecordInput

function filterCNAMERows(rows: CNAMERow[], search: string): CNAMERow[] {
  const query = search.trim().toLowerCase()
  if (!query) return rows
  return rows.filter((row) => {
    if (row.hostname.toLowerCase().includes(query)) return true
    return Object.entries(row.instances).some(([instance, records]) =>
      instance.toLowerCase().includes(query)
      || records.some((record) => record.target.toLowerCase().includes(query)),
    )
  })
}

export default function DNSCnamesTab() {
  const { message, modal } = App.useApp()
  const [rows, setRows] = useState<CNAMERow[]>([])
  const [instanceNames, setInstanceNames] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<EditingRecord>()
  const [saving, setSaving] = useState(false)
  const [search, setSearch] = useState('')
  const [currentPage, setCurrentPage] = useState(1)
  const [selectedKeys, setSelectedKeys] = useState<string[]>([])
  const [bulkInstances, setBulkInstances] = useState<string[]>([])
  const [working, setWorking] = useState(false)
  const [form] = Form.useForm<CNAMERecordInput>()
  const filteredRows = filterCNAMERows(rows, search)

  const load = async () => {
    setSelectedKeys([])
    setLoading(true)
    try {
      const result = await api.listDNSCnames()
      setRows(result.records)
      setInstanceNames(result.instance_names)
      setBulkInstances(result.instance_names)
      setErrors(result.errors)
      setCurrentPage(1)
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to load CNAME records')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let active = true
    api.listDNSCnames()
      .then((result) => {
        if (!active) return
        setRows(result.records)
        setInstanceNames(result.instance_names)
        setBulkInstances(result.instance_names)
        setErrors(result.errors)
        setCurrentPage(1)
      })
      .catch((error) => {
        if (active) message.error(error instanceof Error ? error.message : 'Unable to load CNAME records')
      })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [message])

  const showAdd = () => {
    setEditing(undefined)
    form.resetFields()
    setOpen(true)
  }

  const showEdit = (instance: string, record: CNAMERecord) => {
    const value = { instance, hostname: record.hostname, target: record.target, ttl: record.ttl }
    setEditing(value)
    form.setFieldsValue(value)
    setOpen(true)
  }

  const showCopy = (instance: string, row: CNAMERow) => {
    const source = Object.values(row.instances).flat()[0]
    if (!source) return
    setEditing(undefined)
    form.setFieldsValue({ instance, hostname: row.hostname, target: source.target, ttl: source.ttl })
    setOpen(true)
  }

  const save = async () => {
    const values = await form.validateFields()
    const confirmed = await modal.confirm({
      title: editing ? `Update ${editing.hostname} on ${editing.instance}?` : `Add ${values.hostname} to ${values.instance}?`,
      content: editing
        ? `${editing.target} → ${values.target}; TTL: ${editing.ttl ?? 'default'} → ${values.ttl ?? 'default'}${editing.hostname !== values.hostname ? `; ${editing.hostname} → ${values.hostname}` : ''}`
        : `New record: ${values.hostname} → ${values.target}; TTL: ${values.ttl ?? 'default'}`,
      okText: editing ? 'Apply change' : 'Add record',
    })
    if (!confirmed) return
    setSaving(true)
    try {
      if (editing) {
        await api.updateDNSCname({ ...values, old_hostname: editing.hostname, old_target: editing.target, old_ttl: editing.ttl })
      } else {
        await api.addDNSCname(values)
      }
      message.success(`${values.hostname} ${editing ? 'updated' : 'added'} on ${values.instance}`)
      setOpen(false)
      setEditing(undefined)
      form.resetFields()
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to save CNAME record')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (instance: string, record: CNAMERecord) => {
    try {
      await api.deleteDNSCname({ instance, hostname: record.hostname, target: record.target, ttl: record.ttl })
      message.success(`${record.hostname} removed from ${instance}`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to remove CNAME record')
    }
  }

  const applyBulk = async () => {
    const targets = bulkInstances.map((instance) => ({
      instance,
      records: rows
        .filter((row) => selectedKeys.includes(row.hostname))
        .flatMap((row) => (row.instances[instance] ?? []).map((r) => ({ hostname: r.hostname, target: r.target }))),
    })).filter((target) => target.records.length > 0)
    if (!targets.length) {
      message.warning('No selected records exist on the target instances')
      return
    }
    const confirmed = await modal.confirm({
      title: 'Remove selected CNAME records?',
      okText: 'Apply changes',
      okButtonProps: { danger: true },
      content: (
        <div>
          <Typography.Paragraph type="warning">Every alias listed below will be removed.</Typography.Paragraph>
          <Typography.Paragraph>Changes apply sequentially, one instance at a time.</Typography.Paragraph>
          {targets.map((target) => (
            <div key={target.instance}>
              <Typography.Text strong>{target.instance}: {target.records.length} records</Typography.Text>
              <List size="small" dataSource={target.records} renderItem={(item) => <List.Item>{`${item.hostname} → ${item.target}`}</List.Item>} />
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
          const response = await api.bulkDNSCnames(target.instance, target.records)
          results.push(...response.results.map((r) => ({ ...r, instance: target.instance })))
        } catch (error) {
          instanceFailures.push(`${target.instance}: ${error instanceof Error ? error.message : 'Unable to apply bulk action'}`)
        }
      }
      const changed = results.filter((r) => r.changed).length
      const unchanged = results.filter((r) => r.success && !r.changed).length
      const failures = [
        ...results.filter((r) => !r.success).map((r) => `${r.instance} / ${r.id}: ${r.error}`),
        ...instanceFailures,
      ]
      const summary = `${selectedKeys.length} selected, ${changed} changed${unchanged ? `, ${unchanged} already removed` : ''}`
      if (failures.length) message.warning(`${summary}; failed: ${failures.join('; ')}`, 10)
      else message.success(summary)
      setSelectedKeys([])
      await load()
    } finally {
      setWorking(false)
    }
  }

  const columns: TableProps<CNAMERow>['columns'] = [
    { title: 'Alias', dataIndex: 'hostname', fixed: 'left', render: (value) => <Typography.Text strong>{value}</Typography.Text> },
    ...instanceNames.map((instance) => ({
      title: instance,
      key: instance,
      render: (_: unknown, row: CNAMERow) => {
        const records = row.instances[instance] ?? []
        if (!records.length) return errors[instance]
          ? <Tag color="error">Unavailable</Tag>
          : <Button size="small" type="dashed" onClick={() => showCopy(instance, row)}>Copy here</Button>
        return <Space direction="vertical" size={4}>{records.map((record) => (
          <Space key={record.raw}>
            <Typography.Text code>{record.target}</Typography.Text>
            {record.ttl != null ? <Typography.Text type="secondary">TTL: {record.ttl}</Typography.Text> : null}
            <Button size="small" type="text" icon={<EditOutlined />} aria-label={`Edit ${record.hostname} on ${instance}`} onClick={() => showEdit(instance, record)} />
            <Popconfirm
              title={`Delete ${record.hostname} from ${instance}?`}
              description={`${record.target} ${record.hostname} will be removed.`}
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
    { title: 'State', dataIndex: 'state', fixed: 'right', render: (value: CNAMERow['state']) => stateTag[value] },
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
          placeholder="Search alias, target, or instance"
          aria-label="Search CNAME records"
          value={search}
          onChange={(event) => {
            setSelectedKeys([])
            setSearch(event.target.value)
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
        onAction={() => void applyBulk()}
        onClear={() => setSelectedKeys([])}
        disabled={working}
      />
      <Table
        rowKey="hostname"
        rowSelection={{ selectedRowKeys: selectedKeys, onChange: (keys) => setSelectedKeys(keys.map(String)), getCheckboxProps: () => ({ disabled: working }) }}
        columns={columns}
        dataSource={filteredRows}
        loading={loading}
        pagination={{ current: currentPage, pageSize: 25, hideOnSinglePage: true, onChange: (page) => { setCurrentPage(page); setSelectedKeys([]) } }}
        scroll={{ x: 'max-content' }}
        locale={{
          emptyText: <Empty description={search.trim() ? `No CNAME records match “${search.trim()}”` : 'No local CNAME records found'} />,
        }}
      />
      <Modal
        title={editing ? `Edit record on ${editing.instance}` : 'Add local CNAME record'}
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
          <Form.Item name="hostname" label="Alias" rules={[{ required: true }]}>
            <Input placeholder="alias.lan" />
          </Form.Item>
          <Form.Item name="target" label="Target" rules={[{ required: true }]}>
            <Input placeholder="server.lan" />
          </Form.Item>
          <Form.Item name="ttl" label="TTL" extra="Leave blank to use the Pi-hole default">
            <InputNumber min={0} max={604800} precision={0} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
