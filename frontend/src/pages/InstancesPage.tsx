import { useCallback, useEffect, useState } from 'react'
import { DeleteOutlined, DownOutlined, DownloadOutlined, EditOutlined, PlusOutlined, ReloadOutlined, ToolOutlined } from '@ant-design/icons'
import {
  Alert, App, Badge, Button, Descriptions, Dropdown, Empty, Flex, Form, Grid, Input, Modal, Popconfirm, Space, Switch, Table, Tag, Tooltip, Typography,
} from 'antd'
import type { DescriptionsProps, TableProps } from 'antd'
import { api, type Download, type FtlAction, type HealthStatus, type Instance, type InstanceHealth, type InstanceInput } from '../api'

function saveDownload({ blob, filename }: Download): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

const ftlActions: Record<FtlAction, {
  label: string
  title: (name: string) => string
  content: (name: string) => string
  okText: string
  danger: boolean
  done: (name: string) => string
}> = {
  restart_dns: {
    label: 'Restart DNS',
    title: (name) => `Restart DNS on ${name}?`,
    content: (name) => `FTL restarts and ${name} stops answering DNS queries for a few seconds. Clients that use only this resolver will see failed lookups until it is back.`,
    okText: 'Restart DNS',
    danger: false,
    done: (name) => `DNS restart requested on ${name}`,
  },
  flush_logs: {
    label: 'Flush logs',
    title: (name) => `Flush logs on ${name}?`,
    content: (name) => `This empties pihole.log and deletes the last 24 hours of query history from ${name}'s memory and database. The Queries page and 24-hour counters for ${name} start again from zero. This cannot be undone.`,
    okText: 'Flush logs',
    danger: true,
    done: (name) => `Logs flushed on ${name}`,
  },
  flush_network: {
    label: 'Flush network table',
    title: (name) => `Flush the network table on ${name}?`,
    content: (name) => `This clears ${name}'s list of seen devices (MAC, IP, hostname). Pi-hole rebuilds it as devices make queries. Client and group assignments are kept. This cannot be undone.`,
    okText: 'Flush network table',
    danger: true,
    done: (name) => `Network table flushed on ${name}`,
  },
}

const ftlActionItems = Object.entries(ftlActions).map(([key, { label, danger }]) => ({ key, label, danger }))

const statusColor = { success: 'success', warning: 'warning', error: 'error' } as const
const compatibilityColor = { compatible: 'success', incompatible: 'error', untested: 'warning', unknown: 'default' } as const

const healthBadge: Record<HealthStatus, 'success' | 'warning' | 'error'> =
  { healthy: 'success', warning: 'warning', critical: 'error' }
const healthLabel: Record<HealthStatus, string> =
  { healthy: 'Healthy', warning: 'Warning', critical: 'Critical' }

function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor(seconds / 3600) % 24
  const minutes = Math.floor(seconds / 60) % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

