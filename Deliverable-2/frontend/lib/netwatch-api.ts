export type HostStatus = 'OK' | 'WARN' | 'DOWN' | string

export interface HostTelemetry {
  last_seen?: number
  loss?: number
  cpu?: number
  mem?: number
  status?: HostStatus
  age?: number
}

export interface NetwatchStatus {
  utilization?: number
  utilization_stale?: boolean
  telemetry_stale?: boolean
  data_stale?: boolean
  telemetry?: Record<string, HostTelemetry>
  alerts?: string[]
  countdowns?: Record<string, number>
  mitigation_active?: boolean
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function normalizeStatus(value: unknown): NetwatchStatus {
  if (!isRecord(value)) throw new Error('Backend returned an invalid status payload.')
  const telemetry = isRecord(value.telemetry)
    ? Object.fromEntries(Object.entries(value.telemetry).filter(([, host]) => isRecord(host))) as Record<string, HostTelemetry>
    : {}
  const alerts = Array.isArray(value.alerts) ? value.alerts.filter((alert): alert is string => typeof alert === 'string') : []
  const countdowns = isRecord(value.countdowns)
    ? Object.fromEntries(Object.entries(value.countdowns).filter(([, seconds]) => typeof seconds === 'number')) as Record<string, number>
    : {}
  return {
    utilization: typeof value.utilization === 'number' ? value.utilization : undefined,
    utilization_stale: typeof value.utilization_stale === 'boolean' ? value.utilization_stale : undefined,
    telemetry_stale: typeof value.telemetry_stale === 'boolean' ? value.telemetry_stale : undefined,
    data_stale: typeof value.data_stale === 'boolean' ? value.data_stale : false,
    telemetry,
    alerts,
    countdowns,
    mitigation_active: typeof value.mitigation_active === 'boolean' ? value.mitigation_active : false,
  }
}

export interface TriggerRequest {
  problem?: string
  experiment?: string
  host?: string
  target?: string
  rate?: string
  intensity?: number | string
  duration?: number
}

export interface TriggerResult {
  status: 'triggered' | 'error' | string
  message: string
}

export async function fetchStatus(signal?: AbortSignal): Promise<NetwatchStatus> {
  const response = await fetch('/api/status', { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Backend responded with ${response.status}.`)
  return normalizeStatus(await response.json())
}

export async function triggerExperiment(payload: TriggerRequest, signal?: AbortSignal): Promise<TriggerResult> {
  // Normalize fields to the backend canonical schema (problem, host, rate)
  const body = {
    problem: payload.problem ?? (payload.experiment === 'congestion' ? 'flood' : payload.experiment),
    host: payload.host ?? payload.target,
    rate: payload.rate !== undefined ? String(payload.rate) : (payload.intensity !== undefined ? String(payload.intensity) : undefined),
  }
  const response = await fetch('/trigger', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const errorMsg = data && typeof data === 'object' && 'message' in data && typeof data.message === 'string'
      ? data.message
      : `Trigger endpoint responded with HTTP ${response.status}.`
    throw new Error(errorMsg)
  }
  return data as TriggerResult
}

export interface TopologySwitch {
  id: string
  label: string
  dpid: string
  ports: number[]
}

export interface TopologyHost {
  id: string
  label: string
  ip: string
  mac: string
  connected_to: string
  switch_port: number
  role: string
}

export interface TopologyLink {
  id: string
  source: string
  target: string
  source_port: string | number
  target_port: string | number
  capacity_mbps: number
  delay: string
  is_bottleneck: boolean
}

export interface NetworkTopology {
  type: string
  description: string
  switches: TopologySwitch[]
  hosts: TopologyHost[]
  links: TopologyLink[]
}

export interface LinkLiveStats {
  id: string
  source: string
  destination: string
  source_port: string | number
  destination_port: string | number
  capacity_mbps: number
  is_bottleneck: boolean
  delay: string
  tx_bytes?: number | null
  rx_bytes?: number | null
  throughput_mbps?: number | null
  utilization?: number | null
  timestamp?: number | null
  age_seconds?: number | null
  measured: boolean
  stale: boolean
  status: 'up' | 'congested' | 'unknown'
  measurement_source?: string
}

export interface NetworkLinksResponse {
  timestamp: number
  age_seconds?: number | null
  stale: boolean
  links: Record<string, LinkLiveStats>
}

export async function fetchTopology(signal?: AbortSignal): Promise<NetworkTopology> {
  const response = await fetch('/api/topology', { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Topology endpoint responded with ${response.status}.`)
  return response.json()
}

export async function fetchNetworkLinks(signal?: AbortSignal): Promise<NetworkLinksResponse> {
  const response = await fetch('/api/network/links', { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Network links endpoint responded with ${response.status}.`)
  return response.json()
}

export interface OpenFlowEntry {
  id: string
  dpid: number
  switch: string
  table_id: number
  priority: number
  cookie: number
  match: Record<string, string>
  actions: string[]
  duration_sec: number
  idle_timeout: number
  hard_timeout: number
  packet_count: number
  byte_count: number
  throughput_mbps?: number | null
  dropped_mbps?: number | null
  measured: boolean
  is_mitigation: boolean
  is_table_miss: boolean
}

export interface TopTalker {
  rank: number
  flow_id: string
  switch: string
  source: string
  destination: string
  protocol: string
  throughput_mbps?: number | null
  byte_count: number
  packet_count: number
  measured: boolean
  traverses_bottleneck: boolean
  share_of_bottleneck_pct?: number | null
}

export interface NetworkFlowsResponse {
  timestamp: number
  age_seconds?: number | null
  stale: boolean
  flows_count: number
  flows: OpenFlowEntry[]
  top_talkers: TopTalker[]
}

export async function fetchNetworkFlows(signal?: AbortSignal): Promise<NetworkFlowsResponse> {
  const response = await fetch('/api/network/flows', { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Network flows endpoint responded with ${response.status}.`)
  return response.json()
}

// ---------------------------------------------------------------------------
// Priority 4: Historical Analytics, Persistence & NOC Intelligence
// ---------------------------------------------------------------------------

export type TimeRange = '15m' | '1h' | '3h' | '24h'

export interface BottleneckPoint {
  timestamp: number
  throughput_mbps?: number | null
  max_throughput_mbps?: number | null
  utilization_pct?: number | null
  max_utilization_pct?: number | null
  status: string
  measured: boolean
  resolution: 'raw' | '1h_rollup'
}

export interface BottleneckHistoryResponse {
  link_id: string
  range: TimeRange | string
  start_time: number
  end_time: number
  capacity_mbps: number
  threshold_pct: number
  point_count: number
  points: BottleneckPoint[]
}

export interface HostTelemetryPoint {
  timestamp: number
  vm_cpu_util?: number | null
  vm_mem_util?: number | null
  telemetry_loss_rate?: number | null
  agent_status: string
}

export interface HostHistoryResponse {
  range: TimeRange | string
  start_time: number
  end_time: number
  vm_scope: string
  hosts: Record<string, HostTelemetryPoint[]>
}

export interface FlowHistoryPoint {
  timestamp: number
  throughput_mbps?: number | null
  share_of_bottleneck_pct?: number | null
  measured: boolean
}

export interface TopFlowHistory {
  flow_id: string
  switch: string
  source: string
  destination: string
  protocol: string
  traverses_bottleneck: boolean
  avg_throughput_mbps: number
  peak_throughput_mbps: number
  points: FlowHistoryPoint[]
}

export interface FlowHistoryResponse {
  range: TimeRange | string
  start_time: number
  end_time: number
  flows_count: number
  top_flows: TopFlowHistory[]
}

export interface AnalyticsKPIs {
  congestion_free_pct?: number | null
  peak_utilization_pct?: number | null
  peak_utilization_timestamp?: number | null
  mttm_seconds?: number | null
  mttr_seconds?: number | null
  active_incidents_count: number
  total_incidents_in_window: number
}

export interface StorageMetrics {
  db_size_bytes: number
  wal_size_bytes: number
  total_size_bytes: number
  raw_retention_hours: number
  rollup_retention_days: number
  row_counts: Record<string, number>
}

export interface AnalyticsSummaryResponse {
  range: TimeRange | string
  start_time: number
  end_time: number
  metrics: AnalyticsKPIs
  storage: StorageMetrics
}

export interface IncidentRecord {
  id?: number
  incident_id: string
  event_type: string
  target: string
  state: 'ACTIVE' | 'RESOLVED' | string
  severity: 'CRITICAL' | 'WARNING' | string
  start_time: number
  mitigation_time?: number | null
  end_time?: number | null
  duration_sec?: number | null
  time_to_mitigation_sec?: number | null
  time_to_recovery_sec?: number | null
  peak_value: number
  trigger_threshold: number
  description: string
}

export interface IncidentResponse {
  count: number
  incidents: IncidentRecord[]
}

export interface AnomalyRecord {
  incident_id: string
  event_type: 'TRAFFIC_BURST' | 'VM_CPU_SATURATION' | 'HOST_SILENT' | 'CONGESTION' | string
  target: string
  severity: string
  start_time: number
  end_time?: number | null
  duration_sec?: number | null
  peak_value: number
  trigger_threshold: number
  description: string
}

export interface AnomalyResponse {
  count: number
  anomalies: AnomalyRecord[]
}

export async function fetchBottleneckHistory(range: TimeRange = '1h', signal?: AbortSignal): Promise<BottleneckHistoryResponse> {
  const response = await fetch(`/api/history/bottleneck?range=${encodeURIComponent(range)}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Bottleneck history responded with ${response.status}.`)
  return response.json()
}

export async function fetchHostHistory(range: TimeRange = '1h', host = 'all', signal?: AbortSignal): Promise<HostHistoryResponse> {
  const response = await fetch(`/api/history/hosts?range=${encodeURIComponent(range)}&host=${encodeURIComponent(host)}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Host history responded with ${response.status}.`)
  return response.json()
}

export async function fetchFlowHistoryTop(range: TimeRange = '1h', top = 5, signal?: AbortSignal): Promise<FlowHistoryResponse> {
  const response = await fetch(`/api/history/flows/top?range=${encodeURIComponent(range)}&top=${top}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Top flow history responded with ${response.status}.`)
  return response.json()
}

export async function fetchAnalyticsSummary(range: TimeRange = '1h', signal?: AbortSignal): Promise<AnalyticsSummaryResponse> {
  const response = await fetch(`/api/analytics/summary?range=${encodeURIComponent(range)}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Analytics summary responded with ${response.status}.`)
  return response.json()
}

export async function fetchAnalyticsIncidents(state?: 'ACTIVE' | 'RESOLVED', limit = 50, signal?: AbortSignal): Promise<IncidentResponse> {
  const query = state ? `?state=${encodeURIComponent(state)}&limit=${limit}` : `?limit=${limit}`
  const response = await fetch(`/api/analytics/incidents${query}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Analytics incidents responded with ${response.status}.`)
  return response.json()
}

export async function fetchAnalyticsAnomalies(limit = 20, signal?: AbortSignal): Promise<AnomalyResponse> {
  const response = await fetch(`/api/analytics/anomalies?limit=${limit}`, { signal, cache: 'no-store' })
  if (!response.ok) throw new Error(`Analytics anomalies responded with ${response.status}.`)
  return response.json()
}

// ---------------------------------------------------------------------------
// Priority 5: Experiment Process Supervision & Real Network Recovery API
// ---------------------------------------------------------------------------

export interface ExperimentCheckDetail {
  passed: boolean
  details: string
}

export interface ExperimentRecord {
  exp_id: string
  host: string
  cmd: string
  rate: string
  duration: number
  start_time: number
  expiry: number
  state: 'RUNNING' | 'RECOVERING' | 'RECOVERED' | 'RECOVERY_FAILED' | 'RECOVERY_TIMEOUT'
  child_pid?: number | null
  recovery_checks?: Record<string, ExperimentCheckDetail>
  /** @deprecated Use vm_resources_baseline — renamed in Defect 6 fix */
  failed_checks?: string[]
  error?: string | null
  completed_at?: number | null
}

export interface ExperimentStatusResponse {
  active: ExperimentRecord | null
  history: ExperimentRecord[]
  /** 'live' = answered by the running topology over IPC; 'file' = last saved snapshot; 'unavailable' = neither */
  source?: 'live' | 'file' | 'unavailable'
  /** true when the data could not be confirmed as current */
  stale?: boolean
}

export async function fetchExperimentStatus(signal?: AbortSignal): Promise<ExperimentStatusResponse> {
  const response = await fetch('/api/experiments/status', {
    cache: 'no-store',
    headers: { Accept: 'application/json' },
    signal,
  })
  if (!response.ok) {
    throw new Error(`Failed to fetch experiment status: ${response.status} ${response.statusText}`)
  }
  return response.json()
}
