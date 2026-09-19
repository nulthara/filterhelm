import { useCallback, useEffect, useState, type Key } from 'react'
import { PauseCircleOutlined, PlayCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { App, Button, Dropdown, Empty, Space, Table, Tag, Typography } from 'antd'
import type { MenuProps, TableProps } from 'antd'
import { api, type BlockingStatus } from '../api'

type PauseDuration = 10 | 30 | 300 | undefined

const pauseItems: MenuProps['items'] = [
  { key: '10', label: '10 seconds' },
  { key: '30', label: '30 seconds' },
  { key: '300', label: '5 minutes' },
  { type: 'divider' },
  { key: 'indefinite', label: 'Indefinitely', danger: true },
]

function durationFromKey(key: string): PauseDuration {
  return key === 'indefinite' ? undefined : Number(key) as 10 | 30 | 300
}

function durationLabel(duration: PauseDuration): string {
  if (duration === 10) return '10 seconds'
  if (duration === 30) return '30 seconds'
  if (duration === 300) return '5 minutes'
  return 'indefinitely'
}

function stateDisplay(instance: BlockingStatus, elapsedSeconds: number) {
  if (!instance.reachable) return <Tag color="error">Unavailable</Tag>
  if (instance.enabled) return <Tag color="success">Blocking active</Tag>
  if (instance.timer === null) return <Tag color="warning">Paused indefinitely</Tag>
  const remaining = Math.max(0, Math.ceil(instance.timer - elapsedSeconds))
  return <Tag color="warning">Paused · {remaining ? `${remaining}s remaining` : 'resuming…'}</Tag>
}

export default function StatusPage() {
  const { message, modal } = App.useApp()
  const [instances, setInstances] = useState<BlockingStatus[]>([])
  const [selected, setSelected] = useState<Key[]>([])
  const [loading, setLoading] = useState(true)
  const [working, setWorking] = useState(false)
  const [checkedAt, setCheckedAt] = useState(0)
  const [now, setNow] = useState(0)

  const load = useCallback(async (showLoading = false, background = false) => {
    if (showLoading) setLoading(true)
    try {
      const result = await api.status(background ? { background: true } : undefined)
      const timestamp = Date.now()
      setInstances(result.instances)
      setCheckedAt(timestamp)
      setNow(timestamp)
      setSelected((current) => current.filter((name) => result.instances.some((item) => item.name === name && item.reachable)))
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to load status')
    } finally {
      if (showLoading) setLoading(false)
    }
  }, [message])

  useEffect(() => {
    let cancelled = false
    let refresh: number | undefined
    const scheduleRefresh = () => {
      if (cancelled) return
      refresh = window.setTimeout(() => {
        void load(false, true).finally(scheduleRefresh)
      }, 10_000)
    }
    api.status()
      .then((result) => {
        const timestamp = Date.now()
        setInstances(result.instances)
        setCheckedAt(timestamp)
        setNow(timestamp)
      })
      .catch((error) => message.error(error instanceof Error ? error.message : 'Unable to load status'))
      .finally(() => {
        setLoading(false)
        scheduleRefresh()
      })
    return () => {
      cancelled = true
      if (refresh !== undefined) window.clearTimeout(refresh)
    }
  }, [load, message])

  useEffect(() => {
    const tick = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(tick)
  }, [])

  const apply = async (names: string[], enabled: boolean, duration?: PauseDuration) => {
    const target = names.length === 1 ? names[0] : `${names.length} instances`
    const action = enabled
      ? 'resume blocking'
      : duration === undefined ? 'pause blocking indefinitely' : `pause blocking for ${durationLabel(duration)}`
    const confirmed = await modal.confirm({
      title: `${enabled ? 'Resume' : 'Pause'} blocking on ${target}?`,
      content: `This will ${action} on ${target}.`,
      okText: enabled ? 'Resume blocking' : 'Pause blocking',
      okButtonProps: enabled ? undefined : { danger: true },
    })
    if (!confirmed) return
    setWorking(true)
    try {
      const { results } = await api.updateBlocking(names, enabled, duration)
      const failures = results.filter((result) => !result.success)
      if (failures.length) {
        message.warning(`${results.length - failures.length} succeeded; ${failures.length} failed: ${failures.map((item) => item.name).join(', ')}`)
      } else {
        message.success(`${enabled ? 'Blocking resumed' : 'Blocking paused'} on ${target}`)
      }
      await load()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Unable to update blocking')
    } finally {
      setWorking(false)
    }
  }

  const pauseMenu = (names: string[]): MenuProps => ({
    items: pauseItems,
    onClick: ({ key }) => void apply(names, false, durationFromKey(key)),
  })

  const elapsedSeconds = (now - checkedAt) / 1000
  const columns: TableProps<BlockingStatus>['columns'] = [
    { title: 'Instance', dataIndex: 'name', render: (name) => <Typography.Text strong>{name}</Typography.Text> },
    { title: 'Blocking status', render: (_, instance) => stateDisplay(instance, elapsedSeconds) },
    {
      title: 'Detail', render: (_, instance) => instance.error
        ? <Typography.Text type="danger">{instance.error}</Typography.Text>
        : <Typography.Text type="secondary">{instance.enabled ? 'Queries are being filtered normally.' : 'DNS continues resolving without blocklist filtering.'}</Typography.Text>,
    },
    {
      title: 'Actions', align: 'right', render: (_, instance) => (
        <Space>
          <Dropdown menu={pauseMenu([instance.name])} trigger={['click']} disabled={!instance.reachable || working}>
            <Button danger icon={<PauseCircleOutlined />}>Pause</Button>
          </Dropdown>
          <Button
            icon={<PlayCircleOutlined />}
            disabled={!instance.reachable || instance.enabled !== false || working}
            onClick={() => void apply([instance.name], true)}
          >Resume</Button>
        </Space>
      ),
    },
  ]

  const selectedNames = selected.map(String)
  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Status</Typography.Title>
          <Typography.Paragraph type="secondary">Monitor blocking and temporarily pause one or more Pi-hole instances.</Typography.Paragraph>
        </div>
        <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(true)}>Refresh</Button>
      </div>
      <div className="status-actions">
        <Typography.Text>{selected.length ? `${selected.length} selected` : 'Select instances for a group action'}</Typography.Text>
        <Space>
          <Dropdown menu={pauseMenu(selectedNames)} trigger={['click']} disabled={!selected.length || working}>
            <Button danger icon={<PauseCircleOutlined />}>Pause selected</Button>
          </Dropdown>
          <Button icon={<PlayCircleOutlined />} disabled={!selected.length || working} onClick={() => void apply(selectedNames, true)}>
            Resume selected
          </Button>
        </Space>
      </div>
      <Table
        rowKey="name"
        columns={columns}
        dataSource={instances}
        loading={loading}
        pagination={false}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: setSelected,
          getCheckboxProps: (instance) => ({ disabled: !instance.reachable }),
        }}
        locale={{ emptyText: <Empty description="No instances configured" /> }}
        scroll={{ x: 'max-content' }}
      />
    </>
  )
}
