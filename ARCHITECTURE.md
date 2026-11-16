# NETWATCH Architecture

## Topology (`topo/monitoring_topo.py`)
```
 h1 ─┐
 h2 ─┤ 100 Mbps          5 Mbps, 5 ms, queue 100          100 Mbps ┌─ h5 (monitoring server, UDP 9999)
 h3 ─┼──────── s1 ─────────────────────────────────── s2 ─────────┤
 h4 ─┘  (s1 ports 1–4 = h1–h4, port 5 = s2)                      └─ h6 (spare)
```

## Data path
1. **Agents** (`agents/telemetry_agent.py`, on h1–h4) send JSON over UDP to `10.0.0.5:9999`: host, sequence number, CPU %, memory %. These are **VM-wide** values, since all Mininet hosts share one kernel.
2. **Server** (`server/monitoring_server.py`, on h5) computes per-host loss from sequence gaps and raises CPU (85 %), MEM (85 %), loss and silence (>5 s) alerts. It writes `/tmp/server_state.json` and `/tmp/monitor_alerts.log`.
3. **Ryu** (`controller/monitoring_controller.py`) runs every 3 s:
   - sends `OFPPortStatsRequest` and `OFPFlowStatsRequest`;
   - computes utilization = Δbytes×8/Δt ÷ capacity on the s1↔s2 ports;
   - computes each host's sending rate from rx bytes on its s1 port;
   - writes `/tmp/sdn_metrics.json`, `/tmp/sdn_portstats.json`, `/tmp/sdn_flows.json` and `/tmp/sdn_alerts.log`.
4. **Mitigation:**
   - Trigger: utilization > 80 % for 2 polls, and a sender above 1 Mbps.
   - Action: a priority-100 DROP rule `ipv4_src=<top sender>, udp_dst=5555 → h5`, with `hard_timeout=15`, a NETWATCH cookie and `SEND_FLOW_REM`.
   - Removal: deleted by cookie once the link is uncongested **and** the sender's own rate has fallen. Otherwise it expires after 15 s and is re-installed if the flood continues.
5. **Flask** (`dashboard.py`, `127.0.0.1:5000`) reads the snapshot files, rejects stale or invalid samples (never presents them as 0), stores samples, rollups and incidents in SQLite (`storage/netwatch_db.py`), and serves `/api/*`.
6. **Experiments:**
   - Flask `POST /trigger` → Unix socket `/tmp/sdn_control.sock` (group `sdn-ipc`) → topology process.
   - The topology validates input, starts the load generator inside the host, and supervises it.
   - Lifecycle: `RUNNING → RECOVERING →` one of `RECOVERED` (2 consecutive fresh passing samples within 15 s), `RECOVERY_FAILED` (a check failed), or `RECOVERY_TIMEOUT` (nothing failed, but recovery was never confirmed).
   - Only one experiment can run at a time (409 otherwise).
   - State is exported atomically to `/tmp/sdn_experiments.json`.
   - `GET /api/experiments/status` asks the topology **live** over IPC first (`source: "live"`). If the topology is unreachable it returns the file with `stale: true`, and the UI then shows UNKNOWN / UNVERIFIED.
7. **Frontend** (`frontend/`, Next.js): proxies `/api/*` and `/trigger` to Flask (`next.config.mjs`) and polls every 2–5 s.

## Seven pages
| Route | Page | Main APIs |
|---|---|---|
| `/` | Command Center | `/api/status`, `/api/analytics/summary` |
| `/topology-builder` | Topology Builder (local draft editor, localStorage; does **not** deploy) | `/api/topology` (load live) |
| `/live-network` | Live Network | `/api/topology`, `/api/network/links`, `/api/network/flows` |
| `/host-monitoring` | Host Monitoring | `/api/status`, `/api/history/hosts` |
| `/network-intelligence` | Network Intelligence | `/api/history/*`, `/api/analytics/*` |
| `/experiment-center` | Experiment Center | `/trigger`, `/api/experiments/status` |
| `/incident-management` | Incident Management | `/api/status` alerts, `/api/analytics/incidents` |
