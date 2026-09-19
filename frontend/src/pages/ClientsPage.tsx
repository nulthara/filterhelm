import { useCallback, useEffect, useState } from 'react'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Empty, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography } from 'antd'
import type { TableProps } from 'antd'
import { api, type ClientRow, type GroupRow } from '../api'

type AddValues = { instances: string[]; client: string; comment: string }
const stateTag = { consistent: <Tag color="success">Consistent</Tag>, missing: <Tag color="warning">Missing</Tag> }

function groupNameMap(rows: GroupRow[]): Record<string, Record<number, string>> {
  const result: Record<string, Record<number, string>> = {}
  for (const row of rows) for (const [instance, group] of Object.entries(row.instances)) if (group.id != null) {
    result[instance] ??= {}; result[instance][group.id] = row.name
  }
  return result
}

export default function ClientsPage() {
  const { message, modal } = App.useApp()
  const [rows, setRows] = useState<ClientRow[]>([])
  const [groupRows, setGroupRows] = useState<GroupRow[]>([])
  const [instanceNames, setInstanceNames] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<{ instance: string; row: ClientRow; groups: number[] }>()
  const [form] = Form.useForm<AddValues>()
  const applyResult = useCallback(([clients, groups]: [Awaited<ReturnType<typeof api.listClients>>, Awaited<ReturnType<typeof api.listGroups>>]) => {
    setRows(clients.clients); setGroupRows(groups.groups); setInstanceNames(clients.instance_names); setErrors({ ...groups.errors, ...clients.errors })
  }, [])
  const load = useCallback(async () => {
    setLoading(true)
    // Sequential, not Promise.all: two concurrent Pi-hole logins against the same
    // instance (one from listClients' fanout, one from listGroups') intermittently
    // 401 — confirmed against a live instance.
    try { applyResult([await api.listClients(), await api.listGroups()]) } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to load clients') } finally { setLoading(false) }
  }, [applyResult, message])
  useEffect(() => { (async () => {
    try { applyResult([await api.listClients(), await api.listGroups()]) } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to load clients') } finally { setLoading(false) }
  })() }, [applyResult, message])
  const groupNamesByInstance = groupNameMap(groupRows)
  const showAdd = (instance?: string, source?: ClientRow) => {
    form.resetFields(); form.setFieldsValue({ instances: instance ? [instance] : [], client: source?.client ?? '', comment: source ? Object.values(source.instances).find((item) => item.comment)?.comment ?? '' : '' }); setOpen(true)
  }
  const add = async () => {
    const values = await form.validateFields()
    if (!await modal.confirm({ title: `Add client to ${values.instances.length} instance${values.instances.length === 1 ? '' : 's'}?`, content: values.client, okText: 'Add client' })) return
    setWorking(true)
    try { const { results } = await api.addClient(values.instances, values.client, values.comment ?? ''); const failed = results.filter((result) => !result.success); if (failed.length) message.warning(`${results.length - failed.length} succeeded; ${failed.length} failed: ${failed.map((item) => item.name).join(', ')}`); else message.success(`Client added to ${results.length} instance${results.length === 1 ? '' : 's'}`); setOpen(false); await load() } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to add client') } finally { setWorking(false) }
  }
  const editGroups = async () => {
    if (!editing) return
    setWorking(true)
    try { await api.updateClientGroups(editing.instance, editing.row.client, editing.groups); message.success(`Client groups updated on ${editing.instance}`); setEditing(undefined); await load() } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to update client groups') } finally { setWorking(false) }
  }
  const remove = async (instance: string, row: ClientRow) => {
    setWorking(true)
    try { await api.deleteClient(instance, row.client); message.success(`Client removed from ${instance}`); await load() } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to remove client') } finally { setWorking(false) }
  }
  const columns: TableProps<ClientRow>['columns'] = [
    { title: 'Client', dataIndex: 'client', fixed: 'left', render: (identifier, row) => { const comment = Object.values(row.instances).find((item) => item.comment)?.comment; return <Space direction="vertical" size={0}><Typography.Text copyable>{identifier}</Typography.Text>{comment ? <Typography.Text type="secondary">{comment}</Typography.Text> : null}</Space> } },
    ...instanceNames.map((instance) => ({ title: instance, key: instance, render: (_: unknown, row: ClientRow) => {
      const item = row.instances[instance]
      if (!item) return errors[instance] ? <Tag color="error">Unavailable</Tag> : <Button size="small" type="dashed" onClick={() => showAdd(instance, row)}>Copy here</Button>
      return <Space wrap>{item.groups.map((id) => <Tag key={id}>{groupNamesByInstance[instance]?.[id] ?? `#${id}`}</Tag>)}<Button size="small" onClick={() => setEditing({ instance, row, groups: [...item.groups] })}>Edit groups</Button><Popconfirm title={`Remove this client from ${instance}?`} okText="Remove client" okButtonProps={{ danger: true }} onConfirm={() => remove(instance, row)}><Button size="small" type="text" danger icon={<DeleteOutlined />} aria-label={`Remove client from ${instance}`} /></Popconfirm></Space>
    } })),
    { title: 'State', dataIndex: 'state', fixed: 'right', render: (state: ClientRow['state']) => stateTag[state] },
  ]
  const editOptions = editing ? Object.entries(groupNamesByInstance[editing.instance] ?? {}).map(([id, name]) => ({ value: Number(id), label: name })) : []
  return <><div className="page-heading"><div><Typography.Title level={2}>Clients</Typography.Title><Typography.Paragraph type="secondary">Manage clients and their filtering groups across Pi-hole instances.</Typography.Paragraph></div><Space><Button icon={<ReloadOutlined />} onClick={() => void load()}>Refresh</Button><Button type="primary" icon={<PlusOutlined />} disabled={!instanceNames.length} onClick={() => showAdd()}>Add client</Button></Space></div>{Object.entries(errors).map(([instance, error]) => <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />)}<Table rowKey="client" columns={columns} dataSource={rows} loading={loading} pagination={{ pageSize: 25, hideOnSinglePage: true }} scroll={{ x: 'max-content' }} locale={{ emptyText: <Empty description="No clients configured" /> }} /><Modal title={`Edit groups on ${editing?.instance ?? ''}`} open={Boolean(editing)} onCancel={() => setEditing(undefined)} onOk={() => void editGroups()} confirmLoading={working} okText="Save groups" destroyOnHidden><Select mode="multiple" style={{ width: '100%' }} options={editOptions} value={editing?.groups} onChange={(groups) => setEditing((current) => current ? { ...current, groups } : current)} /></Modal><Modal title="Add client" open={open} onCancel={() => setOpen(false)} onOk={() => void add()} confirmLoading={working} okText="Review and add" destroyOnHidden><Form form={form} layout="vertical"><Form.Item name="instances" label="Instances" rules={[{ required: true, message: 'Select at least one instance' }]}><Select mode="multiple" options={instanceNames.map((name) => ({ value: name, label: name }))} /></Form.Item><Form.Item name="client" label="Client" rules={[{ required: true }]}><Input placeholder="192.168.1.50, AA:BB:CC:DD:EE:FF, hostname, or :eth0" /></Form.Item><Form.Item name="comment" label="Comment" initialValue=""><Input.TextArea rows={3} /></Form.Item></Form></Modal></>
}
