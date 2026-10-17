import { useCallback, useEffect, useState } from 'react'
import { SafetyCertificateOutlined } from '@ant-design/icons'
import {
  Alert, App, Button, Card, Empty, Form, Input, InputNumber, Modal, Select, Skeleton, Space, Switch, Table, Typography,
} from 'antd'
import { api, type Change, type Instance, type SettingField, type SettingsValues } from '../api'
import { reviewedBefore } from '../settings'

const booleanValue = (value: unknown) => {
  if (typeof value === 'string') {
    return ['true', 'on', '1', 'yes'].includes(value.trim().toLowerCase())
  }
  return Boolean(value)
}

export default function SettingsPage() {
  const { message } = App.useApp()
  const [instances, setInstances] = useState<Instance[]>([])
  const [selected, setSelected] = useState<string>()
  const [fields, setFields] = useState<SettingField[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const [changes, setChanges] = useState<Change[]>([])
  const [pendingValues, setPendingValues] = useState<SettingsValues>()
  const [reviewed, setReviewed] = useState<SettingsValues>()
  const [applying, setApplying] = useState(false)
  const [form] = Form.useForm<SettingsValues>()

  useEffect(() => {
    api.listInstances()
      .then(({ instances: result }) => setInstances(result))
      .catch((reason) => message.error(reason instanceof Error ? reason.message : 'Unable to load instances'))
  }, [message])

  const loadSettings = useCallback(async (name: string) => {
    setSelected(name)
    setLoading(true)
    setError(undefined)
    try {
      const result = await api.getSettings(name)
      setFields(result.fields)
      const displayValues = { ...result.values }
      for (const field of result.fields) {
        if ((field.kind === 'list' || field.kind === 'lines') && Array.isArray(displayValues[field.key])) {
          displayValues[field.key] = (displayValues[field.key] as string[]).join(field.kind === 'lines' ? '\n' : ', ')
        } else if (field.kind === 'bool') {
          displayValues[field.key] = booleanValue(displayValues[field.key])
        } else if (field.kind === 'str' && displayValues[field.key] == null) {
          displayValues[field.key] = ''
        }
      }
      form.setFieldsValue(displayValues)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load settings')
    } finally {
      setLoading(false)
    }
  }, [form])

  const review = async () => {
    if (!selected) return
    const raw = await form.validateFields()
    const values = { ...raw }
    for (const field of fields) {
      if (field.kind === 'list') {
        values[field.key] = String(raw[field.key] ?? '').split(',').map((item) => item.trim()).filter(Boolean)
      } else if (field.kind === 'lines') {
        values[field.key] = String(raw[field.key] ?? '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
      } else if (field.kind === 'bool') {
        values[field.key] = booleanValue(raw[field.key])
      } else if (field.kind === 'str') {
        values[field.key] = String(raw[field.key] ?? '').trim()
      }
    }
    try {
      const result = await api.diffSettings(selected, values)
      if (!result.changes.length) {
        message.info('No changes to apply')
        return
      }
      setReviewed(reviewedBefore(result.changes))
      setPendingValues(values)
      setChanges(result.changes)
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : 'Unable to review changes')
    }
  }

  const apply = async () => {
    if (!selected || !pendingValues || !reviewed) return
    setApplying(true)
    try {
      const result = await api.applySettings(selected, pendingValues, reviewed)
      message.success(`${result.applied} change${result.applied === 1 ? '' : 's'} applied to ${selected}`)
      setChanges([])
      setPendingValues(undefined)
      setReviewed(undefined)
      await loadSettings(selected)
    } catch (reason) {
      message.error(reason instanceof Error ? reason.message : 'Unable to apply changes')
      setChanges([])
      setPendingValues(undefined)
      setReviewed(undefined)
    } finally {
      setApplying(false)
    }
  }

  const input = (field: SettingField) => {
    if (field.kind === 'bool') return <Switch />
    if (field.kind === 'int') return <InputNumber min={0} max={3} />
    if (field.kind === 'lines') {
      return <Input.TextArea rows={3} placeholder="true,192.168.1.0/24,192.168.1.1,lan" />
    }
    return <Input placeholder={field.kind === 'list' ? 'Comma-separated servers' : undefined} />
  }

  const help = (field: SettingField) => {
    if (field.key === 'dns.upstreams') {
      return <>DNS servers used for non-local queries. Enter comma-separated IP addresses or hostnames; append <Typography.Text code>#port</Typography.Text> for a custom port.</>
    }
    if (field.key === 'dns.queryLogging') {
      return <>Logs DNS queries and replies to <Typography.Text code>pihole.log</Typography.Text>. Disabling this reduces diagnostic detail.</>
    }
    if (field.key === 'misc.privacylevel') {
      return <>0: show everything; 1: hide domains; 2: hide domains and clients; 3: anonymous mode. Increasing privacy cannot restore details already hidden.</>
    }
    if (field.kind === 'lines') {
      return <>
        Format: <Typography.Text code>enabled,CIDR,target,domain</Typography.Text>. Add one reverse server per line;<br />
        for example <Typography.Text code>true,192.168.1.0/24,192.168.1.1,lan</Typography.Text>. Clear all lines to disable conditional forwarding.
      </>
    }
    return undefined
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Settings</Typography.Title>
          <Typography.Paragraph type="secondary">Review and update a deliberately limited set of settings on one instance.</Typography.Paragraph>
        </div>
        <Select
          className="instance-select"
          placeholder="Choose an instance"
          value={selected}
          options={instances.map((item) => ({ value: item.name, label: item.name }))}
          onChange={(value) => void loadSettings(value)}
        />
      </div>
      {!selected ? <Card><Empty description="Choose an instance to view its settings" /></Card> : null}
      {loading ? <Card><Skeleton active /></Card> : null}
      {error ? <Alert type="error" showIcon message="Settings unavailable" description={error} /> : null}
      {selected && !loading && !error ? (
        <Card>
          <Alert
            className="safety-alert"
            type="info"
            showIcon
            icon={<SafetyCertificateOutlined />}
            message="Changes are reviewed before anything is written"
            description={`Only ${selected} will be modified.`}
          />
          <Form form={form} layout="vertical" onFinish={() => void review()}>
            <div className="settings-grid">
              {fields.map((field) => (
                <Form.Item
                  key={field.key}
                  name={field.key}
                  label={field.label}
                  valuePropName={field.kind === 'bool' ? 'checked' : 'value'}
                  rules={field.kind === 'int' ? [{ type: 'number', min: 0, max: 3 }] : undefined}
                  extra={help(field)}
                >
                  {input(field)}
                </Form.Item>
              ))}
            </div>
            <Button type="primary" htmlType="submit">Review changes</Button>
          </Form>
        </Card>
      ) : null}
      <Modal
        title={`Confirm changes — ${selected ?? ''}`}
        open={changes.length > 0}
        width={760}
        okText={`Apply to ${selected ?? 'instance'}`}
        onOk={() => void apply()}
        confirmLoading={applying}
        onCancel={() => { setChanges([]); setPendingValues(undefined); setReviewed(undefined) }}
      >
        <Typography.Paragraph>Current values were read from the instance. Review every difference before applying.</Typography.Paragraph>
        <Table
          rowKey="key"
          pagination={false}
          dataSource={changes}
          columns={[
            { title: 'Setting', dataIndex: 'label' },
            { title: 'Current', dataIndex: 'before_display', render: (value) => value || <Typography.Text type="secondary">Empty</Typography.Text> },
            { title: 'New', dataIndex: 'after_display', render: (value) => <Typography.Text strong>{value || 'Empty'}</Typography.Text> },
          ]}
        />
        <Space className="modal-note"><Typography.Text type="secondary">No other instance will be changed.</Typography.Text></Space>
      </Modal>
    </>
  )
}
