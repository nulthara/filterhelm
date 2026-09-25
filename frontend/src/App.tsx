import { useEffect, useRef, useState } from 'react'
import { AuditOutlined, DashboardOutlined, DiffOutlined, DatabaseOutlined, DesktopOutlined, FileSearchOutlined, FilterOutlined, GlobalOutlined, HistoryOutlined, LogoutOutlined, SettingOutlined, TeamOutlined, UnorderedListOutlined } from '@ant-design/icons'
import { Alert, App as AntApp, Button, Card, Form, Input, Layout, Menu, Skeleton, Space, Tooltip, Typography } from 'antd'
import InstancesPage from './pages/InstancesPage'
import SettingsPage from './pages/SettingsPage'
import DNSPage, { type DNSTab } from './pages/DNSPage'
import StatusPage from './pages/StatusPage'
import AdlistsPage from './pages/AdlistsPage'
import DomainsPage from './pages/DomainsPage'
import GroupsPage from './pages/GroupsPage'
import ClientsPage from './pages/ClientsPage'
import LookupPage from './pages/LookupPage'
import QueriesPage from './pages/QueriesPage'
import AuditPage from './pages/AuditPage'
import DriftPage, { type DriftTarget } from './pages/DriftPage'
import { api, type AuthStatus } from './api'

const { Header, Sider, Content } = Layout

export default function App() {
  const { message } = AntApp.useApp()
  const [page, setPage] = useState('status')
  const [dnsTab, setDnsTab] = useState<DNSTab>('hosts')
  const [auth, setAuth] = useState<AuthStatus>()
  const [authError, setAuthError] = useState<string>()
  const [loggingIn, setLoggingIn] = useState(false)
  const [loginForm] = Form.useForm<{ username: string; password: string }>()

  const openFromDrift = (target: DriftTarget) => { setDnsTab(target.dnsTab ?? 'hosts'); setPage(target.page) }

  const refreshAuth = () => api.authStatus().then(setAuth).catch((error) => setAuthError(error instanceof Error ? error.message : 'Unable to check authentication'))
  useEffect(() => { void refreshAuth() }, [])

  const sessionExpiredHandling = useRef(false)
  useEffect(() => api.onSessionExpired(() => {
    if (sessionExpiredHandling.current) return
    sessionExpiredHandling.current = true
    message.warning('Your session has expired. Please sign in again.')
    void refreshAuth().finally(() => { sessionExpiredHandling.current = false })
  }), [])

  const login = async () => {
    const values = await loginForm.validateFields()
    setLoggingIn(true)
    setAuthError(undefined)
    try {
      await api.login(values.username, values.password)
      loginForm.resetFields()
      await refreshAuth()
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Login failed')
    } finally {
      setLoggingIn(false)
    }
  }

  const logout = async () => {
    try {
      await api.logout()
      await refreshAuth()
    } catch (error) {
      message.error(error instanceof Error ? error.message : 'Logout failed')
    }
  }

  if (!auth && !authError) return <Card><Skeleton active /></Card>
  if (!auth?.authenticated) {
    return (
      <Layout className="app-shell">
        <Header className="app-header"><div className="brand-mark">π</div><Typography.Title level={4} className="brand-title">Pi-hole Manager</Typography.Title></Header>
        <Content className="auth-content">
          <Card title={auth?.mode === 'proxy' ? 'Authentication required' : 'Sign in'} className="auth-card">
            {authError ? <Alert type="error" showIcon message={authError} className="dns-error" /> : null}
            {auth && !auth.configured ? (
              <Alert type="warning" showIcon message="No administrator is configured" description={<span>Run <Typography.Text code>./.venv/bin/python -m app.auth set-password --username admin</Typography.Text> on the server.</span>} />
            ) : auth?.mode === 'proxy' ? (
              <Alert type="info" showIcon message="Authenticate through the configured reverse proxy, then reload this page." />
            ) : (
              <Form form={loginForm} layout="vertical" onFinish={() => void login()}>
                <Form.Item name="username" label="Username" rules={[{ required: true }]}><Input autoComplete="username" autoFocus /></Form.Item>
                <Form.Item name="password" label="Password" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
                <Button type="primary" htmlType="submit" loading={loggingIn} block>Sign in</Button>
              </Form>
            )}
          </Card>
        </Content>
      </Layout>
    )
  }

  return (
    <Layout className="app-shell">
      <Header className="app-header">
        <div className="brand-mark" aria-hidden="true">π</div>
        <Typography.Title level={4} className="brand-title">Pi-hole Manager</Typography.Title>
        <Space className="auth-actions">
          <Typography.Text className="auth-user">{auth.user}</Typography.Text>
          {auth.mode === 'local' ? (
            <Button className="logout-button" icon={<LogoutOutlined />} onClick={() => void logout()}>Sign out</Button>
          ) : (
            <Tooltip title="Sign out through your reverse proxy or identity provider.">
              <span><Button className="logout-button" icon={<LogoutOutlined />} disabled>Proxy-managed session</Button></span>
            </Tooltip>
          )}
        </Space>
      </Header>
      <Layout>
        <Sider breakpoint="lg" collapsedWidth="0" width={220}>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[page]}
            onClick={({ key }) => { setDnsTab('hosts'); setPage(key) }}
            items={[
              { key: 'status', icon: <DashboardOutlined />, label: 'Status' },
              { key: 'queries', icon: <HistoryOutlined />, label: 'Queries' },
              { key: 'drift', icon: <DiffOutlined />, label: 'Drift' },
              { key: 'instances', icon: <DatabaseOutlined />, label: 'Instances' },
              { key: 'dns', icon: <GlobalOutlined />, label: 'Local DNS' },
              { key: 'domains', icon: <FilterOutlined />, label: 'Domains' },
              { key: 'lookup', icon: <FileSearchOutlined />, label: 'Lookup' },
              { key: 'adlists', icon: <UnorderedListOutlined />, label: 'Block Lists' },
              { key: 'groups', icon: <TeamOutlined />, label: 'Groups' },
              { key: 'clients', icon: <DesktopOutlined />, label: 'Clients' },
              { key: 'settings', icon: <SettingOutlined />, label: 'Settings' },
              { key: 'audit', icon: <AuditOutlined />, label: 'Audit log' },
            ]}
          />
        </Sider>
        <Content className="app-content">
          {page === 'status' ? <StatusPage /> : page === 'queries' ? <QueriesPage /> : page === 'drift' ? <DriftPage onNavigate={openFromDrift} /> : page === 'instances' ? <InstancesPage /> : page === 'dns' ? <DNSPage key={dnsTab} initialTab={dnsTab} /> : page === 'domains' ? <DomainsPage /> : page === 'lookup' ? <LookupPage /> : page === 'adlists' ? <AdlistsPage /> : page === 'groups' ? <GroupsPage /> : page === 'clients' ? <ClientsPage /> : page === 'audit' ? <AuditPage /> : <SettingsPage />}
        </Content>
      </Layout>
    </Layout>
  )
}
