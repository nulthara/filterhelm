import type { InstanceHealth } from './api'

export function formatDuration(seconds: number): string {
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor(seconds / 3600) % 24
  const minutes = Math.floor(seconds / 60) % 60
  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes}m`
  return `${minutes}m`
}

export function formatBytes(bytes: number): string {
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`
}

export interface HealthDetailRow {
  key: string
  label: string
  value: string
}

export function healthDetailRows(health: InstanceHealth): HealthDetailRow[] {
  const { host_uptime, ftl_uptime, memory, swap, cpu, temperature, ftl, database } = health
  const temperatureLimit = temperature?.limit == null
    ? 'no limit configured' : `limit ${temperature.limit.toFixed(0)} °${temperature.unit}`
  const temperatureDisplay = temperature === null
    ? 'Unavailable' : `${temperature.value.toFixed(0)} °${temperature.unit} (${temperatureLimit})`
  return [
    { key: 'host_uptime', label: 'Host uptime', value: host_uptime === null ? 'Unavailable' : formatDuration(host_uptime) },
    { key: 'ftl_uptime', label: 'FTL uptime', value: formatDuration(ftl_uptime) },
    { key: 'load', label: 'Load', value: cpu === null ? 'Unavailable' : `${cpu.load.map((value) => value.toFixed(2)).join(' / ')} (${cpu.cores} cores)` },
    { key: 'cpu', label: 'CPU', value: cpu === null ? 'Unavailable' : `${cpu.percent.toFixed(1)}%` },
    { key: 'ram', label: 'RAM', value: memory === null ? 'Unavailable' : `${memory.percent.toFixed(1)}% (${formatBytes(memory.used * 1024)} / ${formatBytes(memory.total * 1024)})` },
    { key: 'ram_available', label: 'RAM available', value: memory === null ? 'Unavailable' : formatBytes(memory.available * 1024) },
    { key: 'swap', label: 'Swap', value: swap === null ? 'Unavailable' : `${swap.percent.toFixed(1)}% (${formatBytes(swap.used * 1024)} / ${formatBytes(swap.total * 1024)})` },
    { key: 'temperature', label: 'Temperature', value: temperatureDisplay },
    { key: 'ftl_cpu', label: 'FTL CPU', value: `${ftl.cpu_percent.toFixed(1)}%` },
    { key: 'ftl_memory', label: 'FTL memory', value: `${ftl.mem_percent.toFixed(1)}%` },
    { key: 'database', label: 'Database', value: `${formatBytes(database.size)} · ${database.queries.toLocaleString()} queries` },
    { key: 'sqlite', label: 'SQLite', value: database.sqlite_version },
  ]
}
