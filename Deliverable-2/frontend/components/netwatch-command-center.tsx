'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import {
  Activity, AlertTriangle, Bell, CheckCircle2, ChevronDown, ChevronLeft, CircleGauge,
  Clock3, Cpu, Database, FlaskConical, Gauge, GitBranch, HardDrive, LayoutDashboard,
  LifeBuoy, Menu, Network, RefreshCw, Router, Server, ShieldCheck,
  Signal, SlidersHorizontal, TerminalSquare, XCircle, Zap
} from 'lucide-react'
import { fetchStatus, type HostTelemetry, type NetwatchStatus } from '@/lib/netwatch-api'
import { PrototypePage, type PageKey } from '@/components/netwatch-prototype-pages'

type NavItem = { label: string; icon: typeof Activity }
const navItems: (NavItem & { href: string; page?: PageKey })[] = [
  { label: 'Command Center', icon: LayoutDashboard, href: '/' }, { label: 'Topology Builder', icon: GitBranch, href: '/topology-builder', page: 'topology' },
  { label: 'Live Network', icon: Network, href: '/live-network', page: 'live' }, { label: 'Host Monitoring', icon: Server, href: '/host-monitoring', page: 'hosts' },
  { label: 'Network Intelligence', icon: Signal, href: '/network-intelligence', page: 'intelligence' }, { label: 'Experiment Center', icon: FlaskConical, href: '/experiment-center', page: 'experiments' },
  { label: 'Incident Management', icon: LifeBuoy, href: '/incident-management', page: 'incidents' },
]