function formatBytes(bytes: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`
}

interface HealthBadgeProps {
  health: InstanceHealth | null
}

function HealthBadge({ health }: HealthBadgeProps) {
  if (health === null) return <Badge status="default" text="Unavailable" />
  return (
    <Tooltip title={health.reasons.length ? health.reasons.join('; ') : undefined}>
      <Badge status={healthBadge[health.status]} text={healthLabel[health.status]} />
    </Tooltip>
  )
}

interface HealthDetailsProps {
  health: InstanceHealth
}

function HealthDetails({ health }: HealthDetailsProps) {
  const { host_uptime, ftl_uptime, memory, swap, cpu, temperature, ftl, database } = health
  const { load, cores } = cpu
  const temperatureLimit = temperature?.limit == null
    ? 'no limit configured' : `limit ${temperature.limit.toFixed(0)} °${temperature.unit}`
  const temperatureDisplay = temperature === null
    ? 'Unavailable' : `${temperature.value.toFixed(0)} °${temperature.unit} (${temperatureLimit})`
  const items: DescriptionsProps['items'] = [
    { key: 'host_uptime', label: 'Host uptime', children: formatDuration(host_uptime) },
    { key: 'ftl_uptime', label: 'FTL uptime', children: formatDuration(ftl_uptime) },
    { key: 'load', label: 'Load', children: `${load.map((value) => value.toFixed(2)).join(' / ')} (${cores} cores)` },
    { key: 'cpu', label: 'CPU', children: `${cpu.percent.toFixed(1)}%` },
    { key: 'ram', label: 'RAM', children: `${memory.percent.toFixed(1)}% (${formatBytes(memory.used * 1024)} / ${formatBytes(memory.total * 1024)})` },
    { key: 'ram_available', label: 'RAM available', children: formatBytes(memory.available * 1024) },
    { key: 'swap', label: 'Swap', children: `${swap.percent.toFixed(1)}% (${formatBytes(swap.used * 1024)} / ${formatBytes(swap.total * 1024)})` },
    { key: 'temperature', label: 'Temperature', children: temperatureDisplay },
    { key: 'ftl_cpu', label: 'FTL CPU', children: `${ftl.cpu_percent.toFixed(1)}%` },
    { key: 'ftl_memory', label: 'FTL memory', children: `${ftl.mem_percent.toFixed(1)}%` },
    { key: 'database', label: 'Database', children: `${formatBytes(database.size)} · ${database.queries.toLocaleString()} queries` },
    { key: 'sqlite', label: 'SQLite', children: database.sqlite_version },
  ]
  return (
    <Flex vertical gap="middle" className="health-details">
      {health.reasons.length > 0 && (
        <Alert type={health.status === 'critical' ? 'error' : 'warning'} showIcon
          message={health.reasons.join('; ')} />
      )}
      <Descriptions size="small" column={{ xs: 1, sm: 2, xl: 4 }} items={items} />
    </Flex>
  )
}

interface InstancesPageProps {
  initialAddOpen?: boolean
}

export default function InstancesPage({ initialAddOpen = false }: InstancesPageProps) {
  const { message, modal } = App.useApp()
  const { md } = Grid.useBreakpoint()
  const compact = md === false
  const [instances, setInstances] = useState<Instance[]>([])
  const [expandOverrides, setExpandOverrides] = useState<Map<string, boolean>>(new Map())
  const [loading, setLoading] = useState(true)
  const [modalOpen, setModalOpen] = useState(initialAddOpen)
  const [editing, setEditing] = useState<Instance>()
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState<string>()
  const [backingUp, setBackingUp] = useState<string>()
  const [actionRunning, setActionRunning] = useState<string>()
  const [form] = Form.useForm<InstanceInput>()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setInstances((await api.listInstances({ health: true })).instances)
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to load instances')
    } finally {
      setLoading(false)
    }
  }, [message])

  useEffect(() => {
    api.listInstances({ health: true })
      .then(({ instances: result }) => setInstances(result))
      .catch((error) => message.error(error instanceof Error ? error.message : 'Unable to load instances'))
      .finally(() => setLoading(false))
  }, [message])

  const save = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      if (editing) {
        await api.updateInstance(editing.name, {
          base_url: values.base_url,
          notes: values.notes,
          verify_ssl: values.verify_ssl,
          ...(values.app_password ? { app_password: values.app_password } : {}),
        })
      } else {
        await api.saveInstance(values)
      }
      message.success(`${values.name} saved`)
      setModalOpen(false)
      setEditing(undefined)
      form.resetFields()
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to save instance')
    } finally {
      setSaving(false)
    }
  }

  const openAdd = () => {
    setEditing(undefined)
    form.resetFields()
    form.setFieldValue('notes', '')
    form.setFieldValue('verify_ssl', true)
    setModalOpen(true)
  }

  const openEdit = (instance: Instance) => {
    setEditing(instance)
    form.setFieldsValue({
      name: instance.name,
      base_url: instance.base_url,
      app_password: '',
      notes: instance.notes,
      verify_ssl: instance.verify_ssl,
    })
    setModalOpen(true)
  }

  const closeModal = () => {
    setModalOpen(false)
    setEditing(undefined)
    form.resetFields()
  }

  const test = async (name: string) => {
    setTesting(name)
    try {
      const result = await api.testInstance(name)
      if (result.status === 'success') message.success(result.detail)
      else message.error(result.detail)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Connection test failed')
    } finally {
      setTesting(undefined)
    }
  }

  const backup = async (name: string) => {
    setBackingUp(name)
    try {
      const download = await api.downloadBackup(name)
      saveDownload(download)
      message.success(`${download.filename} downloaded`)
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Backup failed')
    } finally {
      setBackingUp(undefined)
    }
  }

  const runAction = async (name: string, action: FtlAction) => {
    const { title, content, okText, danger, done } = ftlActions[action]
    const confirmed = await modal.confirm({
      title: title(name),
      content: content(name),
      okText,
      okButtonProps: danger ? { danger: true } : undefined,
    })
    if (!confirmed) return
    setActionRunning(name)
    try {
      await api.runFtlAction(name, action)
      message.success(done(name))
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Action failed')
    } finally {
      setActionRunning(undefined)
    }
  }

  const remove = async (name: string) => {
    try {
      await api.deleteInstance(name)
      message.success(`${name} removed locally`)
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to remove instance')
    }
  }

  const isExpanded = (item: Instance) => item.health !== null &&
    (expandOverrides.get(item.name) ?? item.health.status !== 'healthy')
  const expandedRowKeys = instances.filter(isExpanded).map((item) => item.name)

  const columns: TableProps<Instance>['columns'] = [
    { title: 'Name', dataIndex: 'name', key: 'name', render: (value) => <Typography.Text strong>{value}</Typography.Text> },
    { title: 'Base URL', dataIndex: 'base_url', key: 'base_url', render: (value) => <Typography.Text copyable>{value}</Typography.Text> },
    {
      title: 'Status', key: 'status', render: (_, item) => (
        <Tooltip title={item.status_detail}>
          <Tag color={statusColor[item.status]}>{item.status_detail}</Tag>
        </Tooltip>
      ),
    },
    {
      title: 'Versions', key: 'versions', render: (_, item) => item.versions ? (
        <Space direction="vertical" size={2}>
          <Space wrap size={4}>
            <Typography.Text>Core {item.versions.core.local ?? 'unknown'}</Typography.Text>
            <Typography.Text>Web {item.versions.web.local ?? 'unknown'}</Typography.Text>
            <Typography.Text>FTL {item.versions.ftl.local ?? 'unknown'}</Typography.Text>
            {item.versions.docker.local ? <Typography.Text>Docker {item.versions.docker.local}</Typography.Text> : null}
          </Space>
          <Space wrap size={4}>
            <Tooltip title={item.versions.compatibility_detail}>
              <Tag color={compatibilityColor[item.versions.compatibility]}>{item.versions.compatibility}</Tag>
            </Tooltip>
            {item.versions.update_available ? (
              <Tooltip title={['core', 'web', 'ftl', 'docker'].flatMap((name) => {
                const component = item.versions?.[name as 'core' | 'web' | 'ftl' | 'docker']
                return component?.update_available ? [`${name}: ${component.local} → ${component.remote}`] : []
              }).join('; ')}>
                <Tag color="warning">Update available</Tag>
              </Tooltip>
            ) : null}
          </Space>
        </Space>
      ) : <Typography.Text type="secondary">Unavailable</Typography.Text>,
    },
    Table.EXPAND_COLUMN,
    { title: 'Health', key: 'health', render: (_, item) => <HealthBadge health={item.health} /> },
    { title: 'Notes', dataIndex: 'notes', key: 'notes', responsive: ['lg'] },
    {
      title: 'TLS', key: 'tls', responsive: ['lg'], render: (_, item) => (
        item.verify_ssl ? <Tag color="success">Verified</Tag> : <Tag color="warning">Verification off</Tag>
      ),
    },
    {
      title: 'Actions', key: 'actions', align: 'right', render: (_, item) => (
        <Space size={compact ? 4 : 8}>
          {compact ? (
            <Tooltip title="Test">
              <Button icon={<ReloadOutlined />} aria-label={`Test ${item.name}`} loading={testing === item.name} onClick={() => void test(item.name)} />
            </Tooltip>
          ) : <Button icon={<ReloadOutlined />} loading={testing === item.name} onClick={() => void test(item.name)}>Test</Button>}
          <Tooltip title="Download a Teleporter archive of this Pi-hole's configuration. It may include password hashes, so store it privately.">
            <Button icon={<DownloadOutlined />} aria-label={`Backup ${item.name}`} loading={backingUp === item.name} onClick={() => void backup(item.name)}>{compact ? null : 'Backup'}</Button>
          </Tooltip>
          <Tooltip title={compact ? 'Service' : undefined}>
            <Dropdown
              disabled={actionRunning !== undefined}
              menu={{ items: ftlActionItems, onClick: ({ key }) => void runAction(item.name, key as FtlAction) }}
            >
              <Button icon={<ToolOutlined />} aria-label={`Service actions for ${item.name}`} loading={actionRunning === item.name}
                disabled={actionRunning !== undefined && actionRunning !== item.name}>
                {compact ? null : 'Service '}<DownOutlined />
              </Button>
            </Dropdown>
          </Tooltip>
          {compact ? (
            <Tooltip title="Edit">
              <Button icon={<EditOutlined />} aria-label={`Edit ${item.name}`} onClick={() => openEdit(item)} />
            </Tooltip>
          ) : <Button icon={<EditOutlined />} onClick={() => openEdit(item)}>Edit</Button>}
          <Popconfirm
            title={`Remove ${item.name}?`}
            description="This only forgets the instance locally."
            okText="Remove"
            okButtonProps={{ danger: true }}
            onConfirm={() => remove(item.name)}
          >
            <Button danger icon={<DeleteOutlined />} aria-label={`Remove ${item.name}`} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Instances</Typography.Title>
          <Typography.Paragraph type="secondary">Manage the Pi-hole servers available to this application.</Typography.Paragraph>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load()}>Refresh</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>Add instance</Button>
        </Space>
      </div>
      <Table
        className="instances-table"
        scroll={{ x: 'max-content' }}
        rowKey="name"
        columns={columns}
        dataSource={instances}
        loading={loading}
        pagination={false}
        expandable={{
          expandedRowKeys,
          rowExpandable: (item) => item.health !== null,
          expandedRowRender: (item) => item.health ? <HealthDetails health={item.health} /> : null,
          onExpand: (expanded, item) => setExpandOverrides((current) => {
            const next = new Map(current)
            next.set(item.name, expanded)
            return next
          }),
        }}
        locale={{ emptyText: <Empty description="No instances configured" /> }}
      />
      <Modal
        title={editing ? `Edit ${editing.name}` : 'Add instance'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={() => void save()}
        confirmLoading={saving}
        okText="Save instance"
        destroyOnHidden
      >
        <Form form={form} layout="vertical" requiredMark="optional">
          <Form.Item name="name" label="Name" rules={[{ required: true }, { max: 100 }]}>
            <Input autoFocus={!editing} disabled={Boolean(editing)} placeholder="pihole1" />
          </Form.Item>
          <Form.Item name="base_url" label="Base URL" rules={[{ required: true }, { type: 'url', message: 'Enter a complete URL such as http://192.168.1.2' }]}>
            <Input placeholder="http://192.168.1.2" />
          </Form.Item>
          <Form.Item
            name="app_password"
            label={editing ? 'Replace app password' : 'App password'}
            extra={editing ? 'Leave blank to keep the currently stored password.' : undefined}
            rules={editing ? undefined : [{ required: true }]}
          >
            <Input.Password autoComplete="new-password" placeholder={editing ? 'Unchanged' : undefined} />
          </Form.Item>
          <Form.Item name="notes" label="Notes" initialValue="">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="verify_ssl" label="Verify TLS certificate" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(previous, current) => previous.verify_ssl !== current.verify_ssl}>
            {({ getFieldValue }) => getFieldValue('verify_ssl') === false ? (
              <Alert
                type="warning"
                showIcon
                message="TLS certificate verification is disabled"
                description="Use this only for a trusted Pi-hole with a self-signed certificate. Traffic will still be encrypted, but the server's identity will not be verified."
              />
            ) : null}
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}
