'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertOctagon, BarChart2, Boxes, Check, CircleDot, Clock3, Cpu, Database, Flame, FlaskConical, Gauge, GitBranch, HardDrive, Info, Layers, Link2, LockKeyhole, Network, Play, Plus, Radio, Redo2, RefreshCw, Save, Server, ShieldAlert, ShieldCheck, SlidersHorizontal, Sparkles, TerminalSquare, Trash2, TrendingUp, TriangleAlert, Undo2, Upload, Download, ZoomIn, ZoomOut, Maximize2, X } from 'lucide-react'
import { ReactFlow, Background, Controls, Handle, MiniMap, Position, addEdge, useEdgesState, useNodesState, type Connection, type Edge, type Node, type NodeProps } from '@xyflow/react'
import {
  fetchTopology,
  fetchNetworkLinks,
  fetchNetworkFlows,
  triggerExperiment,
  fetchBottleneckHistory,
  fetchHostHistory,
  fetchFlowHistoryTop,
  fetchAnalyticsSummary,
  fetchAnalyticsIncidents,
  fetchAnalyticsAnomalies,
  fetchExperimentStatus,
  type NetworkTopology,
  type NetworkLinksResponse,
  type NetworkFlowsResponse,
  type OpenFlowEntry,
  type TopTalker,
  type LinkLiveStats,
  type NetwatchStatus,
  type HostTelemetry,
  type TimeRange,
  type BottleneckHistoryResponse,
  type BottleneckPoint,
  type HostHistoryResponse,
  type HostTelemetryPoint,
  type FlowHistoryResponse,
  type TopFlowHistory,
  type AnalyticsSummaryResponse,
  type IncidentRecord,
  type AnomalyRecord,
  type ExperimentRecord,
  type ExperimentStatusResponse,
} from '@/lib/netwatch-api'

export type PageKey = 'topology' | 'live' | 'hosts' | 'intelligence' | 'experiments' | 'incidents'
const pageCopy: Record<PageKey, { eyebrow: string; title: string; description: string }> = {
  topology: { eyebrow: 'NETWORK DESIGN / BUILDER', title: 'Topology Builder', description: 'Compose and validate draft topologies or load the live configured Mininet SDN network.' },
  live: { eyebrow: 'OPERATIONS / TOPOLOGY', title: 'Live Network', description: 'Real-time topology view with live OpenFlow bottleneck throughput and port telemetry.' },
  hosts: { eyebrow: 'TELEMETRY / HOSTS', title: 'Host Monitoring', description: 'Inspect host health and telemetry reporting status from the connected backend.' },
  intelligence: { eyebrow: 'ANALYTICS / INSIGHTS', title: 'Network Intelligence', description: 'Explore historical trends, executive NOC KPIs, incident lifecycle logs, and anomaly events.' },
  experiments: { eyebrow: 'CONTROL PLANE / LAB', title: 'Experiment Center', description: 'Inject controlled network and host stress experiments into the running topology.' },
  incidents: { eyebrow: 'OPERATIONS / RESPONSE', title: 'Incident Management', description: 'Review controller and server alerts and coordinate incident response.' },
}

export function PrototypePage({
  page,
  statusData,
  onRefresh,
  loading = false,
  connectionError = null,
}: {
  page: PageKey
  statusData?: NetwatchStatus | null
  onRefresh?: () => void
  loading?: boolean
  connectionError?: string | null
}) {
  const copy = pageCopy[page]
  // The topology editor is a local draft canvas. Its separate load action can
  // read live topology, but its edits are not backed by live state.
  const isConnected = page === 'hosts' || page === 'experiments' || page === 'incidents' || page === 'live' || page === 'intelligence'

  return (
    <>
      <div className="nw-page-intro">
        <div>
          <p className="nw-kicker">
            {copy.eyebrow}{' '}
            {isConnected ? (
              <span className="nw-live-chip">
                <span />
                LIVE
              </span>
            ) : (
              <span className="nw-live-chip" style={{ color: '#7f98a9' }}>
                <span style={{ background: '#7f98a9', boxShadow: 'none' }} />
                PROTOTYPE
              </span>
            )}
          </p>
          <h2>{copy.title}</h2>
          <p className="nw-muted">{copy.description}</p>
        </div>

        {isConnected ? (
          <div
            className="nw-prototype-badge"
            style={{
              color: statusData ? 'var(--green)' : 'var(--amber)',
              borderColor: statusData ? 'rgba(77,214,154,.3)' : 'rgba(244,181,104,.3)',
            }}
          >
            <Radio size={14} />
            {statusData ? 'Live Backend Connected' : 'Connecting to Backend...'}
          </div>
        ) : (
          <div className="nw-prototype-badge">
            <LockKeyhole size={14} /> Local prototype · backend unchanged
          </div>
        )}
      </div>

      {page === 'topology' && <TopologyBuilder />}
      {page === 'live' && (
        <LiveNetwork
          statusData={statusData}
          onRefresh={onRefresh}
        />
      )}
      {page === 'hosts' && (
        <HostMonitoring
          statusData={statusData}
          onRefresh={onRefresh}
          loading={loading}
        />
      )}
      {page === 'intelligence' && (
        <NetworkIntelligence
          statusData={statusData}
          onRefresh={onRefresh}
        />
      )}
      {page === 'experiments' && (
        <ExperimentCenter
          statusData={statusData}
          onRefresh={onRefresh}
        />
      )}
      {page === 'incidents' && (
        <IncidentManagement
          statusData={statusData}
          onRefresh={onRefresh}
        />
      )}
    </>
  )
}

