import { DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Button, Dropdown, Tooltip } from 'antd'
import type { ThemeMode } from './theme'
import { useThemeMode } from './themeContext'

const modes = {
  system: { icon: <DesktopOutlined />, label: 'System' },
  light: { icon: <SunOutlined />, label: 'Light' },
  dark: { icon: <MoonOutlined />, label: 'Dark' },
}

export default function ThemeToggle() {
  const { mode, setMode } = useThemeMode()
  const { icon, label } = modes[mode]
  return (
    <Dropdown menu={{
      selectable: true,
      selectedKeys: [mode],
      items: Object.entries(modes).map(([key, item]) => ({ key, ...item })),
      onClick: ({ key }) => setMode(key as ThemeMode),
    }}>
      <Tooltip title="Theme">
        <Button icon={icon} aria-label={`Theme: ${label}`} />
      </Tooltip>
    </Dropdown>
  )
}
