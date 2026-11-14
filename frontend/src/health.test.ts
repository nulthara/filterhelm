import { describe, expect, it } from 'vitest'
import type { InstanceHealth } from './api'
import { formatBytes, formatDuration, healthDetailRows } from './health'

const health: InstanceHealth = {
  status: 'healthy', reasons: [], host_uptime: 1296249, ftl_uptime: 432620,
  memory: { total: 2097152, used: 54137, available: 2042507, percent: 2.581453323364258 },
  swap: { total: 8388604, used: 3017108, percent: 35.9667472680794 },
  cpu: { cores: 4, percent: 6.974999904632568, load: [0.6064453125, 1.32763671875, 1.234375] },
  temperature: { value: 57, limit: 60, unit: 'C' },
  ftl: { cpu_percent: 0.02500000037252903, mem_percent: 1.43 },
  database: { size: 460140544, queries: 2636298, sqlite_version: '3.53.1' },
}

describe('healthDetailRows', () => {
  it('keeps all twelve rows in order and formats their values', () => {
    const rows = healthDetailRows(health)
    expect(rows.map(({ key, label }) => [key, label])).toEqual([
      ['host_uptime', 'Host uptime'], ['ftl_uptime', 'FTL uptime'], ['load', 'Load'],
      ['cpu', 'CPU'], ['ram', 'RAM'], ['ram_available', 'RAM available'], ['swap', 'Swap'],
      ['temperature', 'Temperature'], ['ftl_cpu', 'FTL CPU'], ['ftl_memory', 'FTL memory'],
      ['database', 'Database'], ['sqlite', 'SQLite'],
    ])
    const values = Object.fromEntries(rows.map(({ key, value }) => [key, value]))
    expect(values.host_uptime).toBe('15d 0h')
    expect(values.ram).toBe('2.6% (52.9 MiB / 2.0 GiB)')
    expect(values.sqlite).toBe('3.53.1')
  })

  it('shows unavailable host fields while keeping FTL and database values', () => {
    const rows = healthDetailRows({ ...health, host_uptime: null, memory: null, swap: null, cpu: null })
    const values = Object.fromEntries(rows.map(({ key, value }) => [key, value]))
    for (const key of ['host_uptime', 'load', 'cpu', 'ram', 'ram_available', 'swap']) {
      expect(values[key]).toBe('Unavailable')
    }
    expect(values.ftl_uptime).toBe('5d 0h')
    expect(values.ftl_cpu).toBe('0.0%')
    expect(values.database).toBe(`438.8 MiB · ${(2636298).toLocaleString()} queries`)
  })
})

it('formats byte sizes and sub-minute durations', () => {
  expect(formatBytes(0)).toBe('0 B')
  expect(formatBytes(1536)).toBe('1.5 KiB')
  expect(formatDuration(59)).toBe('0m')
})