type NodeKind = 'host' | 'sender' | 'receiver' | 'both' | 'switch' | 'router' | 'monitor'
type DeviceData = { label: string; kind: NodeKind; ip?: string; mac?: string; role?: string; telemetry?: boolean; dpid?: string; version?: string; switchType?: string; ports?: string }
type LinkData = { bandwidth: string; delay: string; loss: string; queue: string; status: 'up' | 'down' }
const defaultLink: LinkData = { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' }

const initialNodes: Node<DeviceData>[] = [
  { id: 's1', type: 'device', position: { x: 280, y: 220 }, data: { label: 's1', kind: 'switch', dpid: '0000000000000001', version: '1.3', switchType: 'OpenFlow', ports: '1-5' } },
  { id: 's2', type: 'device', position: { x: 520, y: 220 }, data: { label: 's2', kind: 'switch', dpid: '0000000000000002', version: '1.3', switchType: 'OpenFlow', ports: '1-3' } },
  { id: 'h1', type: 'device', position: { x: 80, y: 70 }, data: { label: 'h1', kind: 'host', ip: '10.0.0.1', mac: '00:00:00:00:00:01', role: 'Sender / Host', telemetry: true } },
  { id: 'h2', type: 'device', position: { x: 80, y: 170 }, data: { label: 'h2', kind: 'host', ip: '10.0.0.2', mac: '00:00:00:00:00:02', role: 'Sender / Host', telemetry: true } },
  { id: 'h3', type: 'device', position: { x: 80, y: 270 }, data: { label: 'h3', kind: 'host', ip: '10.0.0.3', mac: '00:00:00:00:00:03', role: 'Sender / Host', telemetry: true } },
  { id: 'h4', type: 'device', position: { x: 80, y: 370 }, data: { label: 'h4', kind: 'host', ip: '10.0.0.4', mac: '00:00:00:00:00:04', role: 'Sender / Host', telemetry: true } },
  { id: 'h5', type: 'device', position: { x: 720, y: 140 }, data: { label: 'h5', kind: 'monitor', ip: '10.0.0.5', mac: '00:00:00:00:00:05', role: 'Monitoring Server', telemetry: false } },
  { id: 'h6', type: 'device', position: { x: 720, y: 300 }, data: { label: 'h6', kind: 'host', ip: '10.0.0.6', mac: '00:00:00:00:00:06', role: 'Receiver / Host', telemetry: false } },
]

const initialEdges: Edge<LinkData>[] = [
  { id: 'h1-s1', source: 'h1', target: 's1', type: 'smoothstep', label: '10 Mbps · 1 ms', data: { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' } },
  { id: 'h2-s1', source: 'h2', target: 's1', type: 'smoothstep', label: '10 Mbps · 1 ms', data: { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' } },
  { id: 'h3-s1', source: 'h3', target: 's1', type: 'smoothstep', label: '10 Mbps · 1 ms', data: { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' } },
  { id: 'h4-s1', source: 'h4', target: 's1', type: 'smoothstep', label: '10 Mbps · 1 ms', data: { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' } },
  { id: 's1-s2', source: 's1', target: 's2', type: 'smoothstep', label: '5 Mbps · 5 ms (Bottleneck)', data: { bandwidth: '5', delay: '5', loss: '0', queue: '1000', status: 'up' } },
  { id: 's2-h5', source: 's2', target: 'h5', type: 'smoothstep', label: '10 Mbps · 1 ms', data: { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' } },
  { id: 's2-h6', source: 's2', target: 'h6', type: 'smoothstep', label: '10 Mbps · 1 ms', data: { bandwidth: '10', delay: '1', loss: '0', queue: '1000', status: 'up' } },
]

function DeviceNode({ data, selected }: NodeProps<Node<DeviceData>>) { return <div className={`nw-flow-node ${data.kind} ${selected ? 'selected' : ''}`}><Handle type="target" position={Position.Top} /><span>{data.kind === 'switch' || data.kind === 'router' ? <Network size={17} /> : data.kind === 'monitor' ? <Radio size={17} /> : <Server size={17} />}</span><strong>{data.label}</strong><small>{data.ip ?? data.kind}</small><Handle type="source" position={Position.Bottom} /></div> }
const nodeTypes = { device: DeviceNode }

function TopologyBuilder() {
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges)
  const [selectedNode, setSelectedNode] = useState<string | null>('s1')
  const [selectedEdge, setSelectedEdge] = useState<string | null>(null)
  const [history, setHistory] = useState<{ nodes: Node<DeviceData>[]; edges: Edge<LinkData>[] }[]>([])
  const [future, setFuture] = useState<typeof history>([])
  const [draftName, setDraftName] = useState('mininet-sdn')
  const [message, setMessage] = useState('')
  const importInput = useRef<HTMLInputElement>(null)
  const selected = nodes.find((node) => node.id === selectedNode)
  const selectedLink = edges.find((edge) => edge.id === selectedEdge)
  const snapshot = () => setHistory((past) => [...past.slice(-19), { nodes, edges }])
  const connect = useCallback((connection: Connection) => { if (!connection.source || !connection.target || connection.source === connection.target) return setMessage('Invalid connection: choose two different devices.'); const duplicate = edges.some((edge) => (edge.source === connection.source && edge.target === connection.target) || (edge.source === connection.target && edge.target === connection.source)); if (duplicate) return setMessage('That connection already exists.'); snapshot(); const id = `${connection.source}-${connection.target}-${Date.now()}`; setEdges((current) => addEdge({ ...connection, id, type: 'smoothstep', label: '10 Mbps · 1 ms', data: { ...defaultLink } }, current)); setFuture([]) }, [nodes, edges, setEdges])
  const addDevice = (kind: NodeKind) => { snapshot(); const prefix = kind === 'switch' ? 's' : 'h'; let count = nodes.filter((node) => node.data.kind === kind).length + 1; const labels = new Set(nodes.map((node) => node.data.label)); while (labels.has(`${prefix}${count}`)) count += 1; const id = `${kind}-${Date.now()}`; setNodes((current) => [...current, { id, type: 'device', position: { x: 200 + (current.length % 3) * 220, y: 100 + (current.length % 2) * 170 }, data: { label: `${prefix}${count}`, kind, ip: kind === 'host' ? `10.0.0.${count + 2}` : undefined, role: kind === 'host' ? 'Host' : undefined, telemetry: true, dpid: kind === 'switch' ? `000000000000000${count}` : undefined, version: kind === 'switch' ? '1.3' : undefined, switchType: kind === 'switch' ? 'OpenFlow' : undefined, ports: kind === 'switch' ? '1-4' : undefined } }]); setFuture([]) }
  const updateNode = (patch: Partial<DeviceData>) => { if (!selectedNode) return; snapshot(); setNodes((current) => current.map((node) => node.id === selectedNode ? { ...node, data: { ...node.data, ...patch } } : node)); setFuture([]) }
  const updateLink = (patch: Partial<LinkData>) => { if (!selectedEdge) return; snapshot(); setEdges((current) => current.map((edge) => edge.id === selectedEdge ? { ...edge, data: { ...edge.data!, ...patch }, label: `${patch.bandwidth ?? edge.data?.bandwidth} Mbps · ${patch.delay ?? edge.data?.delay} ms` } : edge)); setFuture([]) }
  const deleteSelected = () => { snapshot(); if (selectedEdge) { setEdges((current) => current.filter((edge) => edge.id !== selectedEdge)); setSelectedEdge(null) } else if (selectedNode) { setNodes((current) => current.filter((node) => node.id !== selectedNode)); setEdges((current) => current.filter((edge) => edge.source !== selectedNode && edge.target !== selectedNode)); setSelectedNode(null) } setFuture([]) }
  const undo = () => { const previous = history.at(-1); if (!previous) return; setFuture((next) => [...next, { nodes, edges }]); setNodes(previous.nodes); setEdges(previous.edges); setHistory((past) => past.slice(0, -1)) }
  const redo = () => { const next = future.at(-1); if (!next) return; setHistory((past) => [...past, { nodes, edges }]); setNodes(next.nodes); setEdges(next.edges); setFuture((past) => past.slice(0, -1)) }
  const save = () => { const saved = JSON.parse(localStorage.getItem('netwatch-topologies') ?? '{}'); saved[draftName] = { nodes, edges, savedAt: new Date().toISOString() }; localStorage.setItem('netwatch-topologies', JSON.stringify(saved)); setMessage(`Saved ${draftName} locally`) }
  const exportJson = () => { const blob = new Blob([JSON.stringify({ name: draftName, nodes, edges }, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${draftName}.json`; anchor.click(); URL.revokeObjectURL(url); setMessage('Exported topology JSON') }
  const loadDraft = () => { const saved = JSON.parse(localStorage.getItem('netwatch-topologies') ?? '{}'); const names = Object.keys(saved); if (!names.length) return setMessage('No saved drafts found'); const name = window.prompt(`Load draft: ${names.join(', ')}`, draftName); if (!name || !saved[name]) return; setDraftName(name); setNodes(saved[name].nodes); setEdges(saved[name].edges); setMessage(`Loaded ${name}`) }
  const clearCanvas = () => { if (!window.confirm('Clear this local draft? This cannot be undone.')) return; snapshot(); setNodes([]); setEdges([]); setSelectedNode(null); setSelectedEdge(null); setMessage('Canvas cleared locally') }
  const importJson = async (event: React.ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (!file) return; try { const parsed = JSON.parse(await file.text()); if (!Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) throw new Error('Expected nodes and edges arrays.'); snapshot(); setNodes(parsed.nodes); setEdges(parsed.edges); if (typeof parsed.name === 'string') setDraftName(parsed.name); setMessage(`Imported ${parsed.name ?? file.name}`) } catch (reason) { setMessage(reason instanceof Error ? `Import failed: ${reason.message}` : 'Import failed: invalid JSON') } finally { event.target.value = '' } }
  const validate = () => { const issues: string[] = []; const names = new Set<string>(); const dpids = new Set<string>(); nodes.forEach((node) => { if (names.has(node.data.label)) issues.push('Duplicate device names detected.'); names.add(node.data.label); if (node.data.dpid && dpids.has(node.data.dpid)) issues.push('Duplicate switch DPIDs detected.'); if (node.data.dpid) dpids.add(node.data.dpid); if (node.data.kind === 'switch' && !edges.some((edge) => edge.source === node.id || edge.target === node.id)) issues.push(`${node.data.label} has no connections.`); if (node.data.ip && !/^\d{1,3}(\.\d{1,3}){3}$/.test(node.data.ip)) issues.push(`${node.data.label} has an invalid IP address.`) }); edges.forEach((edge) => { const link = edge.data ?? defaultLink; if (Number(link.bandwidth) <= 0) issues.push(`Link ${edge.id} has invalid bandwidth.`); if (Number(link.loss) < 0 || Number(link.loss) > 100) issues.push(`Link ${edge.id} has invalid packet loss.`); if (Number(link.queue) < 0) issues.push(`Link ${edge.id} has invalid queue size.`) }); setMessage(issues.length ? `${issues.length} validation issue${issues.length > 1 ? 's' : ''}: ${issues[0]}` : 'Topology valid: no blocking issues found') }

  const loadLiveTopology = async () => {
    setMessage('Querying live configured SDN topology from backend...')
    try {
      const topo = await fetchTopology()
      if (!topo || !topo.switches || !topo.hosts || !topo.links) {
        throw new Error('Invalid topology response received from backend.')
      }
      snapshot()
      const newNodes: Node<DeviceData>[] = []
      topo.switches.forEach((sw) => {
        newNodes.push({
          id: sw.id,
          type: 'device',
          position: { x: sw.id === 's1' ? 280 : 520, y: 220 },
          data: {
            label: sw.label,
            kind: 'switch',
            dpid: sw.dpid,
            version: '1.3',
            switchType: 'OpenFlow',
            ports: sw.ports ? `1-${sw.ports.length}` : undefined,
          },
        })
      })
      topo.hosts.forEach((h) => {
        let x = 80
        let y = 70
        if (h.id === 'h1') { x = 80; y = 70 }
        else if (h.id === 'h2') { x = 80; y = 170 }
        else if (h.id === 'h3') { x = 80; y = 270 }
        else if (h.id === 'h4') { x = 80; y = 370 }
        else if (h.id === 'h5') { x = 720; y = 140 }
        else if (h.id === 'h6') { x = 720; y = 300 }
        newNodes.push({
          id: h.id,
          type: 'device',
          position: { x, y },
          data: {
            label: h.label,
            kind: h.id === 'h5' ? 'monitor' : 'host',
            ip: h.ip,
            mac: h.mac,
            role: h.role,
            telemetry: ['h1', 'h2', 'h3', 'h4'].includes(h.id),
          },
        })
      })
      const newEdges: Edge<LinkData>[] = topo.links.map((link) => ({
        id: link.id,
        source: link.source,
        target: link.target,
        type: 'smoothstep',
        label: `${link.capacity_mbps} Mbps · ${link.delay}${link.is_bottleneck ? ' (Bottleneck)' : ''}`,
        data: {
          bandwidth: String(link.capacity_mbps),
          delay: link.delay.replace('ms', ''),
          loss: '0',
          queue: '1000',
          status: 'up',
        },
      }))
      setNodes(newNodes)
      setEdges(newEdges)
      setDraftName('mininet-live-sdn')
      setMessage(`Successfully loaded configured Mininet SDN topology (${topo.switches.length} switches, ${topo.hosts.length} hosts, ${topo.links.length} links)`)
    } catch (err: unknown) {
      setMessage(err instanceof Error ? `Failed to load live topology: ${err.message}` : 'Failed to load live topology.')
    }
  }

  useEffect(() => { const saved = localStorage.getItem('netwatch-topologies'); if (saved) setMessage('Saved drafts available locally') }, [])
  return <div className="nw-topology-editor"><div className="nw-topology-toolbar"><input className="nw-draft-name" value={draftName} onChange={(event) => setDraftName(event.target.value)} aria-label="Topology draft name" /><div className="nw-toolbar-group"><button onClick={() => addDevice('host')}><Plus size={13} /> Host</button><button onClick={() => addDevice('switch')}><Plus size={13} /> Switch</button><button onClick={() => addDevice('router')}><Plus size={13} /> Router</button><button onClick={() => addDevice('monitor')}><Plus size={13} /> Monitor</button></div><div className="nw-toolbar-group"><button onClick={loadLiveTopology} style={{ color: 'var(--cyan)', borderColor: 'rgba(89,216,255,.4)' }}><Network size={13} /> Load Live SDN Topology</button><button onClick={undo} disabled={!history.length}><Undo2 size={13} /></button><button onClick={redo} disabled={!future.length}><Redo2 size={13} /></button><button onClick={deleteSelected} disabled={!selectedNode && !selectedEdge}><Trash2 size={13} /> Delete</button><button onClick={validate}><Check size={13} /> Validate</button></div><div className="nw-toolbar-group"><button onClick={save}><Save size={13} /> Save</button><button onClick={loadDraft}><Upload size={13} /> Load</button><button onClick={() => importInput.current?.click()}><Upload size={13} /> Import</button><input ref={importInput} type="file" accept="application/json" hidden onChange={importJson} /><button onClick={exportJson}><Download size={13} /> Export</button><button onClick={clearCanvas}><Trash2 size={13} /> Clear</button></div></div><div className="nw-topology-workspace"><aside className="nw-component-panel nw-panel"><PanelHeading title="Components" icon={<Boxes size={16} />} meta="DRAG TO ADD" /><div className="nw-component-list">{[['host', 'Host'], ['sender', 'Sender host'], ['receiver', 'Receiver host'], ['both', 'Sender + receiver'], ['switch', 'OpenFlow switch'], ['router', 'Router'], ['monitor', 'Monitoring server']].map(([kind, label]) => <button key={kind} onClick={() => addDevice(kind as NodeKind)}><span className={`nw-component-icon ${kind}`}><Server size={15} /></span><div><strong>{label}</strong><small>{kind === 'switch' ? 'OpenFlow device' : 'Network endpoint'}</small></div><Plus size={13} /></button>)}</div><div className="nw-draft-state"><CircleDot size={13} /><div><strong>Local draft</strong><span>Not deployed to Mininet</span></div></div><div className="nw-topology-counts"><span>{nodes.filter((node) => ['host', 'sender', 'receiver', 'both'].includes(node.data.kind)).length} hosts</span><span>{nodes.filter((node) => node.data.kind === 'switch').length} switches</span><span>{edges.length} links</span></div></aside><div className="nw-flow-canvas"><ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={connect} onNodeClick={(_, node) => { setSelectedNode(node.id); setSelectedEdge(null) }} onEdgeClick={(_, edge) => { setSelectedEdge(edge.id); setSelectedNode(null) }} onPaneClick={() => { setSelectedNode(null); setSelectedEdge(null) }} fitView><Background color="#284052" gap={22} size={1} /><Controls showInteractive={false} /><MiniMap nodeColor={(node) => node.data?.kind === 'switch' ? '#59d8ff' : '#9d8fff'} /></ReactFlow><div className="nw-flow-legend"><span><i className="up" /> Up</span><span><i className="down" /> Down</span><span><i className="congested" /> Congested</span></div></div><aside className="nw-properties-panel nw-panel"><PanelHeading title={selectedLink ? 'Link properties' : 'Properties'} icon={<SlidersHorizontal size={16} />} meta={selectedLink ? 'SELECTED LINK' : 'SELECTED DEVICE'} />{selectedLink ? <LinkProperties link={selectedLink} onChange={updateLink} /> : selected ? <NodeProperties node={selected} onChange={updateNode} /> : <div className="nw-empty"><CircleDot /><strong>Nothing selected</strong><span>Select a node or link to edit its configuration.</span></div>}<div className="nw-properties-footer"><button onClick={save}><Save size={13} /> Save draft</button><button onClick={exportJson}><Download size={13} /> Export JSON</button></div></aside></div>{message && <div className="nw-editor-status"><span>{message}</span><button onClick={() => setMessage('')} aria-label="Dismiss status"><X size={13} /></button></div>}</div>
}
function NodeProperties({ node, onChange }: { node: Node<DeviceData>; onChange: (patch: Partial<DeviceData>) => void }) { const data = node.data; return <div className="nw-form nw-property-form"><label>Name<input value={data.label} onChange={(event) => onChange({ label: event.target.value })} /></label>{['host', 'sender', 'receiver', 'both'].includes(data.kind) && <><label>IP address<input value={data.ip ?? ''} placeholder="10.0.0.1" onChange={(event) => onChange({ ip: event.target.value })} /></label><label>MAC address<input value={data.mac ?? ''} placeholder="00:00:00:00:00:01" onChange={(event) => onChange({ mac: event.target.value })} /></label><label>Role<select value={data.role ?? 'Host'} onChange={(event) => onChange({ role: event.target.value })}><option>Host</option><option>Sender</option><option>Receiver</option><option>Both</option></select></label><label className="nw-switch-field">Telemetry enabled<input type="checkbox" checked={data.telemetry !== false} onChange={(event) => onChange({ telemetry: event.target.checked })} /></label></>}{data.kind === 'switch' && <><label>Switch ID / DPID<input value={data.dpid ?? ''} onChange={(event) => onChange({ dpid: event.target.value })} /></label><label>OpenFlow version<select value={data.version ?? '1.3'} onChange={(event) => onChange({ version: event.target.value })}><option>1.3</option><option>1.0</option><option>1.5</option></select></label><label>Switch type<select value={data.switchType ?? 'OpenFlow'} onChange={(event) => onChange({ switchType: event.target.value })}><option>OpenFlow</option><option>OVS</option><option>Router</option></select></label><label>Port configuration<input value={data.ports ?? ''} placeholder="1-4" onChange={(event) => onChange({ ports: event.target.value })} /></label></>}</div> }
function LinkProperties({ link, onChange }: { link: Edge<LinkData>; onChange: (patch: Partial<LinkData>) => void }) { const data = link.data ?? defaultLink; return <div className="nw-form nw-property-form"><div className="nw-link-endpoints"><span>{link.source}</span><Link2 size={12} /><span>{link.target}</span></div><label>Bandwidth (Mbps)<select value={data.bandwidth} onChange={(event) => onChange({ bandwidth: event.target.value })}><option>1</option><option>5</option><option>10</option><option>50</option><option>100</option><option>1000</option></select></label><label>Custom bandwidth<input type="number" min="0.1" value={data.bandwidth} onChange={(event) => onChange({ bandwidth: event.target.value })} /></label><label>Delay (ms)<input type="number" min="0" value={data.delay} onChange={(event) => onChange({ delay: event.target.value })} /></label><label>Packet loss (%)<input type="number" min="0" max="100" value={data.loss} onChange={(event) => onChange({ loss: event.target.value })} /></label><label>Queue size (packets)<input type="number" min="0" value={data.queue} onChange={(event) => onChange({ queue: event.target.value })} /></label><label>Status<select value={data.status} onChange={(event) => onChange({ status: event.target.value as LinkData['status'] })}><option value="up">Up</option><option value="down">Down</option></select></label></div> }

interface LiveNodeDef {
  id: string
  label: string
  kind: 'switch' | 'host' | 'monitor'
  x: number
  y: number
  ip?: string
  dpid?: string
  sub?: string
}

const LIVE_NODES: LiveNodeDef[] = [
  { id: 's1', label: 's1', kind: 'switch', x: 310, y: 230, dpid: '0000000000000001', sub: '5 Ports · OF 1.3' },
  { id: 's2', label: 's2', kind: 'switch', x: 530, y: 230, dpid: '0000000000000002', sub: '3 Ports · OF 1.3' },
  { id: 'h1', label: 'h1', kind: 'host', x: 105, y: 75, ip: '10.0.0.1', sub: 'Sender / Host' },
  { id: 'h2', label: 'h2', kind: 'host', x: 105, y: 175, ip: '10.0.0.2', sub: 'Sender / Host' },
  { id: 'h3', label: 'h3', kind: 'host', x: 105, y: 275, ip: '10.0.0.3', sub: 'Sender / Host' },
  { id: 'h4', label: 'h4', kind: 'host', x: 105, y: 375, ip: '10.0.0.4', sub: 'Sender / Host' },
  { id: 'h5', label: 'h5 (Server)', kind: 'monitor', x: 735, y: 145, ip: '10.0.0.5', sub: 'UDP 9999 Srv' },
  { id: 'h6', label: 'h6 (Sink)', kind: 'host', x: 735, y: 305, ip: '10.0.0.6', sub: 'Traffic Sink' },
]

interface LiveLinkDef {
  id: string
  src: string
  dst: string
  srcPort: string
  dstPort: string
  capacity: number
  delay: number
  bottleneck: boolean
}

const LIVE_LINKS: LiveLinkDef[] = [
  { id: 'h1-s1', src: 'h1', dst: 's1', srcPort: 'eth0', dstPort: 's1-eth1', capacity: 100, delay: 1, bottleneck: false },
  { id: 'h2-s1', src: 'h2', dst: 's1', srcPort: 'eth0', dstPort: 's1-eth2', capacity: 100, delay: 1, bottleneck: false },
  { id: 'h3-s1', src: 'h3', dst: 's1', srcPort: 'eth0', dstPort: 's1-eth3', capacity: 100, delay: 1, bottleneck: false },
  { id: 'h4-s1', src: 'h4', dst: 's1', srcPort: 'eth0', dstPort: 's1-eth4', capacity: 100, delay: 1, bottleneck: false },
  { id: 's1-s2', src: 's1', dst: 's2', srcPort: 's1-eth5', dstPort: 's2-eth1', capacity: 5, delay: 5, bottleneck: true },
  { id: 's2-h5', src: 's2', dst: 'h5', srcPort: 's2-eth2', dstPort: 'eth0', capacity: 100, delay: 1, bottleneck: false },
  { id: 's2-h6', src: 's2', dst: 'h6', srcPort: 's2-eth3', dstPort: 'eth0', capacity: 100, delay: 1, bottleneck: false },
]

function LiveNetwork({
  statusData,
  onRefresh,
}: {
  statusData?: NetwatchStatus | null
  onRefresh?: () => void
}) {
  const [linksResponse, setLinksResponse] = useState<NetworkLinksResponse | null>(null)
  const [flowsResponse, setFlowsResponse] = useState<NetworkFlowsResponse | null>(null)
  const [switchFilter, setSwitchFilter] = useState<'all' | 's1' | 's2'>('all')
  const [selectedId, setSelectedId] = useState<{ type: 'node' | 'link'; id: string } | null>(null)
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null)

  const pollLinks = useCallback(async () => {
    try {
      const data = await fetchNetworkLinks()
      setLinksResponse(data)
      setLastRefreshed(new Date())
    } catch {
      // Keep previous data on transient poll failure
    }
  }, [])

  const pollFlows = useCallback(async () => {
    try {
      const data = await fetchNetworkFlows()
      setFlowsResponse(data)
    } catch {
      // Keep previous data on transient poll failure
    }
  }, [])

  useEffect(() => {
    pollLinks()
    pollFlows()
    const timer = setInterval(() => {
      pollLinks()
      pollFlows()
    }, 3000)
    return () => clearInterval(timer)
  }, [pollLinks, pollFlows])

  const filteredFlows = useMemo(() => {
    if (!flowsResponse?.flows) return []
    if (switchFilter === 'all') return flowsResponse.flows
    return flowsResponse.flows.filter((f) => f.switch === switchFilter || f.dpid === (switchFilter === 's1' ? 1 : 2))
  }, [flowsResponse, switchFilter])

  const nodeMap = useMemo(() => new Map(LIVE_NODES.map((n) => [n.id, n])), [])
  const statsMap = useMemo(() => {
    const map = new Map<string, LinkLiveStats>()
    if (linksResponse?.links) {
      for (const [id, stats] of Object.entries(linksResponse.links)) {
        map.set(id, stats)
      }
    }
    return map
  }, [linksResponse])

  const bottleneckStats = statsMap.get('s1-s2')
  const bottleneckUtil = bottleneckStats?.utilization ?? (typeof statusData?.utilization === 'number' ? statusData.utilization : null)
  const isBottleneckCongested = (bottleneckUtil ?? 0) > 80

  const activeTelemetryHosts = statusData?.telemetry ? Object.keys(statusData.telemetry).length : 0

  const selectedNode = selectedId?.type === 'node' ? nodeMap.get(selectedId.id) : null
  const selectedLink = selectedId?.type === 'link' ? LIVE_LINKS.find((l) => l.id === selectedId.id) : null
  const selectedLinkStats = selectedLink ? statsMap.get(selectedLink.id) : null

  return (
    <>
      <div className="nw-live-layout">
      <div className="nw-panel nw-live-map">
        <PanelHeading
          title="Mininet Live SDN Topology"
          icon={<Network size={16} />}
          meta="8 NODES · 7 CONFIGURED LINKS · OPENFLOW 1.3"
        />

        <div className="nw-live-canvas" style={{ minHeight: '490px', padding: 0 }}>
          <svg
            viewBox="0 0 840 460"
            style={{ width: '100%', height: '100%', display: 'block' }}
          >
            <defs>
              <linearGradient id="bottleneckGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                <stop offset="0%" stopColor="#f4b568" stopOpacity="0.8" />
                <stop offset="50%" stopColor={isBottleneckCongested ? '#f07879' : '#59d8ff'} stopOpacity="1" />
                <stop offset="100%" stopColor="#f4b568" stopOpacity="0.8" />
              </linearGradient>
            </defs>

            {/* Links Layer */}
            {LIVE_LINKS.map((link) => {
              const srcNode = nodeMap.get(link.src)
              const dstNode = nodeMap.get(link.dst)
              if (!srcNode || !dstNode) return null

              const stats = statsMap.get(link.id)
              const isMeasured = stats?.measured === true && typeof stats?.throughput_mbps === 'number'
              const throughput = isMeasured ? (stats?.throughput_mbps ?? 0) : null
              const util = isMeasured ? (stats?.utilization ?? 0) : null
              const isSelected = selectedId?.type === 'link' && selectedId.id === link.id

              const hasActiveTraffic = isMeasured && throughput !== null && throughput > 0.05
              const animSpeed = hasActiveTraffic
                ? `${Math.max(0.35, 2.2 - ((util ?? 0) / 100) * 1.8).toFixed(2)}s`
                : '0s'
              const flowColor = (util ?? 0) > 80 ? '#f07879' : (util ?? 0) > 40 ? '#f4b568' : '#59d8ff'

              const midX = (srcNode.x + dstNode.x) / 2
              const midY = (srcNode.y + dstNode.y) / 2 + (link.bottleneck ? -14 : (srcNode.y > dstNode.y ? 12 : -12))

              return (
                <g key={link.id} style={{ cursor: 'pointer' }} onClick={() => setSelectedId({ type: 'link', id: link.id })}>
                  {/* Underlay Click Target & Base Line */}
                  <line
                    x1={srcNode.x}
                    y1={srcNode.y}
                    x2={dstNode.x}
                    y2={dstNode.y}
                    stroke="transparent"
                    strokeWidth={20}
                  />
                  <line
                    x1={srcNode.x}
                    y1={srcNode.y}
                    x2={dstNode.x}
                    y2={dstNode.y}
                    stroke={
                      isSelected
                        ? '#59d8ff'
                        : link.bottleneck
                        ? 'url(#bottleneckGrad)'
                        : 'rgba(89, 216, 255, 0.22)'
                    }
                    strokeWidth={isSelected ? (link.bottleneck ? 5 : 3.5) : link.bottleneck ? 4 : 2}
                    strokeDasharray={link.bottleneck ? 'none' : '4 3'}
                  />

                  {/* Directional Traffic Animation (Active only when throughput > 0.05 Mbps) */}
                  {hasActiveTraffic && (
                    <line
                      x1={srcNode.x}
                      y1={srcNode.y}
                      x2={dstNode.x}
                      y2={dstNode.y}
                      stroke={flowColor}
                      strokeWidth={link.bottleneck ? 3.5 : 2.5}
                      strokeDasharray="6 6"
                      style={{
                        animation: `sdnDashFlow ${animSpeed} linear infinite`,
                      }}
                    />
                  )}

                  {/* Midpoint Info Badge */}
                  <g transform={`translate(${midX}, ${midY})`}>
                    {link.bottleneck ? (
                      <g>
                        <rect
                          x={-75}
                          y={-14}
                          width={150}
                          height={28}
                          rx={5}
                          fill="#0b1e2c"
                          stroke={isSelected ? '#59d8ff' : isBottleneckCongested ? '#f07879' : '#f4b568'}
                          strokeWidth={isSelected ? 2 : 1}
                        />
                        <text
                          x={0}
                          y={-2}
                          textAnchor="middle"
                          fill="#f4b568"
                          fontSize="9"
                          fontWeight="700"
                          letterSpacing="0.05em"
                        >
                          5 Mbps BOTTLENECK
                        </text>
                        <text
                          x={0}
                          y={9}
                          textAnchor="middle"
                          fill={isMeasured ? '#e0f0fa' : '#708899'}
                          fontSize="9"
                          fontFamily="monospace"
                        >
                          {isMeasured
                            ? `${throughput!.toFixed(2)} Mbps · ${util!.toFixed(1)}%`
                            : 'Unmeasured / Idle'}
                        </text>
                      </g>
                    ) : (
                      <g>
                        <rect
                          x={-46}
                          y={-9}
                          width={92}
                          height={18}
                          rx={4}
                          fill="#081724"
                          stroke={isSelected ? '#59d8ff' : 'rgba(89,216,255,0.2)'}
                          strokeWidth={isSelected ? 1.5 : 1}
                        />
                        <text
                          x={0}
                          y={3}
                          textAnchor="middle"
                          fill={isMeasured ? '#b2e4f7' : '#5f798a'}
                          fontSize="8.5"
                          fontFamily="monospace"
                        >
                          {isMeasured ? `${throughput!.toFixed(2)} Mbps` : '10M · Unmeas'}
                        </text>
                      </g>
                    )}
                  </g>
                </g>
              )
            })}

            {/* Nodes Layer */}
            {LIVE_NODES.map((node) => {
              const isSelected = selectedId?.type === 'node' && selectedId.id === node.id
              const isSwitch = node.kind === 'switch'
              const isServer = node.id === 'h5'
              const hostTelemetry = statusData?.telemetry?.[node.id]
              const hostStatus = hostTelemetry?.status ?? (isServer ? 'SRV' : 'IDLE')
              const statusDotColor =
                hostStatus === 'OK'
                  ? 'var(--green)'
                  : hostStatus === 'WARN'
                  ? 'var(--amber)'
                  : hostStatus === 'DOWN'
                  ? 'var(--red)'
                  : '#5d7687'

              const w = isSwitch ? 82 : isServer ? 88 : 76
              const h = isSwitch ? 52 : 46

              return (
                <g
                  key={node.id}
                  transform={`translate(${node.x}, ${node.y})`}
                  style={{ cursor: 'pointer' }}
                  onClick={() => setSelectedId({ type: 'node', id: node.id })}
                >
                  {/* Node Box */}
                  <rect
                    x={-w / 2}
                    y={-h / 2}
                    width={w}
                    height={h}
                    rx={7}
                    fill={isSwitch ? '#0e2334' : isServer ? '#132030' : '#0c1a27'}
                    stroke={
                      isSelected
                        ? 'var(--cyan)'
                        : isSwitch
                        ? '#3b6a88'
                        : isServer
                        ? '#506782'
                        : '#2c4355'
                    }
                    strokeWidth={isSelected ? 2.5 : 1.2}
                  />

                  {/* Node Label */}
                  <text
                    x={isSwitch ? 0 : -w / 2 + 18}
                    y={isSwitch ? -7 : -4}
                    textAnchor={isSwitch ? 'middle' : 'start'}
                    fill={isSwitch ? '#75d6ff' : isServer ? '#a5caff' : '#c5d8e4'}
                    fontSize="11"
                    fontWeight="700"
                  >
                    {node.label}
                  </text>

                  {/* Subtext */}
                  <text
                    x={isSwitch ? 0 : -w / 2 + 18}
                    y={isSwitch ? 9 : 9}
                    textAnchor={isSwitch ? 'middle' : 'start'}
                    fill="#6d889b"
                    fontSize="8.5"
                    fontFamily="monospace"
                  >
                    {node.ip ?? (isSwitch ? (node.id === 's1' ? 'OF 1.3 · s1' : 'OF 1.3 · s2') : node.sub)}
                  </text>

                  {/* Status Indicator Dot for hosts */}
                  {!isSwitch && (
                    <circle
                      cx={-w / 2 + 9}
                      cy={-3}
                      r={3.5}
                      fill={statusDotColor}
                    />
                  )}

                  {/* Ports indicator for switches */}
                  {isSwitch && (
                    <text
                      x={0}
                      y={18}
                      textAnchor="middle"
                      fill="#4a687c"
                      fontSize="7.5"
                    >
                      {node.sub}
                    </text>
                  )}
                </g>
              )
            })}
          </svg>

          {/* Aggregate traffic disclaimer banner */}
          <div
            className="nw-animation-legend"
            style={{
              position: 'absolute',
              bottom: '12px',
              left: '14px',
              background: 'rgba(8, 20, 31, 0.88)',
              padding: '6px 10px',
              borderRadius: '5px',
              border: '1px solid var(--line)',
              fontSize: '9.5px',
              color: '#7a96a8',
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
            }}
          >
            <span
              className="nw-packet"
              style={{
                width: '6px',
                height: '6px',
                borderRadius: '50%',
                background: 'var(--cyan)',
                boxShadow: '0 0 6px var(--cyan)',
              }}
            />
            <span>
              <strong style={{ color: '#b8d5e5' }}>Aggregate link throughput</strong> · Not individual packet capture (OpenFlow port counter deltas)
            </span>
          </div>

          {/* Polling frequency badge */}
          <div
            style={{
              position: 'absolute',
              top: '12px',
              right: '14px',
              fontSize: '9px',
              color: '#5b7688',
              display: 'flex',
              alignItems: 'center',
              gap: '5px',
            }}
          >
            <CircleDot size={9} style={{ color: 'var(--green)' }} />
            <span>OpenFlow polling: 3.0s</span>
          </div>
        </div>
      </div>

      {/* Right Side Inspector & Network State */}
      <div className="nw-live-side">
        <div className="nw-panel">
          <PanelHeading
            title={
              selectedNode
                ? `Node: ${selectedNode.label}`
                : selectedLink
                ? `Link: ${selectedLink.id}`
                : 'SDN Operations Overview'
            }
            icon={
              selectedNode?.kind === 'switch' ? (
                <Network size={16} />
              ) : selectedNode ? (
                <Server size={16} />
              ) : selectedLink ? (
                <Link2 size={16} />
              ) : (
                <Radio size={16} />
              )
            }
            meta={selectedId ? 'SELECTED ELEMENT' : 'LIVE NOC STATE'}
          />

          {selectedNode && (
            <div style={{ padding: '16px', display: 'grid', gap: '12px', fontSize: '11px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <span className="nw-muted">Device ID:</span>
                <strong style={{ color: '#e0f0fa' }}>{selectedNode.id}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <span className="nw-muted">Kind:</span>
                <span style={{ textTransform: 'capitalize', color: 'var(--cyan)' }}>{selectedNode.kind}</span>
              </div>
              {selectedNode.ip && (
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                  <span className="nw-muted">IP Address:</span>
                  <span style={{ fontFamily: 'monospace' }}>{selectedNode.ip}</span>
                </div>
              )}
              {selectedNode.dpid && (
                <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                  <span className="nw-muted">Datapath ID:</span>
                  <span style={{ fontFamily: 'monospace', fontSize: '10px' }}>{selectedNode.dpid}</span>
                </div>
              )}

              {/* Host Telemetry if available */}
              {statusData?.telemetry?.[selectedNode.id] ? (
                <div style={{ background: 'rgba(255,255,255,.02)', padding: '10px', borderRadius: '6px', display: 'grid', gap: '6px' }}>
                  <strong style={{ fontSize: '10px', color: '#9db6c6', textTransform: 'uppercase' }}>Host Telemetry</strong>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">Status:</span>
                    <strong style={{ color: statusData.telemetry[selectedNode.id].status === 'OK' ? 'var(--green)' : 'var(--amber)' }}>
                      {statusData.telemetry[selectedNode.id].status}
                    </strong>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">CPU (VM-wide):</span>
                    <span>{statusData.telemetry[selectedNode.id].cpu?.toFixed(1) ?? '—'}%</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">Memory (VM-wide):</span>
                    <span>{statusData.telemetry[selectedNode.id].mem?.toFixed(1) ?? '—'}%</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">Sequence Gaps:</span>
                    <span>{statusData.telemetry[selectedNode.id].loss !== undefined ? `${(statusData.telemetry[selectedNode.id].loss! * 100).toFixed(1)}%` : '—'}</span>
                  </div>
                </div>
              ) : selectedNode.kind === 'switch' ? (
                <div style={{ background: 'rgba(255,255,255,.02)', padding: '10px', borderRadius: '6px', display: 'grid', gap: '6px' }}>
                  <strong style={{ fontSize: '10px', color: '#9db6c6', textTransform: 'uppercase' }}>OpenFlow 1.3 Switch</strong>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">Controlled By:</span>
                    <span>Ryu Controller</span>
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">Active Ports:</span>
                    <span>{selectedNode.id === 's1' ? 'Ports 1..5 (h1–h4, s2)' : 'Ports 1..3 (s1, h5, h6)'}</span>
                  </div>
                </div>
              ) : (
                <p className="nw-muted" style={{ margin: 0, fontSize: '10.5px' }}>
                  {selectedNode.id === 'h5'
                    ? 'Monitoring server receiving UDP telemetry broadcasts on port 9999.'
                    : 'Traffic receiver / test sink endpoint.'}
                </p>
              )}

              <button
                className="nw-secondary-button"
                onClick={() => setSelectedId(null)}
                style={{ width: '100%', justifyContent: 'center', margin: '4px 0 0' }}
              >
                Clear Selection
              </button>
            </div>
          )}

          {selectedLink && (
            <div style={{ padding: '16px', display: 'grid', gap: '12px', fontSize: '11px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <span className="nw-muted">Link ID:</span>
                <strong style={{ color: '#e0f0fa' }}>{selectedLink.id}</strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <span className="nw-muted">Endpoints:</span>
                <span style={{ fontFamily: 'monospace' }}>
                  {selectedLink.src}:{selectedLink.srcPort} ↔ {selectedLink.dst}:{selectedLink.dstPort}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <span className="nw-muted">Type:</span>
                <strong style={{ color: selectedLink.bottleneck ? '#f4b568' : 'var(--cyan)' }}>
                  {selectedLink.bottleneck ? '5 Mbps Bottleneck Link' : 'Standard Edge Link'}
                </strong>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid var(--line)', paddingBottom: '8px' }}>
                <span className="nw-muted">Capacity:</span>
                <span>{selectedLink.capacity} Mbps · {selectedLink.delay} ms</span>
              </div>

              <div style={{ background: 'rgba(255,255,255,.02)', padding: '10px', borderRadius: '6px', display: 'grid', gap: '6px' }}>
                <strong style={{ fontSize: '10px', color: '#9db6c6', textTransform: 'uppercase' }}>Live Port Telemetry</strong>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span className="nw-muted">Throughput:</span>
                  <strong style={{ color: selectedLinkStats?.measured ? '#59d8ff' : '#708899' }}>
                    {selectedLinkStats?.measured && typeof selectedLinkStats.throughput_mbps === 'number'
                      ? `${selectedLinkStats.throughput_mbps.toFixed(2)} Mbps`
                      : 'Unmeasured (First sample / No delta)'}
                  </strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span className="nw-muted">Utilization:</span>
                  <strong style={{ color: selectedLinkStats?.measured ? ((selectedLinkStats.utilization ?? 0) > 80 ? 'var(--red)' : 'var(--green)') : '#708899' }}>
                    {selectedLinkStats?.measured && typeof selectedLinkStats.utilization === 'number'
                      ? `${selectedLinkStats.utilization.toFixed(1)}%`
                      : 'Unmeasured'}
                  </strong>
                </div>
                {selectedLinkStats?.tx_bytes !== null && selectedLinkStats?.tx_bytes !== undefined && (
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">TX Bytes:</span>
                    <span style={{ fontFamily: 'monospace' }}>{selectedLinkStats.tx_bytes.toLocaleString()} B</span>
                  </div>
                )}
                {typeof selectedLinkStats?.age_seconds === 'number' && (
                  <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                    <span className="nw-muted">Sample Age:</span>
                    <span>{selectedLinkStats.age_seconds.toFixed(1)}s ago</span>
                  </div>
                )}
              </div>

              <button
                className="nw-secondary-button"
                onClick={() => setSelectedId(null)}
                style={{ width: '100%', justifyContent: 'center', margin: '4px 0 0' }}
              >
                Clear Selection
              </button>
            </div>
          )}

          {!selectedId && (
            <div className="nw-state-list">
              <HealthItem
                label="SDN Controller"
                value="Ryu OpenFlow 1.3 Active"
                status="healthy"
              />
              <HealthItem
                label="Model Source"
                value="Configured Mininet (s1, s2, h1–h6)"
                status="healthy"
              />
              <HealthItem
                label="Bottleneck Capacity"
                value="5 Mbps · s1-eth5 ↔ s2-eth1"
                status="healthy"
              />
              <HealthItem
                label="Bottleneck Utilization"
                value={
                  bottleneckUtil !== null
                    ? `${bottleneckUtil.toFixed(1)}% (${isBottleneckCongested ? 'Congested' : 'Normal'})`
                    : 'Idle / Awaiting Traffic'
                }
                status={isBottleneckCongested ? 'critical' : 'healthy'}
              />
              <HealthItem
                label="Telemetry Hosts"
                value={`${activeTelemetryHosts} of 4 Hosts Reporting`}
                status={activeTelemetryHosts >= 4 ? 'healthy' : activeTelemetryHosts > 0 ? 'warning' : 'critical'}
              />

              <div className="nw-info-card" style={{ margin: '8px 0 0' }}>
                <Info size={16} style={{ flexShrink: 0, marginTop: '2px', color: 'var(--cyan)' }} />
                <span>
                  <strong style={{ color: '#d0e5f2' }}>Port Statistics Engine</strong>
                  Ryu samples OpenFlow port statistics via <code>OFPPortStatsRequest</code> every 3.0s. Rates are computed from byte deltas across sample intervals.
                </span>
              </div>

              {onRefresh && (
                <button
                  onClick={onRefresh}
                  className="nw-secondary-button"
                  style={{ width: '100%', justifyContent: 'center', margin: '6px 0 0' }}
                >
                  <RefreshCw size={13} /> Refresh Backend Telemetry
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>

      {/* Priority 3: OpenFlow Flow Table Inspector & Top Talkers */}
      <div style={{ marginTop: '16px', display: 'grid', gridTemplateColumns: 'minmax(0, 1.4fr) minmax(0, 0.8fr)', gap: '15px' }}>
        {/* Left: OpenFlow Flow Table */}
        <div className="nw-panel">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px', borderBottom: '1px solid var(--line)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Network size={16} style={{ color: 'var(--cyan)' }} />
              <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 600, color: '#e0f0fa' }}>OpenFlow 1.3 Flow Table</h3>
              <span className="nw-panel-meta" style={{ marginLeft: '6px' }}>{filteredFlows.length} FLOW ENTRIES</span>
            </div>
            {/* Switch Filter buttons */}
            <div style={{ display: 'flex', gap: '5px' }}>
              {(['all', 's1', 's2'] as const).map((sw) => (
                <button
                  key={sw}
                  onClick={() => setSwitchFilter(sw)}
                  style={{
                    padding: '4px 8px',
                    fontSize: '10px',
                    borderRadius: '5px',
                    border: '1px solid',
                    borderColor: switchFilter === sw ? 'var(--cyan)' : 'var(--line)',
                    background: switchFilter === sw ? 'rgba(89,216,255,0.1)' : 'transparent',
                    color: switchFilter === sw ? 'var(--cyan)' : '#8aa1b0',
                    cursor: 'pointer',
                  }}
                >
                  {sw === 'all' ? 'All Switches' : sw.toUpperCase()}
                </button>
              ))}
            </div>
          </div>

          <div style={{ padding: '12px 16px', maxHeight: '380px', overflowY: 'auto' }}>
            {filteredFlows.length > 0 ? (
              <div style={{ display: 'grid', gap: '10px' }}>
                {filteredFlows.map((flow) => {
                  const isMitigation = flow.is_mitigation || flow.priority === 100
                  const isTableMiss = flow.is_table_miss || flow.priority === 0
                  const badgeColor = isMitigation ? '#f07879' : isTableMiss ? '#7a92a2' : 'var(--cyan)'
                  const badgeBg = isMitigation ? 'rgba(240,120,121,0.12)' : isTableMiss ? 'rgba(255,255,255,0.04)' : 'rgba(89,216,255,0.08)'

                  // Hard timeout countdown
                  const remainingHardTimeout = flow.hard_timeout > 0 ? Math.max(0, flow.hard_timeout - flow.duration_sec) : null

                  return (
                    <div
                      key={flow.id}
                      style={{
                        padding: '10px 12px',
                        borderRadius: '7px',
                        border: `1px solid ${isMitigation ? 'rgba(240,120,121,0.3)' : 'var(--line)'}`,
                        background: isMitigation ? 'rgba(240,120,121,0.04)' : 'rgba(255,255,255,0.015)',
                        display: 'grid',
                        gap: '6px',
                        fontSize: '11px',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                          <span
                            style={{
                              padding: '2px 6px',
                              borderRadius: '4px',
                              fontSize: '9px',
                              fontWeight: 700,
                              color: badgeColor,
                              background: badgeBg,
                              border: `1px solid ${badgeColor}40`,
                            }}
                          >
                            {isMitigation ? 'PRIO 100 · MITIGATION / DROP' : isTableMiss ? 'PRIO 0 · TABLE MISS' : `PRIO ${flow.priority} · FORWARDING`}
                          </span>
                          <span style={{ fontSize: '10px', color: '#6e899b', fontFamily: 'monospace' }}>
                            {flow.switch} (Table {flow.table_id})
                          </span>
                        </div>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '10px' }}>
                          {remainingHardTimeout !== null && (
                            <span style={{ color: '#f07879', fontWeight: 600 }}>
                              Expires in {remainingHardTimeout}s
                            </span>
                          )}
                          <span style={{ color: '#8aa1b0' }}>Duration: {flow.duration_sec}s</span>
                        </div>
                      </div>

                      {/* Matches */}
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px', margin: '3px 0' }}>
                        {Object.entries(flow.match).length > 0 ? (
                          Object.entries(flow.match).map(([k, v]) => (
                            <span
                              key={k}
                              style={{
                                padding: '2px 6px',
                                background: 'rgba(255,255,255,0.03)',
                                borderRadius: '4px',
                                fontSize: '10px',
                                fontFamily: 'monospace',
                                color: '#b8cedb',
                                border: '1px solid rgba(255,255,255,0.05)',
                              }}
                            >
                              <strong style={{ color: '#6e8b9e' }}>{k}:</strong> {v}
                            </span>
                          ))
                        ) : (
                          <span style={{ color: '#6e8b9e', fontSize: '10px', fontStyle: 'italic' }}>Match: Any (Table Miss)</span>
                        )}
                      </div>

                      {/* Actions & Metrics Footer */}
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid rgba(255,255,255,0.04)', paddingTop: '6px', fontSize: '10px' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <span className="nw-muted">Actions:</span>
                          {flow.actions.map((act, i) => (
                            <span
                              key={i}
                              style={{
                                padding: '1px 5px',
                                borderRadius: '3px',
                                fontSize: '9px',
                                fontWeight: 600,
                                fontFamily: 'monospace',
                                color: act === 'DROP' ? 'var(--red)' : act === 'CONTROLLER' ? 'var(--amber)' : 'var(--cyan)',
                                background: act === 'DROP' ? 'rgba(240,120,121,0.1)' : 'rgba(89,216,255,0.07)',
                              }}
                            >
                              {act}
                            </span>
                          ))}
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                          <span style={{ color: '#7a96a8' }}>
                            Packets: <strong style={{ color: '#c5d9e5' }}>{flow.packet_count.toLocaleString()}</strong>
                          </span>
                          <span style={{ color: '#7a96a8' }}>
                            Bytes: <strong style={{ color: '#c5d9e5' }}>{(flow.byte_count / 1e6).toFixed(2)} MB</strong>
                          </span>
                          {flow.measured ? (
                            isMitigation ? (
                              <span style={{ color: 'var(--red)', fontWeight: 600 }}>
                                Dropping: {flow.dropped_mbps?.toFixed(2) ?? '—'} Mbps
                              </span>
                            ) : (
                              <span style={{ color: 'var(--cyan)', fontWeight: 600 }}>
                                Rate: {flow.throughput_mbps?.toFixed(2) ?? '—'} Mbps
                              </span>
                            )
                          ) : (
                            <span style={{ color: '#5f798a', fontSize: '9px' }}>Awaiting delta</span>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div className="nw-empty" style={{ minHeight: '140px' }}>
                <Network size={20} />
                <strong>No flow entries reported</strong>
                <span>Flow entries will appear once OpenFlow rules are active on the switches.</span>
              </div>
            )}
          </div>
        </div>

        {/* Right: Top Talkers Panel */}
        <div className="nw-panel">
          <PanelHeading
            title="Top Talkers"
            icon={<Flame size={16} />}
            meta="INGRESS SESSIONS · NO DOUBLE COUNTING"
          />

          <div style={{ padding: '12px 16px', maxHeight: '380px', overflowY: 'auto' }}>
            {flowsResponse?.top_talkers && flowsResponse.top_talkers.length > 0 ? (
              <div style={{ display: 'grid', gap: '10px' }}>
                {flowsResponse.top_talkers.map((tt) => (
                  <div
                    key={tt.rank}
                    style={{
                      padding: '10px',
                      background: 'rgba(255,255,255,0.02)',
                      borderRadius: '6px',
                      border: '1px solid var(--line)',
                      display: 'grid',
                      gap: '5px',
                      fontSize: '11px',
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span
                          style={{
                            width: '18px',
                            height: '18px',
                            borderRadius: '50%',
                            background: tt.rank === 1 ? 'rgba(244,181,104,0.2)' : 'rgba(255,255,255,0.05)',
                            color: tt.rank === 1 ? 'var(--amber)' : '#8aa1b0',
                            display: 'grid',
                            placeItems: 'center',
                            fontSize: '9px',
                            fontWeight: 700,
                          }}
                        >
                          {tt.rank}
                        </span>
                        <strong style={{ color: '#e0f0fa', fontFamily: 'monospace' }}>
                          {tt.source} → {tt.destination}
                        </strong>
                      </div>
                      <span
                        style={{
                          fontSize: '9px',
                          padding: '1px 5px',
                          borderRadius: '3px',
                          background: 'rgba(89,216,255,0.08)',
                          color: 'var(--cyan)',
                          fontWeight: 600,
                        }}
                      >
                        {tt.protocol}
                      </span>
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '10px', marginTop: '2px' }}>
                      <span style={{ color: '#7a96a8' }}>
                        Rate: <strong style={{ color: tt.throughput_mbps ? 'var(--cyan)' : '#6e8899' }}>
                          {tt.throughput_mbps !== null && tt.throughput_mbps !== undefined ? `${tt.throughput_mbps.toFixed(2)} Mbps` : 'Unmeasured'}
                        </strong>
                      </span>
                      {tt.share_of_bottleneck_pct !== null && tt.share_of_bottleneck_pct !== undefined ? (
                        <span style={{ color: tt.share_of_bottleneck_pct > 50 ? 'var(--amber)' : '#a0c4d8' }}>
                          Bottleneck Share: <strong>{tt.share_of_bottleneck_pct.toFixed(1)}%</strong>
                        </span>
                      ) : (
                        <span style={{ color: '#5f798a', fontSize: '9px' }}>
                          {tt.traverses_bottleneck ? 'Awaiting bottleneck delta' : 'Edge Link Only'}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="nw-empty" style={{ minHeight: '140px' }}>
                <CircleDot size={20} />
                <strong>No active talkers detected</strong>
                <span>Candidate forwarding flows will appear here once traffic is observed.</span>
              </div>
            )}

            <div className="nw-info-card" style={{ margin: '12px 0 0', padding: '9px', fontSize: '9.5px' }}>
              <Info size={14} style={{ flexShrink: 0, marginTop: '1px', color: 'var(--cyan)' }} />
              <span>
                <strong>Directional Traffic Isolation</strong>
                Sessions are accounted for strictly at the ingress switch to prevent double-counting across multi-switch paths. Dropped traffic is excluded from bottleneck share.
              </span>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
function formatEpoch(epoch?: number) {
  return epoch
    ? new Date(epoch * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—'
}

function formatHostAge(metrics: HostTelemetry) {
  if (typeof metrics.age === 'number') {
    return `${metrics.age.toFixed(1)}s ago`
  }
  return formatEpoch(metrics.last_seen)
}

function HostMonitoring({
  statusData,
  onRefresh,
  loading,
}: {
  statusData?: NetwatchStatus | null
  onRefresh?: () => void
  loading?: boolean
}) {
  const telemetry = statusData?.telemetry ?? {}
  const entries = Object.entries(telemetry)
  const reportingCount = entries.length

  const okCount = entries.filter(([, m]) => m.status === 'OK').length
  const warnCount = entries.filter(([, m]) => m.status === 'WARN').length
  const downCount = entries.filter(([, m]) => m.status === 'DOWN').length

  const losses = entries.flatMap(([, m]) => typeof m.loss === 'number' ? [m.loss] : [])
  const maxLoss = losses.length ? Math.max(...losses) : 0
  const avgLoss = losses.length ? losses.reduce((a, b) => a + b, 0) / losses.length : 0

  const [expStatus, setExpStatus] = useState<ExperimentStatusResponse | null>(null)

  const loadExperimentStatus = useCallback(async () => {
    try {
      const data = await fetchExperimentStatus()
      setExpStatus(data)
    } catch {
      // Keep the last known experiment status if the API is temporarily unavailable.
    }
  }, [])

  useEffect(() => {
    loadExperimentStatus()
    const timer = setInterval(loadExperimentStatus, 2000)
    return () => clearInterval(timer)
  }, [loadExperimentStatus])

  const activeExperiment = expStatus?.active
  const isExperimentRunning = Boolean(
    activeExperiment &&
    (activeExperiment.state === 'RUNNING' || activeExperiment.state === 'RECOVERING')
  )

  return (
    <div className="nw-host-grid">
      <div className="nw-panel nw-host-summary">
        <PanelHeading
          title="Reporting Overview"
          icon={<Server size={16} />}
          meta={reportingCount ? `${reportingCount} HOSTS REPORTING` : 'AWAITING TELEMETRY'}
        />
        <div style={{ padding: '16px', display: 'grid', gap: '14px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px' }}>
            <div style={{ background: 'rgba(255,255,255,.03)', padding: '10px', borderRadius: '7px' }}>
              <span style={{ fontSize: '9px', color: '#6e899b', textTransform: 'uppercase', letterSpacing: '.05em' }}>Total Active</span>
              <strong style={{ display: 'block', fontSize: '20px', marginTop: '4px', color: '#e0f0fa' }}>{reportingCount}</strong>
            </div>
            <div style={{ background: 'rgba(77,214,154,.08)', padding: '10px', borderRadius: '7px', border: '1px solid rgba(77,214,154,.15)' }}>
              <span style={{ fontSize: '9px', color: 'var(--green)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Healthy (OK)</span>
              <strong style={{ display: 'block', fontSize: '20px', marginTop: '4px', color: 'var(--green)' }}>{okCount}</strong>
            </div>
            <div style={{ background: 'rgba(240,120,121,.08)', padding: '10px', borderRadius: '7px', border: '1px solid rgba(240,120,121,.15)' }}>
              <span style={{ fontSize: '9px', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Attention (Warn/Down)</span>
              <strong style={{ display: 'block', fontSize: '20px', marginTop: '4px', color: 'var(--red)' }}>{warnCount + downCount}</strong>
            </div>
          </div>

          <div className="nw-info-card" style={{ margin: 0 }}>
            <Cpu size={16} style={{ flexShrink: 0, marginTop: '2px', color: 'var(--cyan)' }} />
            <span>
              <strong style={{ color: '#d0e5f2' }}>Measurement Scope: Host VM Kernel</strong>
              Telemetry agents read utilization from <code>/proc/stat</code> and <code>/proc/meminfo</code>. Because Mininet network namespaces share the host Linux kernel, CPU and memory metrics represent the host virtual machine, not isolated host cgroups.
            </span>
          </div>

          {isExperimentRunning && activeExperiment && (
            <div className="nw-info-card" style={{ margin: 0, borderColor: 'rgba(244,181,104,.3)' }}>
              <FlaskConical size={16} style={{ flexShrink: 0, marginTop: '2px', color: 'var(--amber)' }} />
              <span>
                <strong style={{ color: '#f4b568' }}>
                  Active injection: {activeExperiment.cmd.toUpperCase()} on {activeExperiment.host}
                </strong>
                This identifies the selected injection target, not every affected host. Other hosts may show shared VM resource or network impact; telemetry status alone cannot attribute the cause to them.
              </span>
            </div>
          )}

          <div style={{ display: 'grid', gap: '6px', fontSize: '11px', color: '#7a96a9', background: 'rgba(255,255,255,.02)', padding: '12px', borderRadius: '7px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Telemetry transit gaps:</span>
              <strong style={{ color: maxLoss > 0.05 ? 'var(--red)' : '#bad3e2' }}>
                {losses.length ? `Max ${(maxLoss * 100).toFixed(1)}% · Avg ${(avgLoss * 100).toFixed(1)}%` : '—'}
              </strong>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Agent reporting port:</span>
              <span style={{ fontFamily: 'monospace' }}>UDP 9999</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Sequence gap definition:</span>
              <span>Missing UDP sequence IDs, not switch packet drop</span>
            </div>
          </div>

          {onRefresh && (
            <button
              onClick={onRefresh}
              className="nw-secondary-button"
              style={{ margin: 0, width: '100%', justifyContent: 'center' }}
            >
              <Activity size={13} /> Refresh Host Telemetry
            </button>
          )}
        </div>
      </div>

      <div className="nw-host-cards">
        {entries.length > 0 ? (
          entries.map(([hostId, metrics]) => {
            const hostIp = hostId === 'h1' ? '10.0.0.1' : hostId === 'h2' ? '10.0.0.2' : hostId === 'h3' ? '10.0.0.3' : hostId === 'h4' ? '10.0.0.4' : 'Mininet Host'
            const cpuVal = typeof metrics.cpu === 'number' ? metrics.cpu : 0
            const memVal = typeof metrics.mem === 'number' ? metrics.mem : 0
            const lossVal = metrics.loss
            const badgeClass = metrics.status === 'DOWN' ? 'critical' : metrics.status === 'WARN' ? 'warning' : 'healthy'

            return (
              <div className="nw-panel nw-host-card" key={hostId}>
                <div className="nw-host-card-head">
                  <div className="nw-host">
                    <div className="nw-host-icon"><Server size={14} /></div>
                    <div>
                      <strong>{hostId}</strong>
                      <small style={{ display: 'block', color: '#5f7c90', fontSize: '10px' }}>{hostIp}</small>
                    </div>
                  </div>
                  <span className={`nw-badge ${badgeClass}`}>
                    <span className="nw-severity-dot" />
                    {metrics.status ?? 'UNKNOWN'}
                  </span>
                </div>

                {isExperimentRunning && activeExperiment && (
                  <div
                    style={{
                      marginTop: '10px',
                      padding: '7px 9px',
                      borderRadius: '5px',
                      fontSize: '9px',
                      fontWeight: 700,
                      letterSpacing: '.04em',
                      background: hostId === activeExperiment.host
                        ? 'rgba(244,181,104,.12)'
                        : 'rgba(89,216,255,.07)',
                      color: hostId === activeExperiment.host ? 'var(--amber)' : 'var(--cyan)',
                      border: `1px solid ${hostId === activeExperiment.host ? 'rgba(244,181,104,.25)' : 'rgba(89,216,255,.18)'}`,
                    }}
                  >
                    {hostId === activeExperiment.host
                      ? 'INJECTION TARGET'
                      : 'OTHER HOST · IMPACT NOT ATTRIBUTED'}
                  </div>
                )}

                <div className="nw-placeholder-metrics" style={{ margin: '14px 0' }}>
                  <div>
                    <span><Cpu size={12} /> CPU (VM-wide)</span>
                    <strong style={{ fontSize: '18px', color: cpuVal > 80 ? 'var(--amber)' : '#dcecf5' }}>
                      {typeof metrics.cpu === 'number' ? `${cpuVal.toFixed(1)}%` : '—'}
                    </strong>
                    <div className="nw-mini-bar" style={{ width: '100%', height: '4px' }}>
                      <span className={cpuVal > 80 ? 'high' : ''} style={{ width: `${Math.min(cpuVal, 100)}%` }} />
                    </div>
                  </div>
                  <div>
                    <span><HardDrive size={12} /> MEM (VM-wide)</span>
                    <strong style={{ fontSize: '18px', color: memVal > 80 ? 'var(--amber)' : '#dcecf5' }}>
                      {typeof metrics.mem === 'number' ? `${memVal.toFixed(1)}%` : '—'}
                    </strong>
                    <div className="nw-mini-bar" style={{ width: '100%', height: '4px' }}>
                      <span className={memVal > 80 ? 'high' : ''} style={{ width: `${Math.min(memVal, 100)}%` }} />
                    </div>
                  </div>
                </div>

                <div className="nw-host-note" style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                    <Activity size={12} />
                    Seq gaps: <strong style={{ color: lossVal !== undefined && lossVal > 0.05 ? 'var(--red)' : '#c0d4e0' }}>
                      {typeof lossVal === 'number' ? `${(lossVal * 100).toFixed(1)}%` : '—'}
                    </strong>
                  </span>
                  <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                    <Clock3 size={12} />
                    {formatHostAge(metrics)}
                  </span>
                </div>
              </div>
            )
          })
        ) : (
          <div className="nw-panel" style={{ gridColumn: '1 / -1', minHeight: '260px' }}>
            <div className="nw-empty large">
              <Server size={28} />
              <strong>No host telemetry available</strong>
              <span>
                {loading
                  ? 'Connecting to Flask backend on http://127.0.0.1:5000...'
                  : 'Awaiting telemetry packets. Ensure Mininet topology (monitoring_topo.py) and monitoring server are running.'}
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

interface ParsedAlert {
  raw: string
  timestamp: string
  source: string
  message: string
  severity: 'critical' | 'warning' | 'info'
}

function parseAlert(raw: string): ParsedAlert {
  let text = raw
  let source = 'SDN Subsystem'
  let timestamp = 'Timestamp: Logger stdout'
  let severity: 'critical' | 'warning' | 'info' = 'warning'

  const sourceMatch = text.match(/^\[(SERVER|CONTROLLER)\]\s*/i)
  if (sourceMatch) {
    source = sourceMatch[1].toUpperCase() === 'SERVER'
      ? 'Monitoring Server (Telemetry Watchdog)'
      : 'OpenFlow Controller (Switch Monitor)'
    text = text.slice(sourceMatch[0].length)
  }

  const timeMatch = text.match(/^\[([^\]]+)\]\s*/)
  if (timeMatch) {
    timestamp = timeMatch[1]
    text = text.slice(timeMatch[0].length)
  }

  if (text.startsWith('SERVER-ALERT:')) {
    severity = 'critical'
    text = text.replace(/^SERVER-ALERT:\s*/, '')
  } else if (text.startsWith('ALERT:')) {
    text = text.replace(/^ALERT:\s*/, '')
    if (/HIGH-UTIL|INTEGRATION ALERT|MITIGATION ACTIVE/i.test(text)) {
      severity = 'critical'
    } else if (/loss|CPU|MEM/i.test(text)) {
      severity = 'warning'
    } else if (/MITIGATION (?:EXPIRED|REMOVED)/i.test(text)) {
      severity = 'info'
    }
  }

  return { raw, timestamp, source, message: text, severity }
}

function IncidentManagement({
  statusData,
  onRefresh,
}: {
  statusData?: NetwatchStatus | null
  onRefresh?: () => void
}) {
  const [acknowledgedSet, setAcknowledgedSet] = useState<Set<string>>(new Set())
  const [selectedAlertRaw, setSelectedAlertRaw] = useState<string | null>(null)
  const [exampleAck, setExampleAck] = useState(false)

  const rawAlerts = statusData?.alerts ?? []
  const parsedAlerts = useMemo(() => rawAlerts.map(parseAlert), [rawAlerts])

  const selectedAlert = useMemo(
    () => parsedAlerts.find((a) => a.raw === selectedAlertRaw) ?? (parsedAlerts.length > 0 ? parsedAlerts[0] : null),
    [parsedAlerts, selectedAlertRaw]
  )

  const toggleAck = (raw: string) => {
    setAcknowledgedSet((prev) => {
      const next = new Set(prev)
      if (next.has(raw)) next.delete(raw)
      else next.add(raw)
      return next
    })
  }

  return (
    <div className="nw-incident-layout">
      <div className="nw-panel">
        <PanelHeading
          title="Controller & Server Incidents"
          icon={<TriangleAlert size={16} />}
          meta={`${parsedAlerts.length} LOGGED ALERTS`}
        />

        <div style={{ margin: '14px 17px', padding: '10px 12px', background: 'rgba(244,181,104,.06)', border: '1px solid rgba(244,181,104,.18)', borderRadius: '7px', fontSize: '10px', color: '#c5a578', lineHeight: 1.5 }}>
          <strong>Local Acknowledgement Notice:</strong> Acknowledgements are kept in browser session state only. Backend log storage (<code>/tmp/sdn_alerts.log</code>) is append-only and does not persist acknowledgement states.
        </div>

        <div style={{ display: 'grid', gap: '8px', padding: '0 17px 17px' }}>
          {parsedAlerts.length > 0 ? (
            parsedAlerts.map((alert, index) => {
              const isAck = acknowledgedSet.has(alert.raw)
              const isSelected = selectedAlert?.raw === alert.raw

              return (
                <div
                  key={`${alert.raw}-${index}`}
                  onClick={() => setSelectedAlertRaw(alert.raw)}
                  style={{
                    border: `1px solid ${isSelected ? 'var(--cyan)' : isAck ? 'rgba(153,190,216,.1)' : 'rgba(244,181,104,.25)'}`,
                    background: isSelected ? 'rgba(89,216,255,.05)' : isAck ? 'rgba(255,255,255,.02)' : 'rgba(244,181,104,.04)',
                    borderRadius: '7px',
                    padding: '12px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '12px',
                    cursor: 'pointer',
                    transition: 'all .15s ease',
                  }}
                >
                  <div className="nw-alert-icon" style={{
                    color: alert.severity === 'critical' ? 'var(--red)' : 'var(--amber)',
                    background: alert.severity === 'critical' ? 'rgba(240,120,121,.1)' : 'rgba(244,181,104,.1)'
                  }}>
                    <TriangleAlert size={15} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0, display: 'grid', gap: '3px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      <strong style={{ fontSize: '11px', color: '#dcebf3' }}>{alert.message}</strong>
                      <span className="nw-badge" style={{ fontSize: '9px', padding: '2px 5px' }}>
                        Logged Alert
                      </span>
                    </div>
                    <span style={{ fontSize: '10px', color: '#6d8798' }}>
                      {alert.timestamp} · {alert.source}
                    </span>
                  </div>
                  <button
                    className="nw-secondary-button"
                    style={{ margin: 0, whiteSpace: 'nowrap', fontSize: '9px', padding: '5px 8px' }}
                    onClick={(e) => {
                      e.stopPropagation()
                      toggleAck(alert.raw)
                    }}
                  >
                    {isAck ? 'Acknowledged' : 'Acknowledge'}
                  </button>
                </div>
              )
            })
          ) : (
            <div className="nw-incident-empty">
              <TriangleAlert />
              <strong>No active controller or server incidents</strong>
              <span>When bottlenecks, packet loss, or host silences are detected, they will appear here from the live alert log.</span>
            </div>
          )}

          {/* Distinct Simulated Incident Treatment */}
          <div style={{ marginTop: '16px', borderTop: '1px solid var(--line)', paddingTop: '14px' }}>
            <div style={{ fontSize: '9px', color: '#688497', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: '8px' }}>
              Reference / Simulation Specimen
            </div>
            <div className="nw-prototype-alert" style={{ margin: 0 }}>
              <div className="nw-alert-icon" style={{ color: '#88a0b0', background: 'rgba(136,160,176,.1)' }}>
                <TriangleAlert size={15} />
              </div>
              <div>
                <strong>Simulated incident template</strong>
                <span style={{ color: '#889eac' }}>
                  Illustrative UI specimen · Not an active incident
                </span>
              </div>
              <button
                className="nw-secondary-button"
                onClick={() => setExampleAck(!exampleAck)}
              >
                {exampleAck ? 'Acknowledged (Sim)' : 'Acknowledge (Sim)'}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="nw-panel">
        <PanelHeading
          title="Incident Details"
          icon={<SlidersHorizontal size={16} />}
          meta={selectedAlert ? 'INSPECTING INCIDENT' : 'SELECT AN ALERT'}
        />
        {selectedAlert ? (
          <div style={{ padding: '16px', display: 'grid', gap: '14px' }}>
            <div>
              <span style={{ fontSize: '9px', color: '#688497', textTransform: 'uppercase', letterSpacing: '.06em' }}>Event Message</span>
              <div style={{ fontSize: '13px', fontWeight: 600, color: '#e2eff7', marginTop: '4px' }}>
                {selectedAlert.message}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
              <div style={{ background: 'rgba(255,255,255,.02)', padding: '10px', borderRadius: '6px' }}>
                <span style={{ fontSize: '9px', color: '#688497', textTransform: 'uppercase' }}>Reported Time</span>
                <strong style={{ display: 'block', fontSize: '11px', color: '#b9d2e1', marginTop: '3px' }}>
                  {selectedAlert.timestamp}
                </strong>
              </div>
              <div style={{ background: 'rgba(255,255,255,.02)', padding: '10px', borderRadius: '6px' }}>
                <span style={{ fontSize: '9px', color: '#688497', textTransform: 'uppercase' }}>Incident Source</span>
                <strong style={{ display: 'block', fontSize: '11px', color: '#b9d2e1', marginTop: '3px' }}>
                  {selectedAlert.source}
                </strong>
              </div>
            </div>

            <div>
              <span style={{ fontSize: '9px', color: '#688497', textTransform: 'uppercase', letterSpacing: '.06em' }}>Raw Controller Log Entry</span>
              <pre style={{
                background: '#060f17',
                border: '1px solid var(--line)',
                padding: '10px',
                borderRadius: '6px',
                fontSize: '10px',
                color: 'var(--cyan)',
                overflowX: 'auto',
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-all',
                marginTop: '4px',
              }}>
                {selectedAlert.raw}
              </pre>
            </div>

            <div>
              <span style={{ fontSize: '9px', color: '#688497', textTransform: 'uppercase', letterSpacing: '.06em' }}>Session Status</span>
              <div style={{ marginTop: '6px' }}>
                <button
                  className="nw-secondary-button"
                  style={{ margin: 0 }}
                  onClick={() => toggleAck(selectedAlert.raw)}
                >
                  {acknowledgedSet.has(selectedAlert.raw) ? 'Clear Local Acknowledgement' : 'Acknowledge Incident Locally'}
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="nw-empty">
            <CircleDot />
            <strong>No incident selected</strong>
            <span>Select any active incident from the list to inspect log details and trigger responses.</span>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Priority 4: Network Analytics & Intelligence Dashboard
// ---------------------------------------------------------------------------

function formatBytes(bytes?: number): string {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return 'N/A'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function formatTime(ts?: number | null): string {
  if (!ts || isNaN(ts)) return 'N/A'
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function formatDateTime(ts?: number | null): string {
  if (!ts || isNaN(ts)) return 'N/A'
  const d = new Date(ts * 1000)
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
}

function formatDuration(sec?: number | null): string {
  if (sec === undefined || sec === null || isNaN(sec)) return '—'
  if (sec < 60) return `${sec.toFixed(1)}s`
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}m ${s}s`
}

function formatAge(ts?: number | null): string {
  if (!ts || isNaN(ts)) return '—'
  const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (delta < 60) return `${delta}s ago`
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  return `${Math.floor(delta / 3600)}h ago`
}

function NetworkIntelligence({
  statusData,
  onRefresh: parentRefresh,
}: {
  statusData?: NetwatchStatus | null
  onRefresh?: () => void
}) {
  const [range, setRange] = useState<TimeRange>('1h')
  const [activeHost, setActiveHost] = useState<string>('all')
  const [incidentFilter, setIncidentFilter] = useState<'ALL' | 'ACTIVE' | 'RESOLVED'>('ALL')

  // API Data States
  const [summary, setSummary] = useState<AnalyticsSummaryResponse | null>(null)
  const [bottleneck, setBottleneck] = useState<BottleneckHistoryResponse | null>(null)
  const [hostsData, setHostsData] = useState<HostHistoryResponse | null>(null)
  const [flowsData, setFlowsData] = useState<FlowHistoryResponse | null>(null)
  const [incidents, setIncidents] = useState<IncidentRecord[]>([])
  const [anomalies, setAnomalies] = useState<AnomalyRecord[]>([])

  // UI Lifecycle States
  const [loading, setLoading] = useState<boolean>(true)
  const [refreshing, setRefreshing] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  // Chart Tooltip Hover State
  const [hoveredPoint, setHoveredPoint] = useState<BottleneckPoint | null>(null)
  const [tooltipPos, setTooltipPos] = useState<{ x: number; y: number } | null>(null)

  // Request Cancellation & In-flight guards
  const abortControllerRef = useRef<AbortController | null>(null)
  const inFlightRef = useRef<boolean>(false)

  const loadData = useCallback(async (selectedRange: TimeRange, selectedHost: string, isBackground = false) => {
    if (inFlightRef.current && isBackground) return

    if (!isBackground) {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
      setLoading(true)
    } else {
      setRefreshing(true)
    }

    const abortController = new AbortController()
    abortControllerRef.current = abortController
    inFlightRef.current = true

    try {
      const [sumRes, bnkRes, hostRes, flowRes, incRes, anomRes] = await Promise.all([
        fetchAnalyticsSummary(selectedRange, abortController.signal),
        fetchBottleneckHistory(selectedRange, abortController.signal),
        fetchHostHistory(selectedRange, selectedHost, abortController.signal),
        fetchFlowHistoryTop(selectedRange, 5, abortController.signal),
        fetchAnalyticsIncidents(undefined, 50, abortController.signal),
        fetchAnalyticsAnomalies(20, abortController.signal),
      ])

      setSummary(sumRes)
      setBottleneck(bnkRes)
      setHostsData(hostRes)
      setFlowsData(flowRes)
      setIncidents(incRes.incidents ?? [])
      setAnomalies(anomRes.anomalies ?? [])
      setError(null)
      setLastUpdated(new Date())
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        return // Superseded request
      }
      const msg = err instanceof Error ? err.message : 'Failed to retrieve analytics data from backend.'
      setError(msg)
    } finally {
      inFlightRef.current = false
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  // Range and host selection change triggers fetch
  useEffect(() => {
    loadData(range, activeHost, false)
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort()
      }
    }
  }, [range, activeHost, loadData])

  // Periodic anomaly & KPI refresh (polling every 10 seconds)
  useEffect(() => {
    const timer = setInterval(() => {
      loadData(range, activeHost, true)
    }, 10000)
    return () => clearInterval(timer)
  }, [range, activeHost, loadData])

  const handleManualRefresh = () => {
    loadData(range, activeHost, false)
    if (parentRefresh) parentRefresh()
  }

  // Filtered incidents by tab
  const filteredIncidents = useMemo(() => {
    if (incidentFilter === 'ALL') return incidents
    return incidents.filter((i) => i.state === incidentFilter)
  }, [incidents, incidentFilter])

  const activeIncidentCount = useMemo(() => incidents.filter((i) => i.state === 'ACTIVE').length, [incidents])
  const resolvedIncidentCount = useMemo(() => incidents.filter((i) => i.state === 'RESOLVED').length, [incidents])

  // KPI Calculations & formatting
  const cfp = summary?.metrics.congestion_free_pct
  const peakUtil = summary?.metrics.peak_utilization_pct
  const peakTs = summary?.metrics.peak_utilization_timestamp
  const mttm = summary?.metrics.mttm_seconds
  const mttr = summary?.metrics.mttr_seconds
  const activeCount = summary?.metrics.active_incidents_count ?? 0
  const windowCount = summary?.metrics.total_incidents_in_window ?? 0

  return (
    <div className="nw-analytics-dashboard" style={{ display: 'grid', gap: '16px' }}>
      {/* 1. Header Toolbar */}
      <div
        className="nw-panel"
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 18px',
          gap: '12px',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <div
            style={{
              width: 32,
              height: 32,
              borderRadius: 8,
              background: 'rgba(89,216,255,0.12)',
              color: 'var(--cyan)',
              display: 'grid',
              placeItems: 'center',
            }}
          >
            <BarChart2 size={18} />
          </div>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <strong style={{ fontSize: '13px', color: '#e8f0f6', letterSpacing: '0.02em' }}>
                NOC Intelligence & Telemetry Analytics
              </strong>
              <span className="nw-live-chip">
                <span />
                LIVE
              </span>
            </div>
            <small style={{ color: '#688497', fontSize: '10px' }}>
              SQLite WAL Persistence · 60s EMA Anomaly Detection · Hourly Closed Aggregations
            </small>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          {/* Range Selectors */}
          <div
            style={{
              display: 'flex',
              background: 'rgba(255,255,255,0.04)',
              borderRadius: '6px',
              padding: '2px',
              border: '1px solid var(--line)',
            }}
          >
            {(['15m', '1h', '3h', '24h'] as TimeRange[]).map((r) => (
              <button
                key={r}
                type="button"
                onClick={() => setRange(r)}
                style={{
                  border: 0,
                  background: range === r ? 'rgba(89,216,255,0.18)' : 'transparent',
                  color: range === r ? 'var(--cyan)' : '#8ba2b3',
                  padding: '5px 11px',
                  borderRadius: '5px',
                  fontSize: '10px',
                  fontWeight: range === r ? 700 : 500,
                  cursor: 'pointer',
                  transition: 'all 0.15s ease',
                }}
              >
                {r.toUpperCase()}
              </button>
            ))}
          </div>

          {/* Refresh Button */}
          <button
            type="button"
            onClick={handleManualRefresh}
            className="nw-secondary-button"
            disabled={loading}
            style={{ margin: 0, padding: '6px 10px', display: 'flex', alignItems: 'center', gap: '6px' }}
          >
            <RefreshCw size={12} className={refreshing || loading ? 'animate-spin' : ''} />
            <span>{refreshing ? 'Refreshing...' : 'Refresh'}</span>
          </button>

          {lastUpdated && (
            <span style={{ fontSize: '10px', color: '#627c8e' }}>
              Updated {lastUpdated.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          )}
        </div>
      </div>

      {/* Error Alert Banner */}
      {error && (
        <div
          className="nw-deploy-note"
          style={{
            margin: 0,
            background: 'rgba(240,120,121,0.08)',
            borderColor: 'rgba(240,120,121,0.3)',
            color: 'var(--red)',
          }}
        >
          <TriangleAlert size={16} style={{ flexShrink: 0, marginTop: 2 }} />
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
            <span>
              <strong>Analytics Engine Error:</strong> {error}
            </span>
            <button
              type="button"
              onClick={handleManualRefresh}
              className="nw-danger-button"
              style={{ padding: '3px 8px', border: '1px solid var(--red)', borderRadius: '4px' }}
            >
              Retry
            </button>
          </div>
        </div>
      )}

      {/* 2. Section A: NOC Executive KPI Cards */}
      <div
        className="nw-kpi-grid"
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: '12px',
        }}
      >
        {/* KPI 1: CFP */}
        <div className="nw-metric" style={{ minHeight: 'auto', padding: '14px' }}>
          <div className="nw-metric-copy" style={{ display: 'grid', gap: '2px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: '#7993a4' }}>
              <ShieldCheck size={12} style={{ color: 'var(--green)' }} />
              CONGESTION-FREE % (CFP)
            </span>
            <strong className="nw-metric-value" style={{ fontSize: '24px', color: cfp !== null && cfp !== undefined && cfp >= 80 ? 'var(--green)' : 'var(--amber)' }}>
              {cfp !== null && cfp !== undefined ? `${cfp.toFixed(1)}%` : 'N/A'}
            </strong>
            <p style={{ margin: 0, fontSize: '10px', color: '#627e90' }}>
              Samples &lt; 80% utilization
            </p>
          </div>
        </div>

        {/* KPI 2: Peak Utilization */}
        <div className="nw-metric" style={{ minHeight: 'auto', padding: '14px' }}>
          <div className="nw-metric-copy" style={{ display: 'grid', gap: '2px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: '#7993a4' }}>
              <Activity size={12} style={{ color: 'var(--cyan)' }} />
              PEAK BOTTLENECK UTIL
            </span>
            <strong className="nw-metric-value" style={{ fontSize: '24px', color: peakUtil !== null && peakUtil !== undefined && peakUtil >= 80 ? 'var(--red)' : 'var(--cyan)' }}>
              {peakUtil !== null && peakUtil !== undefined ? `${peakUtil.toFixed(1)}%` : 'N/A'}
            </strong>
            <p style={{ margin: 0, fontSize: '10px', color: '#627e90' }}>
              {peakTs ? `Peak at ${formatTime(peakTs)}` : 'Link s1-s2 (5 Mbps max)'}
            </p>
          </div>
        </div>

        {/* KPI 3: MTTM */}
        <div className="nw-metric" style={{ minHeight: 'auto', padding: '14px' }}>
          <div className="nw-metric-copy" style={{ display: 'grid', gap: '2px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: '#7993a4' }}>
              <Clock3 size={12} style={{ color: '#a897ff' }} />
              MEAN TIME TO MITIGATION
            </span>
            <strong className="nw-metric-value" style={{ fontSize: '24px', color: '#d0c6ff' }}>
              {mttm !== null && mttm !== undefined ? `${mttm.toFixed(1)}s` : 'N/A'}
            </strong>
            <p style={{ margin: 0, fontSize: '10px', color: '#627e90' }}>
              Drop rule confirmed on s1
            </p>
          </div>
        </div>

        {/* KPI 4: MTTR */}
        <div className="nw-metric" style={{ minHeight: 'auto', padding: '14px' }}>
          <div className="nw-metric-copy" style={{ display: 'grid', gap: '2px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: '#7993a4' }}>
              <Check size={12} style={{ color: 'var(--green)' }} />
              MEAN TIME TO RECOVERY
            </span>
            <strong className="nw-metric-value" style={{ fontSize: '24px', color: 'var(--green)' }}>
              {mttr !== null && mttr !== undefined ? `${mttr.toFixed(1)}s` : 'N/A'}
            </strong>
            <p style={{ margin: 0, fontSize: '10px', color: '#627e90' }}>
              Util &lt; 75% & rule cleared
            </p>
          </div>
        </div>

        {/* KPI 5: Active Incidents */}
        <div className="nw-metric" style={{ minHeight: 'auto', padding: '14px' }}>
          <div className="nw-metric-copy" style={{ display: 'grid', gap: '2px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: '#7993a4' }}>
              <AlertOctagon size={12} style={{ color: activeCount > 0 ? 'var(--red)' : 'var(--green)' }} />
              ACTIVE INCIDENTS
            </span>
            <strong className="nw-metric-value" style={{ fontSize: '24px', color: activeCount > 0 ? 'var(--red)' : '#e8f0f6' }}>
              {activeCount}
            </strong>
            <p style={{ margin: 0, fontSize: '10px', color: '#627e90' }}>
              {windowCount} incident{windowCount === 1 ? '' : 's'} in {range}
            </p>
          </div>
        </div>

        {/* KPI 6: SQLite Storage */}
        <div className="nw-metric" style={{ minHeight: 'auto', padding: '14px' }}>
          <div className="nw-metric-copy" style={{ display: 'grid', gap: '2px' }}>
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '10px', color: '#7993a4' }}>
              <Database size={12} style={{ color: 'var(--cyan)' }} />
              STORAGE FOOTPRINT
            </span>
            <strong className="nw-metric-value" style={{ fontSize: '24px', color: '#b6cad7' }}>
              {summary ? formatBytes(summary.storage.total_size_bytes) : 'N/A'}
            </strong>
            <p style={{ margin: 0, fontSize: '10px', color: '#627e90' }}>
              {summary ? `${summary.storage.raw_retention_hours}h raw · ${summary.storage.rollup_retention_days}d rollups` : 'Retention horizons'}
            </p>
          </div>
        </div>
      </div>

      {/* 3. Section B & F: Bottleneck Utilization History + Real-Time Anomaly Feed */}
      <div
        className="nw-analytics-grid-main"
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.55fr) minmax(320px, 1fr)',
          gap: '16px',
        }}
      >
        {/* Section B: Bottleneck Utilization Chart */}
        <div className="nw-panel" style={{ minHeight: '380px', display: 'flex', flexDirection: 'column' }}>
          <PanelHeading
            title="Bottleneck Link Utilization Trend (s1-s2)"
            icon={<Activity size={16} />}
            meta={range === '24h' ? 'MIXED RESOLUTION (HOURLY ROLLUPS + RAW)' : `RAW TELEMETRY (${range.toUpperCase()})`}
          />

          <div style={{ padding: '12px 18px', flex: 1, position: 'relative' }}>
            {/* Chart Legend / Metadata */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px', fontSize: '10px', color: '#6d8798' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ width: 10, height: 2, background: 'var(--cyan)' }} />
                  Utilization Rate
                </span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                  <span style={{ width: 10, height: 1, background: 'var(--amber)', borderTop: '1px dashed var(--amber)' }} />
                  80% Congestion Threshold
                </span>
                {range === '24h' && (
                  <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
                    <span style={{ width: 6, height: 6, background: '#a897ff', transform: 'rotate(45deg)' }} />
                    1h Rollup Aggregate
                  </span>
                )}
              </div>
              <span>Capacity: <strong>5.0 Mbps</strong> · Port s1:5 ↔ s2:1</span>
            </div>

            {/* Interactive SVG Chart or Empty State */}
            {loading && !bottleneck ? (
              <div className="nw-empty" style={{ minHeight: '230px' }}>
                <RefreshCw size={24} className="animate-spin" style={{ color: 'var(--cyan)' }} />
                <strong>Loading historical samples...</strong>
                <span>Fetching time-series records from SQLite storage engine.</span>
              </div>
            ) : !bottleneck || bottleneck.points.length === 0 ? (
              <div className="nw-chart-empty" style={{ minHeight: '230px' }}>
                <Sparkles size={24} style={{ color: '#4c687b' }} />
                <strong>No historical telemetry for this range</strong>
                <span>The background sampler persists runtime snapshots every 2 seconds. Trigger traffic in Experiment Center to generate history.</span>
              </div>
            ) : (
              <div style={{ position: 'relative', width: '100%', overflow: 'hidden' }}>
                <BottleneckSvgChart
                  bottleneck={bottleneck}
                  onHoverPoint={(pt, pos) => {
                    setHoveredPoint(pt)
                    setTooltipPos(pos)
                  }}
                />

                {/* Floating Tooltip */}
                {hoveredPoint && tooltipPos && (
                  <div
                    style={{
                      position: 'absolute',
                      left: Math.max(10, Math.min(tooltipPos.x - 70, 520)),
                      top: Math.max(10, tooltipPos.y - 85),
                      background: 'rgba(9, 21, 32, 0.95)',
                      border: '1px solid rgba(89, 216, 255, 0.35)',
                      borderRadius: '6px',
                      padding: '8px 10px',
                      fontSize: '10px',
                      pointerEvents: 'none',
                      boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
                      zIndex: 10,
                      minWidth: '150px',
                    }}
                  >
                    <div style={{ color: '#7fa1b6', marginBottom: '4px', fontSize: '9px' }}>
                      {formatDateTime(hoveredPoint.timestamp)}
                    </div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', marginBottom: '2px' }}>
                      <span style={{ color: '#a6bece' }}>Utilization:</span>
                      <strong style={{ color: hoveredPoint.utilization_pct && hoveredPoint.utilization_pct >= 80 ? 'var(--red)' : 'var(--cyan)' }}>
                        {hoveredPoint.utilization_pct !== null && hoveredPoint.utilization_pct !== undefined ? `${hoveredPoint.utilization_pct.toFixed(1)}%` : 'Unmeasured'}
                      </strong>
                    </div>
                    {hoveredPoint.throughput_mbps !== null && hoveredPoint.throughput_mbps !== undefined && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', marginBottom: '2px' }}>
                        <span style={{ color: '#a6bece' }}>Throughput:</span>
                        <strong style={{ color: '#e8f0f6' }}>{hoveredPoint.throughput_mbps.toFixed(2)} Mbps</strong>
                      </div>
                    )}
                    {hoveredPoint.resolution === '1h_rollup' && hoveredPoint.max_utilization_pct !== null && hoveredPoint.max_utilization_pct !== undefined && (
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '8px', marginBottom: '2px', color: '#a897ff' }}>
                        <span>Peak in Hour:</span>
                        <strong>{hoveredPoint.max_utilization_pct.toFixed(1)}%</strong>
                      </div>
                    )}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '4px', borderTop: '1px solid rgba(255,255,255,0.08)', paddingTop: '4px' }}>
                      <span style={{ fontSize: '9px', color: '#688497' }}>
                        {hoveredPoint.resolution === '1h_rollup' ? '1h Aggregate' : 'Raw Telemetry'}
                      </span>
                      <span className={`nw-badge ${hoveredPoint.status === 'congested' ? 'critical' : 'healthy'}`} style={{ fontSize: '9px' }}>
                        {hoveredPoint.status.toUpperCase()}
                      </span>
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Section F: Real-Time Anomaly Feed */}
        <div className="nw-panel" style={{ minHeight: '380px', display: 'flex', flexDirection: 'column' }}>
          <PanelHeading
            title="Real-Time Anomaly Detection Feed"
            icon={<Flame size={16} />}
            meta={`${anomalies.length} DETECTED`}
          />

          <div style={{ padding: '10px 14px', flex: 1, overflowY: 'auto', maxHeight: '340px', display: 'grid', gap: '8px', alignContent: 'start' }}>
            {anomalies.length === 0 ? (
              <div className="nw-empty" style={{ minHeight: '220px' }}>
                <Check size={24} style={{ color: 'var(--green)' }} />
                <strong>No anomalies detected</strong>
                <span>Traffic bursts (3x 60s EMA), VM saturation (&gt;85% CPU), and silent telemetry events will appear here in real time.</span>
              </div>
            ) : (
              anomalies.map((anom) => {
                const isCritical = anom.severity === 'CRITICAL' || anom.event_type === 'CONGESTION'
                const isBurst = anom.event_type === 'TRAFFIC_BURST'
                const isVM = anom.event_type === 'VM_CPU_SATURATION'

                return (
                  <div
                    key={anom.incident_id}
                    style={{
                      background: 'rgba(255,255,255,0.02)',
                      border: `1px solid ${isCritical ? 'rgba(240,120,121,0.25)' : 'rgba(244,181,104,0.25)'}`,
                      borderRadius: '7px',
                      padding: '10px 12px',
                      display: 'grid',
                      gap: '4px',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <span
                          style={{
                            width: 18,
                            height: 18,
                            borderRadius: 4,
                            background: isBurst ? 'rgba(244,181,104,0.15)' : isVM ? 'rgba(168,151,255,0.15)' : 'rgba(240,120,121,0.15)',
                            color: isBurst ? 'var(--amber)' : isVM ? '#a897ff' : 'var(--red)',
                            display: 'grid',
                            placeItems: 'center',
                          }}
                        >
                          {isBurst ? <Flame size={11} /> : isVM ? <Cpu size={11} /> : <AlertOctagon size={11} />}
                        </span>
                        <strong style={{ fontSize: '11px', color: '#e8f0f6' }}>
                          {anom.event_type.replace(/_/g, ' ')}
                        </strong>
                      </div>
                      <span style={{ fontSize: '9px', color: '#668092' }}>
                        {formatAge(anom.start_time)}
                      </span>
                    </div>

                    <p style={{ margin: 0, fontSize: '10px', color: '#a2b8c7', lineHeight: '1.4' }}>
                      {anom.description}
                    </p>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '9px', color: '#688497', marginTop: '2px' }}>
                      <span>Target: <strong style={{ color: '#b9cad6' }}>{anom.target}</strong></span>
                      <span>
                        Peak: <strong style={{ color: isCritical ? 'var(--red)' : 'var(--amber)' }}>
                          {anom.peak_value}{isBurst ? ' Mbps' : '%'}
                        </strong> (Trigger: {anom.trigger_threshold}{isBurst ? 'x EMA' : '%'})
                      </span>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>
      </div>

      {/* 4. Section C & D: Host Telemetry Trends + Top Talkers */}
      <div
        className="nw-analytics-grid-secondary"
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.2fr)',
          gap: '16px',
        }}
      >
        {/* Section C: Host Telemetry Trends */}
        <div className="nw-panel" style={{ minHeight: '340px', display: 'flex', flexDirection: 'column' }}>
          <PanelHeading
            title="Host Telemetry & System Resource Trends"
            icon={<Cpu size={16} />}
            meta="AGENT HEARTBEATS"
          />

          <div style={{ padding: '14px 18px', flex: 1, display: 'flex', flexDirection: 'column', gap: '12px' }}>
            {/* VM Namespace Scope Disclaimer Banner */}
            <div
              className="nw-info-card"
              style={{
                margin: 0,
                background: 'rgba(89,216,255,0.04)',
                borderColor: 'rgba(89,216,255,0.2)',
                color: '#b5d6e6',
                padding: '9px 12px',
                fontSize: '9.5px',
                lineHeight: 1.45,
              }}
            >
              <Info size={14} style={{ color: 'var(--cyan)', flexShrink: 0, marginTop: 1 }} />
              <div>
                <strong style={{ color: 'var(--cyan)', display: 'block', marginBottom: '2px' }}>
                  Shared VM Host Namespace Limitation
                </strong>
                Mininet hosts (h1–h4) share the host Linux OS kernel. CPU and memory metrics represent the shared VM container, not isolated physical host hardware. Telemetry sequence gaps measure UDP telemetry agent-to-server delivery, not data-plane network packet drop.
              </div>
            </div>

            {/* Host Filter Tabs */}
            <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
              <span style={{ fontSize: '10px', color: '#688497', marginRight: '4px' }}>FILTER:</span>
              {['all', 'h1', 'h2', 'h3', 'h4'].map((hId) => (
                <button
                  key={hId}
                  type="button"
                  onClick={() => setActiveHost(hId)}
                  style={{
                    border: '1px solid var(--line)',
                    background: activeHost === hId ? 'rgba(89,216,255,0.15)' : 'rgba(255,255,255,0.02)',
                    color: activeHost === hId ? 'var(--cyan)' : '#7f98a8',
                    padding: '3px 9px',
                    borderRadius: '4px',
                    fontSize: '10px',
                    cursor: 'pointer',
                  }}
                >
                  {hId === 'all' ? 'All Hosts' : hId.toUpperCase()}
                </button>
              ))}
            </div>

            {/* Host Telemetry Table */}
            <div className="nw-table-wrap" style={{ flex: 1 }}>
              <table className="nw-table">
                <thead>
                  <tr>
                    <th>Host</th>
                    <th>Shared VM CPU</th>
                    <th>Shared VM Mem</th>
                    <th>Telemetry Gap</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {!hostsData || Object.keys(hostsData.hosts).length === 0 ? (
                    <tr>
                      <td colSpan={5} style={{ textAlign: 'center', color: '#627c8e', padding: '24px' }}>
                        No host telemetry snapshots in this range.
                      </td>
                    </tr>
                  ) : (
                    Object.entries(hostsData.hosts).map(([hostId, samples]) => {
                      const latest = samples.at(-1)
                      const cpu = latest?.vm_cpu_util
                      const mem = latest?.vm_mem_util
                      const loss = latest?.telemetry_loss_rate
                      const st = latest?.agent_status ?? 'OK'

                      return (
                        <tr key={hostId}>
                          <td>
                            <div className="nw-host">
                              <div className="nw-host-icon">
                                <Server size={13} />
                              </div>
                              <strong>{hostId.toUpperCase()}</strong>
                            </div>
                          </td>
                          <td>
                            <div className="nw-bar-value">
                              <div className="nw-mini-bar">
                                <span
                                  className={cpu && cpu > 80 ? 'high' : ''}
                                  style={{ width: `${Math.min(100, cpu ?? 0)}%` }}
                                />
                              </div>
                              <span style={{ fontSize: '10px' }}>
                                {cpu !== null && cpu !== undefined ? `${cpu.toFixed(1)}%` : '—'}
                              </span>
                            </div>
                          </td>
                          <td>
                            <div className="nw-bar-value">
                              <div className="nw-mini-bar">
                                <span
                                  style={{
                                    width: `${Math.min(100, mem ?? 0)}%`,
                                    background: '#a897ff',
                                  }}
                                />
                              </div>
                              <span style={{ fontSize: '10px' }}>
                                {mem !== null && mem !== undefined ? `${mem.toFixed(1)}%` : '—'}
                              </span>
                            </div>
                          </td>
                          <td>
                            <span
                              style={{
                                fontSize: '10px',
                                color: loss && loss > 0 ? 'var(--amber)' : '#7a96a7',
                              }}
                            >
                              {loss !== null && loss !== undefined ? `${(loss * 100).toFixed(1)}%` : '0.0%'}
                            </span>
                          </td>
                          <td>
                            <span className={`nw-badge ${st === 'OK' ? 'healthy' : st === 'WARN' ? 'warning' : 'critical'}`}>
                              {st}
                            </span>
                          </td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Section D: Top Talkers */}
        <div className="nw-panel" style={{ minHeight: '340px', display: 'flex', flexDirection: 'column' }}>
          <PanelHeading
            title="Top Talkers (Forwarding Flows)"
            icon={<TrendingUp size={16} />}
            meta="DROP FLOWS EXCLUDED"
          />

          <div style={{ padding: '14px 18px', flex: 1, display: 'flex', flexDirection: 'column' }}>
            <div className="nw-table-wrap" style={{ flex: 1 }}>
              <table className="nw-table">
                <thead>
                  <tr>
                    <th>Rank</th>
                    <th>Flow Endpoints</th>
                    <th>Protocol</th>
                    <th>Path</th>
                    <th>Avg Rate</th>
                    <th>Peak</th>
                    <th>Bottleneck Share</th>
                  </tr>
                </thead>
                <tbody>
                  {!flowsData || flowsData.top_flows.length === 0 ? (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', color: '#627c8e', padding: '28px' }}>
                        No forwarding flow measurements recorded. (Mitigation DROP flows are excluded).
                      </td>
                    </tr>
                  ) : (
                    flowsData.top_flows.map((fl, idx) => (
                      <tr key={fl.flow_id}>
                        <td>
                          <span
                            style={{
                              width: 20,
                              height: 20,
                              borderRadius: '50%',
                              background: idx === 0 ? 'rgba(89,216,255,0.15)' : 'rgba(255,255,255,0.04)',
                              color: idx === 0 ? 'var(--cyan)' : '#8aa3b4',
                              display: 'inline-grid',
                              placeItems: 'center',
                              fontSize: '9px',
                              fontWeight: 700,
                            }}
                          >
                            #{idx + 1}
                          </span>
                        </td>
                        <td>
                          <div style={{ display: 'grid', gap: '2px' }}>
                            <strong style={{ fontSize: '11px', color: '#e8f0f6' }}>
                              {fl.source} → {fl.destination}
                            </strong>
                            <small className="nw-mono" style={{ fontSize: '8.5px', color: '#5f798b' }}>
                              {fl.switch.toUpperCase()} · ID: {fl.flow_id.slice(0, 16)}...
                            </small>
                          </div>
                        </td>
                        <td>
                          <span style={{ fontSize: '10px', color: '#9cb5c5' }}>{fl.protocol}</span>
                        </td>
                        <td>
                          {fl.traverses_bottleneck ? (
                            <span
                              className="nw-badge warning"
                              style={{ fontSize: '9px', padding: '2px 6px', background: 'rgba(244,181,104,0.1)', borderRadius: '4px' }}
                            >
                              s1-s2 Bottleneck
                            </span>
                          ) : (
                            <span style={{ fontSize: '9px', color: '#607c8d' }}>Local Transit</span>
                          )}
                        </td>
                        <td>
                          <strong style={{ color: 'var(--cyan)', fontSize: '11px' }}>
                            {fl.avg_throughput_mbps.toFixed(2)} Mbps
                          </strong>
                        </td>
                        <td>
                          <span style={{ color: '#d8e5ee', fontSize: '10px' }}>
                            {fl.peak_throughput_mbps.toFixed(2)} Mbps
                          </span>
                        </td>
                        <td>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <div className="nw-mini-bar" style={{ width: '48px' }}>
                              <span
                                style={{
                                  width: `${Math.min(100, fl.points.at(-1)?.share_of_bottleneck_pct ?? 0)}%`,
                                  background: fl.traverses_bottleneck ? 'var(--amber)' : 'var(--cyan)',
                                }}
                              />
                            </div>
                            <span style={{ fontSize: '9.5px', color: '#88a3b5' }}>
                              {fl.points.at(-1)?.share_of_bottleneck_pct !== null && fl.points.at(-1)?.share_of_bottleneck_pct !== undefined
                                ? `${fl.points.at(-1)!.share_of_bottleneck_pct!.toFixed(0)}%`
                                : '—'}
                            </span>
                          </div>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>

      {/* 5. Section E: Incident Lifecycle Log */}
      <div className="nw-panel" style={{ minHeight: '320px', display: 'flex', flexDirection: 'column' }}>
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '12px 18px',
            borderBottom: '1px solid var(--line)',
            gap: '10px',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <AlertOctagon size={16} style={{ color: 'var(--amber)' }} />
            <h3 style={{ margin: 0, fontSize: '13px', fontWeight: 600 }}>Incident Lifecycle State-Machine Log</h3>
          </div>

          <div style={{ display: 'flex', gap: '6px' }}>
            {(['ALL', 'ACTIVE', 'RESOLVED'] as const).map((filter) => (
              <button
                key={filter}
                type="button"
                onClick={() => setIncidentFilter(filter)}
                style={{
                  border: '1px solid var(--line)',
                  background: incidentFilter === filter ? 'rgba(89,216,255,0.15)' : 'rgba(255,255,255,0.02)',
                  color: incidentFilter === filter ? 'var(--cyan)' : '#7f98a8',
                  padding: '3px 10px',
                  borderRadius: '4px',
                  fontSize: '10px',
                  cursor: 'pointer',
                }}
              >
                {filter === 'ALL'
                  ? `All (${incidents.length})`
                  : filter === 'ACTIVE'
                  ? `Active (${activeIncidentCount})`
                  : `Resolved (${resolvedIncidentCount})`}
              </button>
            ))}
          </div>
        </div>

        <div style={{ padding: '14px 18px', flex: 1 }}>
          <div className="nw-table-wrap">
            <table className="nw-table">
              <thead>
                <tr>
                  <th>Incident ID</th>
                  <th>Event Type</th>
                  <th>Target</th>
                  <th>Severity</th>
                  <th>State</th>
                  <th>Peak Value</th>
                  <th>TTM</th>
                  <th>MTTR</th>
                  <th>Duration</th>
                  <th>Start Time</th>
                  <th>Description</th>
                </tr>
              </thead>
              <tbody>
                {filteredIncidents.length === 0 ? (
                  <tr>
                    <td colSpan={11} style={{ textAlign: 'center', color: '#627c8e', padding: '32px' }}>
                      No {incidentFilter.toLowerCase()} incidents recorded in the lifecycle state machine.
                    </td>
                  </tr>
                ) : (
                  filteredIncidents.map((inc) => {
                    const isCongestion = inc.event_type === 'CONGESTION'
                    const isBurst = inc.event_type === 'TRAFFIC_BURST'
                    const isVM = inc.event_type === 'VM_CPU_SATURATION'
                    const isCritical = inc.severity === 'CRITICAL'

                    return (
                      <tr key={inc.incident_id}>
                        <td>
                          <span className="nw-mono" style={{ color: 'var(--cyan)', fontSize: '9.5px' }}>
                            {inc.incident_id}
                          </span>
                        </td>
                        <td>
                          <strong style={{ fontSize: '10.5px', color: '#e8f0f6' }}>
                            {inc.event_type}
                          </strong>
                        </td>
                        <td>
                          <span style={{ color: '#9db4c4', fontSize: '10px' }}>{inc.target}</span>
                        </td>
                        <td>
                          <span
                            className={`nw-badge ${isCritical ? 'critical' : 'warning'}`}
                            style={{
                              fontSize: '9px',
                              padding: '2px 6px',
                              borderRadius: '3px',
                              background: isCritical ? 'rgba(240,120,121,0.12)' : 'rgba(244,181,104,0.12)',
                            }}
                          >
                            {inc.severity}
                          </span>
                        </td>
                        <td>
                          {inc.state === 'ACTIVE' ? (
                            <span className="nw-live-chip" style={{ color: 'var(--red)' }}>
                              <span style={{ background: 'var(--red)', boxShadow: '0 0 8px var(--red)' }} />
                              ACTIVE
                            </span>
                          ) : (
                            <span className="nw-badge healthy" style={{ fontSize: '9px' }}>
                              <Check size={10} />
                              RESOLVED
                            </span>
                          )}
                        </td>
                        <td>
                          <strong style={{ color: isCritical ? 'var(--red)' : 'var(--amber)', fontSize: '10.5px' }}>
                            {inc.peak_value.toFixed(1)}{isBurst ? ' Mbps' : '%'}
                          </strong>
                        </td>
                        <td>
                          <span style={{ fontSize: '10px', color: inc.time_to_mitigation_sec ? '#a897ff' : '#577284' }}>
                            {inc.time_to_mitigation_sec !== null && inc.time_to_mitigation_sec !== undefined ? `${inc.time_to_mitigation_sec.toFixed(1)}s` : '—'}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: '10px', color: inc.time_to_recovery_sec ? 'var(--green)' : '#577284' }}>
                            {inc.time_to_recovery_sec !== null && inc.time_to_recovery_sec !== undefined ? `${inc.time_to_recovery_sec.toFixed(1)}s` : '—'}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: '10px', color: '#88a3b5' }}>
                            {inc.state === 'ACTIVE' ? 'Ongoing' : formatDuration(inc.duration_sec)}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: '9.5px', color: '#6b8697' }}>
                            {formatDateTime(inc.start_time)}
                          </span>
                        </td>
                        <td style={{ maxWidth: '240px', whiteSpace: 'normal' }}>
                          <span style={{ fontSize: '9.5px', color: '#8ea6b6', lineHeight: 1.3 }}>
                            {inc.description}
                          </span>
                        </td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Pure SVG Interactive Time-Series Chart Component
// ---------------------------------------------------------------------------

function BottleneckSvgChart({
  bottleneck,
  onHoverPoint,
}: {
  bottleneck: BottleneckHistoryResponse
  onHoverPoint: (point: BottleneckPoint | null, pos: { x: number; y: number } | null) => void
}) {
  const width = 760
  const height = 210
  const padLeft = 40
  const padRight = 15
  const padTop = 15
  const padBottom = 25

  const plotW = width - padLeft - padRight
  const plotH = height - padTop - padBottom

  const points = bottleneck.points
  const startTime = bottleneck.start_time
  const endTime = bottleneck.end_time
  const span = Math.max(1, endTime - startTime)

  const getX = useCallback((ts: number) => {
    const ratio = Math.max(0, Math.min(1, (ts - startTime) / span))
    return padLeft + ratio * plotW
  }, [startTime, span, padLeft, plotW])

  const getY = useCallback((val: number) => {
    const clamped = Math.max(0, Math.min(100, val))
    return padTop + plotH - (clamped / 100) * plotH
  }, [padTop, plotH])

  // Build segmented paths to never draw lines across unmeasured gaps
  const segments = useMemo(() => {
    const res: BottleneckPoint[][] = []
    let cur: BottleneckPoint[] = []

    for (const p of points) {
      if (p.measured && p.utilization_pct !== null && p.utilization_pct !== undefined) {
        cur.push(p)
      } else {
        if (cur.length > 0) {
          res.push(cur)
          cur = []
        }
      }
    }
    if (cur.length > 0) {
      res.push(cur)
    }
    return res
  }, [points])

  const y80 = getY(80)

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      style={{ width: '100%', height: 'auto', display: 'block', overflow: 'visible' }}
    >
      <defs>
        <linearGradient id="nw-util-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="var(--cyan)" stopOpacity="0.35" />
          <stop offset="100%" stopColor="var(--cyan)" stopOpacity="0.0" />
        </linearGradient>
      </defs>

      {/* Y-Axis Horizontal Grid Lines and Labels */}
      {[0, 25, 50, 75, 100].map((tick) => {
        const y = getY(tick)
        return (
          <g key={tick}>
            <line
              x1={padLeft}
              y1={y}
              x2={padLeft + plotW}
              y2={y}
              stroke="rgba(153, 190, 216, 0.08)"
              strokeDasharray={tick === 0 ? undefined : '2 3'}
            />
            <text
              x={padLeft - 6}
              y={y + 3}
              textAnchor="end"
              fill="#526d80"
              fontSize="9"
              fontFamily="monospace"
            >
              {tick}%
            </text>
          </g>
        )
      })}

      {/* 80% Congestion Threshold Line */}
      <line
        x1={padLeft}
        y1={y80}
        x2={padLeft + plotW}
        y2={y80}
        stroke="var(--amber)"
        strokeWidth="1.2"
        strokeDasharray="4 3"
        opacity="0.85"
      />
      <text
        x={padLeft + 6}
        y={y80 - 4}
        fill="var(--amber)"
        fontSize="8.5"
        fontWeight="600"
        letterSpacing="0.06em"
      >
        80% CONGESTION THRESHOLD
      </text>

      {/* Render Continuous Measured Path Segments */}
      {segments.map((seg, sIdx) => {
        if (seg.length === 1) {
          const pt = seg[0]
          return (
            <circle
              key={`dot-${sIdx}`}
              cx={getX(pt.timestamp)}
              cy={getY(pt.utilization_pct!)}
              r="3"
              fill="var(--cyan)"
            />
          )
        }

        const lineD = seg
          .map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${getX(p.timestamp).toFixed(1)} ${getY(p.utilization_pct!).toFixed(1)}`)
          .join(' ')

        const areaD = `${lineD} L ${getX(seg.at(-1)!.timestamp).toFixed(1)} ${padTop + plotH} L ${getX(seg[0].timestamp).toFixed(1)} ${padTop + plotH} Z`

        return (
          <g key={`seg-${sIdx}`}>
            <path d={areaD} fill="url(#nw-util-grad)" />
            <path
              d={lineD}
              fill="none"
              stroke="var(--cyan)"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </g>
        )
      })}

      {/* Data Point Markers & Hover Hit Targets */}
      {points.map((p, idx) => {
        if (!p.measured || p.utilization_pct === null || p.utilization_pct === undefined) return null
        const cx = getX(p.timestamp)
        const cy = getY(p.utilization_pct)
        const isRollup = p.resolution === '1h_rollup'
        const isCongested = (p.utilization_pct ?? 0) >= 80

        return (
          <g key={`pt-${idx}`}>
            {isRollup ? (
              <rect
                x={cx - 3}
                y={cy - 3}
                width="6"
                height="6"
                fill="#a897ff"
                stroke="#08141f"
                strokeWidth="1"
                transform={`rotate(45 ${cx} ${cy})`}
              />
            ) : (
              <circle
                cx={cx}
                cy={cy}
                r={points.length > 50 ? '2' : '3'}
                fill={isCongested ? 'var(--red)' : 'var(--cyan)'}
                stroke="#08141f"
                strokeWidth="1"
              />
            )}

            {/* Invisible Hit Target for Tooltip */}
            <circle
              cx={cx}
              cy={cy}
              r="8"
              fill="transparent"
              style={{ cursor: 'pointer' }}
              onMouseEnter={() => onHoverPoint(p, { x: cx, y: cy })}
              onMouseLeave={() => onHoverPoint(null, null)}
            />
          </g>
        )
      })}

      {/* X-Axis Time Ticks */}
      <text x={padLeft} y={height - 8} fill="#547185" fontSize="9" fontFamily="monospace">
        {formatTime(startTime)}
      </text>
      <text x={padLeft + plotW / 2} y={height - 8} textAnchor="middle" fill="#547185" fontSize="9" fontFamily="monospace">
        {formatTime(startTime + span / 2)}
      </text>
      <text x={padLeft + plotW} y={height - 8} textAnchor="end" fill="#547185" fontSize="9" fontFamily="monospace">
        {formatTime(endTime)}
      </text>
    </svg>
  )
}

type SupportedExperiment = 'flood' | 'burst' | 'cpu' | 'mem' | 'kill'
type SupportedHost = 'h1' | 'h2' | 'h3' | 'h4'
type SupportedRate = '5' | '8' | '10'

const EXPERIMENTS: { id: SupportedExperiment; label: string; desc: string; fixedDuration: number }[] = [
  { id: 'flood', label: 'Traffic Congestion Flood', desc: 'Saturates the 5 Mbps s1-s2 bottleneck with a UDP stream (python generator) to h5:5555', fixedDuration: 20 },
  { id: 'burst', label: 'Traffic Micro-Burst', desc: '3 Mbps micro-burst for 6 seconds to probe queue buffering', fixedDuration: 6 },
  { id: 'cpu', label: 'CPU Load Stress', desc: 'Spins worker threads to stress host VM CPU', fixedDuration: 20 },
  { id: 'mem', label: 'Memory Allocation Stress', desc: 'Allocates RAM until VM usage reaches ~90% (always leaves 400 MB free) to cross the 85% alert', fixedDuration: 20 },
  { id: 'kill', label: 'Kill Telemetry Agent', desc: 'Terminates agent process; auto-restarts after delay', fixedDuration: 20 },
]

function ExperimentCenter({
  statusData,
  onRefresh,
}: {
  statusData?: NetwatchStatus | null
  onRefresh?: () => void
}) {
  const [experiment, setExperiment] = useState<SupportedExperiment>('flood')
  const [host, setHost] = useState<SupportedHost>('h1')
  const [rate, setRate] = useState<SupportedRate>('8')
  const [submitting, setSubmitting] = useState(false)
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; message: string; timestamp: string } | null>(null)
  const [expStatus, setExpStatus] = useState<ExperimentStatusResponse | null>(null)
  const [expStatusError, setExpStatusError] = useState(false)
  // A trigger that returned HTTP 200 is only SUBMITTED until the topology itself reports it.
  const [pending, setPending] = useState<{ cmd: string; host: string; at: number } | null>(null)
  const [now, setNow] = useState(() => Date.now())

  const countdowns = Object.entries(statusData?.countdowns ?? {}).filter(([, sec]) => sec > 0)
  const currentExp = EXPERIMENTS.find((e) => e.id === experiment)!

  // Fetch real experiment lifecycle and recovery status from backend
  const loadExperimentStatus = useCallback(async () => {
    try {
      const data = await fetchExperimentStatus()
      setExpStatus(data)
      setExpStatusError(false)
    } catch {
      setExpStatusError(true)
    }
    setNow(Date.now())
  }, [])

  useEffect(() => {
    loadExperimentStatus()
    const timer = setInterval(loadExperimentStatus, 2000)
    return () => clearInterval(timer)
  }, [loadExperimentStatus])

  // Clear the SUBMITTED marker once the backend reports the experiment (active or in history).
  useEffect(() => {
    if (!pending || !expStatus || expStatus.source !== 'live') return
    const sinceSec = pending.at / 1000 - 5
    const matches = (r: ExperimentRecord | null | undefined) =>
      Boolean(r && r.cmd === pending.cmd && r.host === pending.host && r.start_time >= sinceSec)
    if (matches(expStatus.active) || expStatus.history.some(matches)) setPending(null)
  }, [expStatus, pending])

  const statusUnverified = expStatusError || !expStatus || expStatus.stale === true
  const pendingAgeSec = pending ? (now - pending.at) / 1000 : 0
  const pendingLabel = pending ? (pendingAgeSec > 10 ? 'UNVERIFIED' : 'SUBMITTED') : null

  // Single-experiment global concurrency check
  const isGlobalActive = Boolean(
    expStatus?.active && (expStatus.active.state === 'RUNNING' || expStatus.active.state === 'RECOVERING')
  )
  const activeExp = expStatus?.active

  const handleTrigger = async (e: React.FormEvent) => {
    e.preventDefault()
    if (submitting || isGlobalActive) return

    setSubmitting(true)
    setBanner(null)

    try {
      const result = await triggerExperiment({
        problem: experiment,
        host,
        rate: experiment === 'flood' ? rate : undefined,
      })
      setBanner({
        type: 'success',
        message: result.message || `${currentExp.label} successfully triggered on ${host}.`,
        timestamp: new Date().toLocaleTimeString(),
      })
      setPending({ cmd: experiment, host, at: Date.now() })
      loadExperimentStatus()
      if (onRefresh) onRefresh()
    } catch (err) {
      setBanner({
        type: 'error',
        message: err instanceof Error ? err.message : 'Failed to trigger experiment.',
        timestamp: new Date().toLocaleTimeString(),
      })
    } finally {
      setSubmitting(false)
    }
  }

  const CHECK_LABELS: Record<string, string> = {
    process_terminated: 'Experiment Process Cleaned Up',
    telemetry_fresh: 'All 4 Hosts Fresh Telemetry (<5s)',
    packet_loss_recovered: 'UDP Sequence Loss Normal (<=5%)',
    bottleneck_util_recovered: 'Bottleneck Utilization Returned to Baseline',
    vm_resources_baseline: 'VM-wide CPU & Memory Returned to Baseline',
    // Legacy label kept for backward compatibility with older experiment records
    cpu_mem_baseline: 'VM CPU & Memory Returned to Baseline',
    mitigation_cleared: 'Controller Temporary Drop Rules Cleared',
    agent_restarted: 'Target Telemetry Agent Restarted & Healthy',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      <div className="nw-experiment-layout">
        <div className="nw-panel nw-experiment-form">
          <PanelHeading
            title="Inject Experiment"
            icon={<FlaskConical size={16} />}
            meta="SUPERVISED IPC CONTROL SOCKET"
          />

          {banner && (
            <div style={{
              margin: '14px 16px 0',
              padding: '10px 12px',
              borderRadius: '7px',
              fontSize: '11px',
              border: banner.type === 'success' ? '1px solid rgba(77,214,154,.3)' : '1px solid rgba(240,120,121,.3)',
              background: banner.type === 'success' ? 'rgba(77,214,154,.08)' : 'rgba(240,120,121,.08)',
              color: banner.type === 'success' ? 'var(--green)' : 'var(--red)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '10px',
            }}>
              <span><strong>[{banner.timestamp}]</strong> {banner.message}</span>
              <button
                onClick={() => setBanner(null)}
                style={{ background: 'transparent', border: 0, color: 'inherit', cursor: 'pointer', padding: 0 }}
              >
                <X size={14} />
              </button>
            </div>
          )}

          <form onSubmit={handleTrigger} className="nw-form two-col">
            <label>
              Experiment type
              <select
                value={experiment}
                onChange={(e) => setExperiment(e.target.value as SupportedExperiment)}
                disabled={submitting || isGlobalActive}
              >
                {EXPERIMENTS.map((exp) => (
                  <option key={exp.id} value={exp.id}>
                    {exp.label}
                  </option>
                ))}
              </select>
            </label>

            <label>
              Target host
              <select
                value={host}
                onChange={(e) => setHost(e.target.value as SupportedHost)}
                disabled={submitting || isGlobalActive}
              >
                <option value="h1">h1 (10.0.0.1)</option>
                <option value="h2">h2 (10.0.0.2)</option>
                <option value="h3">h3 (10.0.0.3)</option>
                <option value="h4">h4 (10.0.0.4)</option>
              </select>
            </label>

            <label>
              Transmission rate
              {experiment === 'flood' ? (
                <select
                  value={rate}
                  onChange={(e) => setRate(e.target.value as SupportedRate)}
                  disabled={submitting || isGlobalActive}
                >
                  <option value="5">5 Mbps</option>
                  <option value="8">8 Mbps (Default)</option>
                  <option value="10">10 Mbps</option>
                </select>
              ) : experiment === 'burst' ? (
                <input value="Fixed: 3 Mbps micro-burst" disabled />
              ) : experiment === 'mem' ? (
                <input value="Fixed: 500 MB allocation" disabled />
              ) : (
                <input value="Not applicable" disabled />
              )}
            </label>

            <label>
              Duration
              <input
                value={`${currentExp.fixedDuration} seconds (enforced by supervisor)`}
                disabled
                title="Experiment durations are enforced by supervisor watchdog policy."
              />
            </label>

            <div style={{ gridColumn: '1 / -1', marginTop: '4px' }}>
              <button
                type="submit"
                className="nw-primary-button"
                disabled={submitting || isGlobalActive}
                style={{ width: '100%', justifyContent: 'center', padding: '10px 14px' }}
              >
                {submitting ? (
                  <>
                    <RefreshCw size={14} className="spin" /> Injecting experiment...
                  </>
                ) : isGlobalActive ? (
                  <>
                    <Clock3 size={14} /> Experiment {activeExp?.cmd.toUpperCase()} active on {activeExp?.host} ({activeExp?.state})
                  </>
                ) : (
                  <>
                    <Play size={14} /> Inject {currentExp.label}
                  </>
                )}
              </button>
            </div>
          </form>

          <p className="nw-form-note" style={{ margin: '0 16px 16px' }}>
            {currentExp.desc}. Governed by global single-experiment concurrency and multi-metric recovery verification.
          </p>
        </div>

        <div className="nw-panel">
          <PanelHeading
            title="Supervised Recovery Monitor"
            icon={<ShieldCheck size={16} />}
            meta={activeExp ? `${activeExp.state}` : pendingLabel ?? (statusUnverified ? 'UNKNOWN' : 'IDLE')}
          />

          <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {statusUnverified && (
              <div role="status" style={{ padding: '10px 12px', borderRadius: '7px', fontSize: '11px', background: 'rgba(244,181,104,.08)', border: '1px solid rgba(244,181,104,.3)', color: 'var(--amber)' }}>
                {expStatusError
                  ? 'Experiment status API unreachable — state UNKNOWN.'
                  : expStatus?.source === 'file'
                    ? 'Topology not reachable — showing the last saved snapshot. State is UNVERIFIED.'
                    : 'No live experiment status available — state UNKNOWN.'}
              </div>
            )}
            {!activeExp && pendingLabel && (
              <div role="status" style={{ padding: '10px 12px', borderRadius: '7px', fontSize: '11px', background: pendingLabel === 'SUBMITTED' ? 'rgba(89,216,255,.08)' : 'rgba(240,120,121,.08)', border: pendingLabel === 'SUBMITTED' ? '1px solid rgba(89,216,255,.3)' : '1px solid rgba(240,120,121,.3)', color: pendingLabel === 'SUBMITTED' ? 'var(--cyan)' : 'var(--red)' }}>
                {pendingLabel === 'SUBMITTED'
                  ? `${pending!.cmd.toUpperCase()} on ${pending!.host} SUBMITTED — waiting for the topology to report it.`
                  : `${pending!.cmd.toUpperCase()} on ${pending!.host} was accepted but has not appeared in live experiment state after 10s — UNVERIFIED.`}
              </div>
            )}
            {activeExp ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                <div style={{
                  padding: '12px',
                  borderRadius: '7px',
                  background: activeExp.state === 'RECOVERING' ? 'rgba(89,216,255,.08)' : 'rgba(244,181,104,.08)',
                  border: activeExp.state === 'RECOVERING' ? '1px solid rgba(89,216,255,.3)' : '1px solid rgba(244,181,104,.3)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}>
                  <div>
                    <div style={{ fontSize: '13px', fontWeight: 600, color: '#e6f0fa' }}>
                      {activeExp.cmd.toUpperCase()} on {activeExp.host}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '2px', fontFamily: 'monospace' }}>
                      ID: {activeExp.exp_id.slice(0, 8)} {activeExp.child_pid ? `| PID: ${activeExp.child_pid}` : ''}
                    </div>
                  </div>
                  <span style={{
                    padding: '4px 8px',
                    borderRadius: '4px',
                    fontSize: '10px',
                    fontWeight: 700,
                    letterSpacing: '0.05em',
                    textTransform: 'uppercase',
                    background: activeExp.state === 'RECOVERING' ? 'var(--cyan)' : 'var(--amber)',
                    color: '#071019',
                  }}>
                    {activeExp.state}
                  </span>
                </div>

                <div>
                  <div style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--muted)', marginBottom: '8px' }}>
                    Recovery Verification Checklist:
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {Object.entries(CHECK_LABELS).map(([key, label]) => {
                      if (key === 'agent_restarted' && activeExp.cmd !== 'kill') return null
                      const check = activeExp.recovery_checks?.[key]
                      const isPassing = check?.passed
                      const hasEvaluated = Boolean(check)

                      return (
                        <div key={key} style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          padding: '6px 10px',
                          borderRadius: '5px',
                          background: 'rgba(255,255,255,.02)',
                          fontSize: '11px',
                          border: '1px solid rgba(153,190,216,.06)',
                        }}>
                          <span style={{ color: '#c5d9e8' }}>{label}</span>
                          <span style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: '5px',
                            fontWeight: 600,
                            color: !hasEvaluated ? 'var(--muted)' : isPassing ? 'var(--green)' : 'var(--red)',
                          }}>
                            {!hasEvaluated ? (
                              'Pending'
                            ) : isPassing ? (
                              <>
                                <Check size={13} /> Passing
                              </>
                            ) : (
                              <>
                                <X size={13} /> Failed
                              </>
                            )}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              </div>
            ) : (
              <div className="nw-empty" style={{ minHeight: '220px' }}>
                <Clock3 size={24} />
                <strong>No active experiments</strong>
                <span>All network links, host agents, and controller flow rules are healthy and operating in normal state.</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Experiment History Audit Log */}
      <div className="nw-panel">
        <PanelHeading
          title="Experiment Recovery Audit Log"
          icon={<TerminalSquare size={16} />}
          meta={`${expStatus?.history?.length ?? 0} COMPLETED${statusUnverified ? ' · UNVERIFIED' : ''}`}
        />

        <div className="nw-table-wrap">
          {expStatus?.history && expStatus.history.length > 0 ? (
            <table className="nw-table">
              <thead>
                <tr>
                  <th>Experiment ID</th>
                  <th>Type</th>
                  <th>Target</th>
                  <th>Duration</th>
                  <th>Final State</th>
                  <th>Completed At</th>
                  <th>Verification Notes</th>
                </tr>
              </thead>
              <tbody>
                {expStatus.history.map((rec) => {
                  const isRecovered = rec.state === 'RECOVERED'
                  const isTimeout = rec.state === 'RECOVERY_TIMEOUT'
                  const tone = isRecovered ? 'green' : isTimeout ? 'amber' : 'red'
                  const toneBg = isRecovered ? 'rgba(77,214,154,.1)' : isTimeout ? 'rgba(244,181,104,.1)' : 'rgba(240,120,121,.1)'
                  const toneBorder = isRecovered ? '1px solid rgba(77,214,154,.25)' : isTimeout ? '1px solid rgba(244,181,104,.25)' : '1px solid rgba(240,120,121,.25)'
                  const stateLabel = isRecovered ? 'RECOVERED' : isTimeout ? 'TIMED OUT' : rec.state === 'RECOVERY_FAILED' ? 'FAILED' : rec.state
                  return (
                    <tr key={rec.exp_id}>
                      <td style={{ fontFamily: 'monospace', color: 'var(--cyan)' }}>
                        {rec.exp_id.slice(0, 8)}
                      </td>
                      <td style={{ fontWeight: 600, textTransform: 'uppercase' }}>
                        {rec.cmd}
                      </td>
                      <td>{rec.host}</td>
                      <td>{rec.duration}s</td>
                      <td>
                        <span style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '2px 6px',
                          borderRadius: '4px',
                          fontSize: '10px',
                          fontWeight: 700,
                          background: toneBg,
                          color: `var(--${tone})`,
                          border: toneBorder,
                        }}>
                          {isRecovered ? <Check size={11} /> : <X size={11} />}
                          {stateLabel}
                        </span>
                      </td>
                      <td style={{ fontSize: '11px', color: 'var(--muted)' }}>
                        {rec.completed_at ? new Date(rec.completed_at * 1000).toLocaleTimeString() : '—'}
                      </td>
                      <td style={{ fontSize: '11px', color: rec.failed_checks?.length ? 'var(--red)' : 'var(--muted)' }}>
                        {rec.error ? (
                          `Error: ${rec.error}`
                        ) : rec.failed_checks?.length ? (
                          `Failed: ${rec.failed_checks.join(', ')}`
                        ) : (
                          'All recovery checks passed (2/2 cycles)'
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            <div className="nw-empty" style={{ padding: '24px' }}>
              <TerminalSquare size={20} />
              <span>No experiment history recorded yet.</span>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
function PanelHeading({ title, icon, meta }: { title: string; icon: React.ReactNode; meta: string }) { return <div className="nw-panel-heading"><div className="nw-panel-title"><span>{icon}</span><h3>{title}</h3></div><span className="nw-panel-meta">{meta}</span></div> }
function HealthItem({ label, value, status }: { label: string; value: string; status: string }) { return <div className="nw-health-row"><span className={`nw-severity-dot ${status}`} /><div><span>{label}</span><strong>{value}</strong></div></div> }
