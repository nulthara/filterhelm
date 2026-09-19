import { Button, Select, Space, Typography } from 'antd'
import type { BulkAction } from '../api'

interface BulkActionBarProps {
  selectedCount: number
  instanceNames: string[]
  selectedInstances: string[]
  onInstancesChange: (instances: string[]) => void
  actions: Array<{ key: BulkAction; label: string; danger?: boolean }>
  onAction: (action: BulkAction) => void
  onClear: () => void
  disabled: boolean
}

export default function BulkActionBar({ selectedCount, instanceNames, selectedInstances, onInstancesChange, actions, onAction, onClear, disabled }: BulkActionBarProps) {
  if (selectedCount === 0) return null
  return (
    <div className="bulk-bar">
      <Space wrap>
        <Typography.Text>{selectedCount} selected</Typography.Text>
        <Select
          mode="multiple"
          aria-label="Bulk target instances"
          placeholder="Target instances"
          style={{ minWidth: 200 }}
          options={instanceNames.map((name) => ({ value: name, label: name }))}
          value={selectedInstances}
          onChange={onInstancesChange}
          disabled={disabled}
        />
        {actions.map((action) => (
          <Button key={action.key} danger={action.danger} disabled={disabled || !selectedInstances.length} onClick={() => onAction(action.key)}>{action.label}</Button>
        ))}
        <Button type="text" onClick={onClear} disabled={disabled}>Clear selection</Button>
      </Space>
    </div>
  )
}
