import { useCallback, useEffect, useState } from 'react'
import { DeleteOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import { Alert, App, Button, Checkbox, Empty, Form, Input, List, Modal, Radio, Select, Skeleton, Space, Switch, Table, Tag, Tooltip, Typography } from 'antd'
import type { TableProps } from 'antd'
import { api, type GroupOrphans, type GroupRow, type OrphanAction } from '../api'

type AddValues = { instances: string[]; name: string; comment: string }
const stateTag = {
  consistent: <Tag color="success">Consistent</Tag>,
  missing: <Tag color="warning">Missing</Tag>,
  conflict: <Tag color="error">Conflict</Tag>,
}

export default function GroupsPage() {
  const { message, modal } = App.useApp()
  const [rows, setRows] = useState<GroupRow[]>([])
  const [instanceNames, setInstanceNames] = useState<string[]>([])
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [open, setOpen] = useState(false)
  const [deleting, setDeleting] = useState<{ instance: string; row: GroupRow } | null>(null)
  const [preview, setPreview] = useState<GroupOrphans | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [action, setAction] = useState<OrphanAction>('reassign')
  const [acknowledgedDefaultRisk, setAcknowledgedDefaultRisk] = useState(false)
  const deletingDefault = deleting?.row.instances[deleting.instance]?.id === 0
  useEffect(() => {
    if (!deleting) return
    const controller = new AbortController()
    api.groupOrphans(deleting.instance, deleting.row.name, controller.signal)
      .then((result) => { if (!controller.signal.aborted) setPreview(result) })
      .catch((error) => {
        if (!controller.signal.aborted) setPreviewError(error instanceof Error ? error.message : 'Unable to load group orphans')
      })
    return () => controller.abort()
  }, [deleting])
  const [form] = Form.useForm<AddValues>()
  const applyResult = useCallback((result: Awaited<ReturnType<typeof api.listGroups>>) => {
    setRows(result.groups); setInstanceNames(result.instance_names); setErrors(result.errors)
  }, [])
  const load = useCallback(async () => {
    setLoading(true)
    try { applyResult(await api.listGroups()) } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to load groups') } finally { setLoading(false) }
  }, [applyResult, message])
  useEffect(() => { api.listGroups().then(applyResult).catch((error) => message.error(error instanceof Error ? error.message : 'Unable to load groups')).finally(() => setLoading(false)) }, [applyResult, message])
  const showAdd = (instance?: string, source?: GroupRow) => {
    form.resetFields()
    form.setFieldsValue({ instances: instance ? [instance] : [], name: source?.name ?? '', comment: source ? Object.values(source.instances).find((item) => item.comment)?.comment ?? '' : '' })
    setOpen(true)
  }
  const add = async () => {
    const values = await form.validateFields()
    if (!await modal.confirm({ title: `Add group to ${values.instances.length} instance${values.instances.length === 1 ? '' : 's'}?`, content: values.name, okText: 'Add group' })) return
    setWorking(true)
    try {
      const { results } = await api.addGroup(values.instances, values.name, values.comment ?? '')
      const failed = results.filter((result) => !result.success)
      if (failed.length) message.warning(`${results.length - failed.length} succeeded; ${failed.length} failed: ${failed.map((item) => item.name).join(', ')}`)
      else message.success(`Group added to ${results.length} instance${results.length === 1 ? '' : 's'}`)
      setOpen(false); await load()
    } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to add group') } finally { setWorking(false) }
  }
  const toggle = async (instance: string, row: GroupRow, enabled: boolean) => {
    if (!await modal.confirm({ title: `${enabled ? 'Enable' : 'Disable'} this group on ${instance}?`, content: row.name, okText: enabled ? 'Enable group' : 'Disable group' })) return
    setWorking(true)
    try { await api.updateGroup(instance, row.name, enabled); message.success(`Group ${enabled ? 'enabled' : 'disabled'} on ${instance}`); await load() } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to update group') } finally { setWorking(false) }
  }
  const showDelete = (instance: string, row: GroupRow) => {
    setPreview(null)
    setPreviewError(null)
    setAction(row.instances[instance]?.id === 0 ? 'ignore' : 'reassign')
    setAcknowledgedDefaultRisk(false)
    setDeleting({ instance, row })
  }
  const closeDelete = () => {
    setDeleting(null)
    setAcknowledgedDefaultRisk(false)
  }
  const remove = async () => {
    if (!deleting || !preview || previewError || (deletingDefault && !acknowledgedDefaultRisk)) return
    const { instance, row } = deleting
    setWorking(true)
    try {
      const result = await api.deleteGroup(instance, row.name, action, deletingDefault && acknowledgedDefaultRisk)
      const failed = result.orphans.filter((item) => !item.success)
      const handled = result.orphans.filter((item) => item.success).map((item) => item.id)
      const summary = handled.length ? ` Handled: ${handled.join(', ')}.` : ''
      if (!result.deleted || failed.length) {
        message.warning(`Group ${result.deleted ? 'removed' : 'kept'} on ${instance}. Failed: ${failed.map((item) => `${item.id}: ${item.error}`).join(', ') || 'none'}.${summary}`)
      } else {
        message.success(`Group removed from ${instance}.${summary}`)
      }
      closeDelete()
    } catch (error) { message.error(error instanceof Error ? error.message : 'Unable to remove group') }
    finally { await load(); setWorking(false) }
  }
  const hasOrphans = !!preview && (preview.adlists.length > 0 || preview.domains.length > 0)
  const deleteText = !hasOrphans || action === 'ignore' ? 'Delete group' : action === 'delete' ? 'Delete entries and group' : 'Move entries and delete group'
  const deleteModal = <Modal
    title={deleting ? `Remove "${deleting.row.name}" from ${deleting.instance}?` : 'Remove group'}
    open={!!deleting}
    onCancel={() => { if (!working) closeDelete() }}
    onOk={() => void remove()}
    confirmLoading={working}
    okText={deleteText}
    okButtonProps={{ danger: action === 'delete' || deletingDefault, disabled: !preview || !!previewError || (deletingDefault && !acknowledgedDefaultRisk) }}
    cancelButtonProps={{ disabled: working }}
  >
    <Space direction="vertical" style={{ width: '100%' }}>
      {!preview && !previewError && <Skeleton active />}
      {previewError && <Alert type="error" showIcon message={previewError} />}
      {preview && !hasOrphans && <Typography.Text>No block lists or domains are scoped only to this group.</Typography.Text>}
      {preview && hasOrphans && <>
        <Alert type="warning" showIcon message={`${preview.adlists.length} block lists and ${preview.domains.length} domains will be left with no group.`} />
        <List size="small">
          {preview.adlists.map((item) => <List.Item key={`adlist:${item.address}`}>{item.address}</List.Item>)}
          {preview.domains.map((item) => <List.Item key={`domain:${item.type}:${item.kind}:${item.domain}`}><Space wrap><Typography.Text>{item.domain}</Typography.Text><Tag>{item.type === 'allow' ? 'Allow' : 'Deny'}</Tag><Tag>{item.kind === 'exact' ? 'Exact' : 'Regex'}</Tag></Space></List.Item>)}
        </List>
        <Radio.Group value={action} onChange={(event) => setAction(event.target.value)} disabled={working}>
          <Space direction="vertical">
            <Radio value="ignore">Delete the group only (these entries will be left with no group)</Radio>
            {!deletingDefault && <Radio value="reassign">Move them to the Default group, then delete the group</Radio>}
            <Radio value="delete">Delete these entries too, then delete the group</Radio>
          </Space>
        </Radio.Group>
      </>}
      {deletingDefault && <>
        <Alert type="error" showIcon message="Deleting the Default group can leave this Pi-hole instance in an inconsistent state. You may be able to recover it by recreating the group, naming it 'Default', and reassociating it with all hosts and rules. This is not guaranteed to work, and you may end up needing to reinstall." />
        <Checkbox checked={acknowledgedDefaultRisk} disabled={working} onChange={(event) => setAcknowledgedDefaultRisk(event.target.checked)}>I understand this may corrupt this Pi-hole instance and could require reinstalling it.</Checkbox>
      </>}
    </Space>
  </Modal>
  const columns: TableProps<GroupRow>['columns'] = [
    { title: 'Name', dataIndex: 'name', fixed: 'left', render: (name, row) => {
      const comment = Object.values(row.instances).find((item) => item.comment)?.comment
      return <Space direction="vertical" size={0}><Typography.Text>{name}</Typography.Text>{comment ? <Typography.Text type="secondary">{comment}</Typography.Text> : null}</Space>
    } },
    ...instanceNames.map((instance) => ({ title: instance, key: instance, render: (_: unknown, row: GroupRow) => {
      const item = row.instances[instance]
      if (!item) return errors[instance] ? <Tag color="error">Unavailable</Tag> : <Button size="small" type="dashed" onClick={() => showAdd(instance, row)}>Copy here</Button>
      const toggleControl = <Switch size="small" checked={item.enabled} disabled={working} onChange={(enabled) => void toggle(instance, row, enabled)} />
      return <Space>{row.name === 'Default' ? <Tooltip title="Disabling the Default group disables every list and domain still assigned to it.">{toggleControl}</Tooltip> : toggleControl}<Typography.Text>{item.enabled ? 'Enabled' : 'Disabled'}</Typography.Text><Button size="small" type="text" danger disabled={working} icon={<DeleteOutlined />} aria-label={`Remove group from ${instance}`} onClick={() => showDelete(instance, row)} /></Space>
    } })),
    { title: 'State', dataIndex: 'state', fixed: 'right', render: (state: GroupRow['state']) => stateTag[state] },
  ]
  return <><div className="page-heading"><div><Typography.Title level={2}>Groups</Typography.Title><Typography.Paragraph type="secondary">Manage filtering groups across Pi-hole instances.</Typography.Paragraph></div><Space><Button icon={<ReloadOutlined />} onClick={() => void load()}>Refresh</Button><Button type="primary" icon={<PlusOutlined />} disabled={!instanceNames.length} onClick={() => showAdd()}>Add group</Button></Space></div>{Object.entries(errors).map(([instance, error]) => <Alert key={instance} className="dns-error" type="error" showIcon message={`${instance} unavailable`} description={error} />)}<Table rowKey="name" columns={columns} dataSource={rows} loading={loading} pagination={{ pageSize: 25, hideOnSinglePage: true }} scroll={{ x: 'max-content' }} locale={{ emptyText: <Empty description="No groups configured" /> }} /><Modal title="Add group" open={open} onCancel={() => setOpen(false)} onOk={() => void add()} confirmLoading={working} okText="Review and add" destroyOnHidden><Form form={form} layout="vertical"><Form.Item name="instances" label="Instances" rules={[{ required: true, message: 'Select at least one instance' }]}><Select mode="multiple" options={instanceNames.map((name) => ({ value: name, label: name }))} /></Form.Item><Form.Item name="name" label="Name" rules={[{ required: true }]}><Input /></Form.Item><Form.Item name="comment" label="Comment" initialValue=""><Input.TextArea rows={3} /></Form.Item></Form></Modal>{deleteModal}</>
}
