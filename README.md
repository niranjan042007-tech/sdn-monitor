# NETWATCH — SDN Network Monitoring and Alert System

## 1. Overview
NETWATCH monitors an emulated SDN network built with Mininet and Open vSwitch, controlled by Ryu using OpenFlow 1.3.
- Host agents send CPU, memory and sequence-numbered UDP telemetry.
- The controller polls port and flow statistics and computes bottleneck utilization.
- On sustained congestion, it blocks the **top sender** with a temporary OpenFlow DROP rule.
- A Flask API stores history in SQLite. A Next.js dashboard (7 pages) shows live state, history, incidents and supervised experiments.

## 2. Architecture
See `ARCHITECTURE.md`. In short:
`agents (h1–h4) --UDP:9999--> server (h5) --/tmp/server_state.json--> Flask :5000 <--/api--> Next.js :3000`
`OVS s1/s2 <--OpenFlow 1.3--> Ryu --/tmp/sdn_*.json--> Flask`
`Flask --Unix socket /tmp/sdn_control.sock--> topology process (experiments + recovery supervisor)`

## 3. Technologies
Python 3, Ryu (in a Python 3.9 venv), OpenFlow 1.3, Mininet, Open vSwitch, Flask, SQLite, Next.js 16 / React 19 / @xyflow/react, Bash, pytest, Playwright (for evidence screenshots).

## 4. Folder structure
```
agents/telemetry_agent.py        UDP telemetry sender (runs inside h1–h4)
server/monitoring_server.py      UDP receiver on h5: loss, CPU/MEM alerts, host-silence
controller/monitoring_controller.py  Ryu app: L2 forwarding, port/flow stats, congestion + mitigation
topo/monitoring_topo.py          Mininet topology + control socket + experiment supervisor
storage/netwatch_db.py           SQLite storage, rollups, retention
dashboard.py                     Flask REST API (+ a minimal legacy HTML page at /)
frontend/                        Next.js dashboard (7 routes)
run_system.sh                    One-command startup/shutdown
tests/                           pytest suite + load generators (cpu/mem/congestion)
tools/capture_screenshots.py     Playwright evidence capture of the running app
data/netwatch.db                 Historical SQLite database
evidence/                        Test output, reports, screenshots
```

## 5. Prerequisites (Ubuntu VM)
`sudo apt install mininet openvswitch-switch python3-venv python3-pip`. Node.js 20+ and pnpm (or npm).
For Ryu, follow `UBUNTU_SETUP.md`: Ryu needs an isolated **Python 3.9** venv (`ryu39-env`). `run_system.sh` looks for it at `./ryu39-env` or `~/netwatch-latest/ryu39-env`.

## 6–8. Installation
```bash
cd ~/netwatch-codex
python3 -m pip install flask pytest            # add --break-system-packages on Ubuntu 24.04+
# Ryu venv: see UBUNTU_SETUP.md (python3.9 -m venv ryu39-env && ryu39-env/bin/pip install ...)
cd frontend && pnpm install && cd ..           # or: npm install
# .env.example lists the optional NETWATCH_* overrides (export them in your shell; defaults work)
```

## 9–13. Running
**Everything at once (recommended):**
```bash
sudo ./run_system.sh        # Ryu → Mininet topology (+ agents on h1–h4, server on h5) → Flask :5000
cd frontend && pnpm dev     # second terminal → http://localhost:3000
```
`run_system.sh` starts Ryu, the topology (which launches the telemetry agents and the monitoring server inside the hosts), and Flask. Press Ctrl+C to stop all of them. If anything is left over, run `sudo mn -c`.

**Manually** (for debugging):
1. `PYTHONPATH=. ryu39-env/bin/ryu-manager controller.monitoring_controller`
2. `sudo python3 topo/monitoring_topo.py`
3. `python3 dashboard.py`
4. `cd frontend && pnpm dev`

## 14. Tests
```bash
python3 -m pytest -q          # 216 tests; does not touch the live /tmp state files
```
Screenshots and evidence (with the system running): `python3 tools/capture_screenshots.py [--run-burst]`. See `TESTING_GUIDE.md`.

## 15. Troubleshooting
| Symptom | Fix |
|---|---|
| Header shows "Stale data" / utilization "—" | Ryu or the server is not writing `/tmp/sdn_metrics.json` or `server_state.json`. Check `/tmp/sdn_controller.log` and `/tmp/server.log` |
| Experiment Center says **UNKNOWN / UNVERIFIED** | The topology control socket is unreachable. Is `monitoring_topo.py` running? Is your user in the `sdn-ipc` group? (log out and back in after `run_system.sh` adds it) |
| `/trigger` returns 503 | Same as above: the topology is not running |
| `/trigger` returns 409 | Another experiment is RUNNING or RECOVERING; wait for it to finish |
| Old experiment history after a restart | Fixed: the clean start now deletes `/tmp/sdn_experiments.json`. Set `NETWATCH_PRESERVE_RUNTIME_STATE=1` to keep it |
| Leftover Mininet | `sudo mn -c` |

## 16. Known limitations
See `evidence/known_limitations.md`. Key points:
- Polling every 3 s means mitigation takes about 6 s.
- The DROP rule matches the demo flood (UDP 5555 → h5).
- CPU and memory are VM-wide because Mininet hosts share one kernel.
- Incident acknowledgements are browser-local.
- Topology Builder edits are a **local draft**; they do not change the running Mininet.
