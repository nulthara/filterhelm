import { Tabs, Typography } from 'antd'
import DNSHostsTab from './DNSHostsTab'
import DNSCnamesTab from './DNSCnamesTab'

export default function DNSPage() {
  return (
    <>
      <div className="page-heading">
        <div>
          <Typography.Title level={2}>Local DNS</Typography.Title>
          <Typography.Paragraph type="secondary">Compare and manage custom DNS records across Pi-hole instances.</Typography.Paragraph>
        </div>
      </div>
      <Tabs items={[
        { key: 'hosts', label: 'A/AAAA records', children: <DNSHostsTab /> },
        { key: 'cnames', label: 'CNAME records', children: <DNSCnamesTab /> },
      ]} />
    </>
  )
}