function formatTime(epoch?: number) { return epoch ? new Date(epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '—' }
function formatAge(host: HostTelemetry) { return typeof host.age === 'number' ? `${host.age.toFixed(1)}s ago` : formatTime(host.last_seen) }
function severity(status?: string) { return status === 'DOWN' ? 'critical' : status === 'WARN' ? 'warning' : 'healthy' }

export default function NetwatchCommandCenter() {
  const pathname = usePathname()
  const router = useRouter()
  const [collapsed, setCollapsed] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const activeItem = navItems.find((item) => item.href === pathname) ?? navItems[0]
  const [data, setData] = useState<NetwatchStatus | null>(null)
  const dataRef = useRef<NetwatchStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const [staleSince, setStaleSince] = useState<Date | null>(null)

  const refresh = useCallback(async () => {
    setRefreshing(true)
    try { const nextData = await fetchStatus(); dataRef.current = nextData; setData(nextData); setError(null); setStaleSince(null); setLastUpdated(new Date()) }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to reach backend.'); if (dataRef.current) setStaleSince((current) => current ?? new Date()) }
    finally { setLoading(false); setRefreshing(false) }
  }, [])

  useEffect(() => { refresh(); const id = window.setInterval(refresh, 2500); return () => window.clearInterval(id) }, [refresh])

  const hosts = useMemo(() => Object.entries(data?.telemetry ?? {}), [data])
  const reportingCount = hosts.length
  const alerts = data?.alerts ?? []
  const countdowns = Object.entries(data?.countdowns ?? {}).filter(([, seconds]) => seconds > 0)
  const connection = !error && data ? (data.data_stale ? 'Stale data' : 'Connected') : loading ? 'Connecting' : 'Disconnected'

  return <div className="nw-app">
    <aside className={`nw-sidebar ${collapsed ? 'is-collapsed' : ''} ${mobileNavOpen ? 'mobile-open' : ''}`}>
      <div className="nw-brand"><div className="nw-brand-mark"><Router size={18} /></div><span>NET<span>WATCH</span></span><button className="nw-icon-button nw-collapse" aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <ChevronLeft size={17} /> : <ChevronLeft size={17} />}</button></div>
      <div className="nw-section-label">OPERATIONS</div>
      <nav className="nw-nav" aria-label="Primary navigation">{navItems.map(({ label, icon: Icon, href }) => <button key={label} className={`nw-nav-item ${activeItem.href === href ? 'active' : ''}`} title={label} aria-current={activeItem.href === href ? 'page' : undefined} onClick={() => { router.push(href); setMobileNavOpen(false) }}><Icon size={18} /><span>{label}</span>{activeItem.href === href && <span className="nw-nav-dot" />}</button>)}</nav>
      
    </aside>
    <main className="nw-main">
      <header className="nw-header"><div className="nw-header-left"><button className="nw-mobile-menu nw-icon-button" aria-label="Toggle sidebar" aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen(!mobileNavOpen)}><Menu size={19} /></button><div><div className="nw-eyebrow">OPERATIONS / <span>{activeItem.label.toUpperCase()}</span></div><h1>{activeItem.label}</h1></div></div><div className="nw-header-actions"><div className={`nw-connection ${connection === 'Stale data' ? 'disconnected' : connection.toLowerCase()}`}><span className="nw-status-pulse" />{staleSince ? 'Stale data' : connection}<small>{data?.data_stale ? 'One or more telemetry sources are stale' : staleSince ? `Since ${staleSince.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : 'Awaiting data'}</small></div><button className={`nw-refresh nw-icon-button ${refreshing ? 'spin' : ''}`} onClick={refresh} aria-label="Refresh telemetry"><RefreshCw size={17} /></button></div></header>
      <div className="nw-content">
        {activeItem.page ? (
          <PrototypePage
            page={activeItem.page}
            statusData={data}
            onRefresh={refresh}
            loading={loading}
            connectionError={error}
          />
        ) : <>
        <div className="nw-page-intro"><div><p className="nw-kicker">SYSTEM HEALTH <span className="nw-live-chip"><span />LIVE</span></p><h2>Infrastructure at a glance</h2><p className="nw-muted">Real-time telemetry across your software-defined network.</p></div><div className="nw-data-source"><Database size={15} /><span>OpenFlow telemetry</span><ChevronDown size={14} /></div></div>
        {error && <div className="nw-error"><XCircle size={18} /><div><strong>Backend connection unavailable</strong><span>{error} {data ? 'Showing stale data from the last successful update.' : 'No live data is available.'}</span></div><button onClick={refresh}>Retry</button></div>}
        <section className="nw-kpi-grid" aria-label="Overview metrics">
          <MetricCard label="Bottleneck utilization" value={data?.utilization !== undefined ? `${data.utilization.toFixed(1)}%` : '—'} detail="Aggregate OpenFlow bottleneck" icon={<Gauge size={17} />} tone={data?.utilization && data.utilization >= 80 ? 'warning' : 'cyan'} progress={data?.utilization} />
          <MetricCard label="Reporting hosts" value={loading && !data ? '—' : String(reportingCount)} detail="Hosts reporting telemetry" icon={<Server size={17} />} tone="blue" suffix="hosts" />
          <MetricCard label="Active mitigation" value={data?.mitigation_active ? 'ACTIVE' : data ? 'INACTIVE' : '—'} detail="Controller mitigation state" icon={<ShieldCheck size={17} />} tone={data?.mitigation_active ? 'warning' : 'green'} />
          <MetricCard label="Active experiments" value={loading && !data ? '—' : String(countdowns.length)} detail="Live countdown entries" icon={<FlaskConical size={17} />} tone="purple" suffix="running" />
        </section>
        <section className="nw-grid-main"><div className="nw-panel nw-health"><PanelHeading icon={<Activity size={16} />} title="Network health" meta={data ? 'LIVE TELEMETRY' : 'WAITING FOR DATA'} /><div className="nw-health-body"><div className="nw-gauge"><div className="nw-gauge-ring" style={{ '--gauge': `${Math.min(data?.utilization ?? 0, 100)}%` } as React.CSSProperties}><div><strong>{data?.utilization !== undefined ? Math.round(data.utilization) : '—'}</strong><span>UTILIZATION</span></div></div><div className="nw-gauge-caption"><span className="nw-severity-dot warning" />Bottleneck load</div></div><div className="nw-health-list"><HealthRow icon={<Server />} label="Host reporting" value={reportingCount ? `${reportingCount} reporting` : 'No telemetry'} status={reportingCount ? 'healthy' : 'warning'} /><HealthRow icon={<ShieldCheck />} label="Mitigation" value={data?.mitigation_active ? 'Active' : data ? 'Inactive' : 'Unknown'} status={data?.mitigation_active ? 'warning' : data ? 'healthy' : 'warning'} /><HealthRow icon={<CircleGauge />} label="Backend API" value={connection} status={connection === 'Connected' ? 'healthy' : 'critical'} /></div></div></div><div className="nw-panel nw-alerts"><PanelHeading icon={<Bell size={16} />} title="Alerts & events" meta={`${alerts.length} CONTROLLER ALERTS`} /><div className="nw-alert-list">{alerts.length ? alerts.map((alert, index) => <div className="nw-alert" key={`${alert}-${index}`}><div className="nw-alert-icon"><AlertTriangle size={15} /></div><div><strong>{alert.replace(/^\[[^\]]+\]\s*/, '').replace(/^ALERT:\s*/, '')}</strong><span>{alert.match(/^\[([^\]]+)\]/)?.[1] ?? 'Controller alert'} · Controller alert</span></div></div>) : <EmptyState icon={<CheckCircle2 />} title="No active alerts" copy="The controller has not reported any alerts." />}</div></div></section>
        <section className="nw-grid-bottom"><div className="nw-panel"><PanelHeading icon={<Server size={16} />} title="Host status" meta={`${reportingCount} REPORTING`} /><div className="nw-table-wrap">{hosts.length ? <table className="nw-table"><thead><tr><th>Host</th><th>CPU utilization</th><th>Memory</th><th>Telemetry gaps</th><th>Status</th><th>Last seen</th></tr></thead><tbody>{hosts.map(([host, metrics]) => <tr key={host}><td><div className="nw-host"><div className="nw-host-icon"><Server size={14} /></div><strong>{host}</strong></div></td><td><MetricBar value={metrics.cpu} /></td><td><MetricBar value={metrics.mem} /></td><td><span className="nw-mono">{typeof metrics.loss === 'number' ? `${(metrics.loss * 100).toFixed(2)}%` : '—'}</span></td><td><StatusBadge status={metrics.status} /></td><td><span className="nw-muted nw-mono">{formatAge(metrics)}</span></td></tr>)}</tbody></table> : <EmptyState icon={<Server />} title="No telemetry received" copy="Hosts will appear here when the backend reports telemetry." />}</div></div><div className="nw-panel nw-experiments"><PanelHeading icon={<Zap size={16} />} title="Experiment activity" meta={`${countdowns.length} ACTIVE`} /><div className="nw-experiment-list">{countdowns.length ? countdowns.map(([name, seconds]) => <div className="nw-experiment" key={name}><div className="nw-experiment-icon"><TerminalSquare size={15} /></div><div className="nw-experiment-copy"><strong>{name}</strong><span>Active controller countdown</span></div><span className="nw-countdown">{seconds.toFixed(1)}s</span></div>) : <EmptyState icon={<Clock3 />} title="No active experiments" copy="Countdown entries will appear when experiments are running." />}</div></div></section>
        <footer className="nw-footer"><span><Activity size={13} />Polling every 2.5 seconds</span><span>Telemetry gaps reflect UDP sequence gaps, not packet loss.</span></footer>
        </>}
      </div>
    </main>
  </div>
}

function MetricCard({ label, value, detail, icon, tone, progress, suffix }: { label: string; value: string; detail: string; icon: React.ReactNode; tone: string; progress?: number; suffix?: string }) { return <article className="nw-metric"><div className={`nw-metric-icon ${tone}`}>{icon}</div><div className="nw-metric-copy"><span>{label}</span><div className="nw-metric-value">{value}{suffix && <small>{suffix}</small>}</div><p>{detail}</p></div>{progress !== undefined && <div className="nw-progress"><span style={{ width: `${Math.min(progress, 100)}%` }} /></div>}</article> }
function PanelHeading({ icon, title, meta }: { icon: React.ReactNode; title: string; meta: string }) { return <div className="nw-panel-heading"><div className="nw-panel-title"><span>{icon}</span><h3>{title}</h3></div><span className="nw-panel-meta">{meta}</span></div> }
function HealthRow({ icon, label, value, status }: { icon: React.ReactNode; label: string; value: string; status: string }) { return <div className="nw-health-row"><span className="nw-health-icon">{icon}</span><div><span>{label}</span><strong>{value}</strong></div><span className={`nw-severity-dot ${status}`} /></div> }
function MetricBar({ value }: { value?: number }) { const safe = typeof value === 'number' ? Math.max(0, Math.min(100, value)) : 0; return <div className="nw-bar-value"><div className="nw-mini-bar"><span className={safe > 80 ? 'high' : ''} style={{ width: `${safe}%` }} /></div><span className="nw-mono">{typeof value === 'number' ? `${value.toFixed(1)}%` : '—'}</span></div> }
function StatusBadge({ status }: { status?: string }) { const level = severity(status); return <span className={`nw-badge ${level}`}><span className="nw-severity-dot" />{status ?? 'UNKNOWN'}</span> }
function EmptyState({ icon, title, copy }: { icon: React.ReactNode; title: string; copy: string }) { return <div className="nw-empty">{icon}<strong>{title}</strong><span>{copy}</span></div> }
