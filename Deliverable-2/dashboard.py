#!/usr/bin/env python3
"""Grafana-Style Web Dashboard (NETWATCH)."""
import json
import math
import re
import os
import socket as _socket
import sys
import tempfile
import time
import threading
from flask import Flask, render_template_string, jsonify, request
from storage.netwatch_db import NetwatchDB

# ---------------------------------------------------------------------------
# IPC helper — sends a validated command to the running Mininet topology via
# the Unix domain socket opened by monitoring_topo.py.
# No second Mininet instance is ever created (fix for Bug B1).
# ---------------------------------------------------------------------------

CONTROL_SOCK = os.environ.get('NETWATCH_CONTROL_SOCKET', '/tmp/sdn_control.sock')


def _send_control_command(cmd, host, rate='8', duration=20):
    """Send a JSON command to the topology control socket.

    Returns a dict: {'status': 'ok'|'error', 'message': '...'}.
    If the socket is not available (topology not running), returns an error.
    """
    payload = json.dumps({
        'cmd': cmd,
        'host': host,
        'rate': str(rate),
        'duration': int(duration),
    }) + '\n'

    try:
        sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(CONTROL_SOCK)
        sock.sendall(payload.encode())

        raw = b''
        while not raw.endswith(b'\n'):
            chunk = sock.recv(4096)
            if not chunk:
                break
            raw += chunk
        sock.close()
        return json.loads(raw.decode().strip())
    except FileNotFoundError:
        return {'status': 'error', 'message': 'Control socket not found — is the topology running?'}
    except _socket.timeout:
        return {'status': 'error', 'message': 'Control socket timed out'}
    except Exception as exc:
        return {'status': 'error', 'message': f'IPC error: {exc}'}

app = Flask(__name__)
active_problems = {}
lock = threading.Lock()

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>NETWATCH - SDN Monitor</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg: #111217; --panel: #181b1f; --border: #2c3235;
            --text: #d8d9da; --text-dim: #8e9297;
            --blue: #3274d9; --green: #73bf69; --red: #f2495c; --yellow: #fade2a;
        }
        body { font-family: 'Inter', 'Segoe UI', Arial, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 20px; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 20px; }
        .header h1 { margin: 0; font-size: 1.5em; font-weight: 500; color: var(--text); }
        .grid-container { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }
        .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 4px; padding: 15px; }
        .panel-title { font-size: 0.9em; color: var(--text-dim); margin-bottom: 15px; font-weight: 500; text-transform: uppercase; letter-spacing: 0.5px; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 10px; margin-bottom: 20px; }
        .stat-box { background: var(--panel); border: 1px solid var(--border); padding: 15px; border-radius: 4px; text-align: center; }
        .stat-value { font-size: 2em; font-weight: 600; margin: 10px 0; }
        .stat-label { font-size: 0.8em; color: var(--text-dim); }
        .stat-value.green { color: var(--green); }
        .stat-value.red { color: var(--red); }
        .stat-value.yellow { color: var(--yellow); }
        .chart-container { position: relative; height: 250px; width: 100%; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9em; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid var(--border); }
        th { color: var(--text-dim); font-weight: 500; }
        .btn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
        .prob-btn { background: var(--border); color: var(--text); border: 1px solid transparent; padding: 12px; border-radius: 4px; cursor: pointer; font-weight: 500; transition: 0.2s; text-align: left; position: relative; }
        .prob-btn:hover { background: #3d444b; }
        .prob-btn.active { background: rgba(242, 73, 92, 0.2); border-color: var(--red); color: var(--red); }
        .countdown { position: absolute; right: 10px; top: 50%; transform: translateY(-50%); font-weight: bold; }
        .alerts-box { max-height: 200px; overflow-y: auto; font-family: monospace; font-size: 0.8em; color: var(--text-dim); background: #0b0c0e; padding: 10px; border-radius: 4px; border: 1px solid var(--border); }
        .alert-line { margin: 4px 0; }
        .alert-line.high { color: var(--red); }
        select { width: 100%; padding: 8px; margin-bottom: 10px; background: var(--bg); border: 1px solid var(--border); color: var(--text); border-radius: 4px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>NETWATCH - SDN Monitoring</h1>
        <div style="color: var(--text-dim);">Refresh: 2s | Status: <span id="global-status" style="color: var(--green);">Online</span></div>
    </div>

    <div class="stat-grid">
        <div class="stat-box">
            <div class="stat-label">Bottleneck Utilization</div>
            <div class="stat-value" id="util-val">0%</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Active Problems</div>
            <div class="stat-value yellow" id="active-probs">0</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Network Health</div>
            <div class="stat-value green" id="net-health">Healthy</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Mitigation State</div>
            <div class="stat-value green" id="mitigation-state">Idle</div>
        </div>
    </div>

    <div class="grid-container">
        <div style="display: flex; flex-direction: column; gap: 20px;">
            <div class="panel">
                <div class="panel-title">Network Utilization (Bottleneck)</div>
                <div class="chart-container"><canvas id="utilChart"></canvas></div>
            </div>
            
            <div class="panel">
                <div class="panel-title">Live Telemetry</div>
                <p style="font-size:12px;color:var(--text-muted);">
                    CPU and memory are shared VM-wide readings. Host loss is host-specific;
                    WARN may indicate elevated VM resources.
                </p>
                <table id="telemetry-table">
                    <tr><th>Host</th><th>VM CPU % (shared)</th><th>VM MEM % (shared)</th><th>Host Loss %</th><th>Status</th></tr>
                </table>
            </div>
        </div>

        <div style="display: flex; flex-direction: column; gap: 20px;">
            <div class="panel">
                <div class="panel-title">Problem Injection Panel</div>
                <select id="sel-host">
                    <option value="h1">Target: h1</option>
                    <option value="h2">Target: h2</option>
                    <option value="h3">Target: h3</option>
                    <option value="h4">Target: h4</option>
                </select>
                <select id="sel-rate">
                    <option value="8">8 Mbps Flood</option>
                    <option value="5">5 Mbps Flood</option>
                    <option value="10">10 Mbps Flood</option>
                </select>
                <div class="btn-grid" id="btn-container"></div>
            </div>

            <div class="panel">
                <div class="panel-title">Live Alerts (Controller + Server)</div>
                <div class="alerts-box" id="alerts">No alerts yet.</div>
            </div>
        </div>
    </div>

    <div class="panel" style="margin-top:20px;">
        <div class="panel-title">Incident & Anomaly History</div>
        <div style="overflow-x:auto;">
            <table id="incident-table">
                <tr>
                    <th>Time</th>
                    <th>Event</th>
                    <th>Severity</th>
                    <th>Target</th>
                    <th>Peak Value</th>
                    <th>Duration</th>
                    <th>State</th>
                </tr>
            </table>
        </div>
        <div id="incident-updated"
             style="font-size:11px;color:var(--text-dim);margin-top:10px;">
            Waiting for incident data...
        </div>
    </div>    
<script>
        const problems = [
            { id: "flood", name: "🌊 Flood Attack", desc: "Send UDP flood" },
            { id: "cpu", name: "🔥 CPU Stress", desc: "Max out CPU" },
            { id: "mem", name: "💾 MEM Stress", desc: "Fill RAM to ~90%" },
            { id: "kill", name: "💀 Kill Agent", desc: "Silent Host" },
            { id: "burst", name: "⚡ Micro-Burst", desc: "3 Mbps for 6s" },
        ];
        const container = document.getElementById('btn-container');
        problems.forEach(p => {
            const btn = document.createElement('button');
            btn.className = 'prob-btn';
            btn.id = 'btn-' + p.id;
            btn.innerHTML = `<div>${p.name}</div><div style="font-size:0.8em;color:var(--text-dim);margin-top:5px;">${p.desc}</div><div class="countdown" id="cd-${p.id}" style="display:none;">20s</div>`;
            btn.onclick = () => triggerProblem(p.id);
            container.appendChild(btn);
        });

        const ctx = document.getElementById('utilChart').getContext('2d');
        const utilChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Utilization %',
                    data: [],
                    borderColor: '#3274d9',
                    backgroundColor: 'rgba(50, 116, 217, 0.1)',
                    borderWidth: 2,
                    pointRadius: 0,
                    fill: true,
                    tension: 0.4
                }, {
                    label: '80% Threshold',
                    data: [],
                    borderColor: '#f2495c',
                    borderDash: [5, 5],
                    borderWidth: 1,
                    pointRadius: 0,
                    fill: false
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                scales: { 
                    y: { beginAtZero: true, max: 110, grid: { color: '#2c3235' }, ticks: { color: '#8e9297' } },
                    x: { grid: { display: false }, ticks: { color: '#8e9297', maxTicksLimit: 8 } }
                },
                plugins: { legend: { labels: { color: '#d8d9da' } } },
                animation: false
            }
        });

        function triggerProblem(id) {
            const host = document.getElementById('sel-host').value;
            const rate = document.getElementById('sel-rate').value;
            fetch('/trigger', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ problem: id, host: host, rate: rate })
            });
        }


        function fetchIncidents() {
            fetch('/api/analytics/incidents?limit=20')
                .then(response => {
                    if (!response.ok) throw new Error('API request failed');
                    return response.json();
                })
                .then(data => {
                    const table = document.getElementById('incident-table');

                    let html = `
                        <tr>
                            <th>Time</th>
                            <th>Event</th>
                            <th>Severity</th>
                            <th>Target</th>
                            <th>Peak Value</th>
                            <th>Duration</th>
                            <th>State</th>
                        </tr>`;

                    for (const incident of data.incidents) {
                        const time = new Date(incident.start_time * 1000)
                            .toLocaleTimeString();

                        const peak = incident.event_type === 'TRAFFIC_BURST'
                            ? `${Number(incident.peak_value).toFixed(2)} Mbps`
                            : `${Number(incident.peak_value).toFixed(2)}`;

                        const duration = incident.duration_sec == null
                            ? 'Ongoing'
                            : `${Number(incident.duration_sec).toFixed(1)} s`;

                        const severityColor = incident.severity === 'CRITICAL'
                            ? 'var(--red)' : 'var(--yellow)';

                        const stateColor = incident.state === 'ACTIVE'
                            ? 'var(--red)' : 'var(--green)';

                        html += `
                            <tr>
                                <td>${time}</td>
                                <td>${incident.event_type}</td>
                                <td style="color:${severityColor}">${incident.severity}</td>
                                <td>${incident.target}</td>
                                <td>${peak}</td>
                                <td>${duration}</td>
                                <td style="color:${stateColor}">${incident.state}</td>
                            </tr>`;
                    }

                    table.innerHTML = html;
                    document.getElementById('incident-updated').innerText =
                        `Showing ${data.incidents.length} incidents | Updated: ${new Date().toLocaleTimeString()}`;
                })
                .catch(error => {
                    document.getElementById('incident-updated').innerText =
                        'Unable to load incidents: ' + error.message;
                });
        }
        function fetchData() {
            fetch('/api/status').then(r => r.json()).then(data => {
                for (const [key, remaining] of Object.entries(data.countdowns)) {
                    const [pid, host] = key.split('_');
                    const cd = document.getElementById('cd-' + pid);
                    const btn = document.getElementById('btn-' + pid);
                    if (cd && remaining > 0) {
                        cd.style.display = 'block';
                        cd.innerText = Math.ceil(remaining) + 's';
                        btn.classList.add('active');
                    }
                }
                for (const p of problems) {
                    const btn = document.getElementById('btn-' + p.id);
                    const cd = document.getElementById('cd-' + p.id);
                    if (!Object.keys(data.countdowns).some(k => k.startsWith(p.id))) {
                        if (cd) cd.style.display = 'none';
                        if (btn) btn.classList.remove('active');
                    }
                }

                const util = data.utilization;
                const utilVal = document.getElementById('util-val');
                utilVal.innerText = util.toFixed(1) + '%';
                utilVal.className = 'stat-value ' + (util > 80 ? 'red' : (util > 50 ? 'yellow' : 'green'));

                document.getElementById('active-probs').innerText = Object.keys(data.countdowns).length;

                const netHealth = document.getElementById('net-health');
                if (util > 80) {
                    netHealth.innerText = 'Congested';
                    netHealth.className = 'stat-value red';
                } else {
                    netHealth.innerText = 'Healthy';
                    netHealth.className = 'stat-value green';
                }

                const now = new Date().toLocaleTimeString();
                utilChart.data.labels.push(now);
                utilChart.data.datasets[0].data.push(util);
                utilChart.data.datasets[1].data.push(80);
                if (utilChart.data.labels.length > 30) {
                    utilChart.data.labels.shift();
                    utilChart.data.datasets[0].data.shift();
                    utilChart.data.datasets[1].data.shift();
                }
                utilChart.update();

                let t = '<tr><th>Host</th><th>VM CPU % (shared)</th><th>VM MEM % (shared)</th><th>Host Loss %</th><th>Status</th></tr>';
                for (const [host, info] of Object.entries(data.telemetry)) {
                    let color = 'var(--green)';
                    if (info.status === 'WARN') color = 'var(--yellow)';
                    if (info.status === 'DOWN') color = 'var(--red)';
                    t += `<tr><td>${host}</td><td>${(info.cpu||0).toFixed(1)}</td><td>${(info.mem||0).toFixed(1)}</td><td style="color:${info.loss>0.05?'var(--red)':'inherit'}">${((info.loss||0)*100).toFixed(1)}</td><td><span style="color:${color}">● ${info.status}</span></td></tr>`;
                }
                document.getElementById('telemetry-table').innerHTML = t;

                const mitState = document.getElementById('mitigation-state');
                if (data.mitigation_active) {
                    mitState.innerText = 'Active';
                    mitState.className = 'stat-value red';
                } else {
                    mitState.innerText = 'Idle';
                    mitState.className = 'stat-value green';
                }

                const a = document.getElementById('alerts');
                if (data.alerts.length === 0) a.innerHTML = 'No alerts yet.';
                else a.innerHTML = data.alerts.map(x => `<div class="alert-line ${x.includes('HIGH-UTIL')||x.includes('loss')?'high':''}">${x}</div>`).join('');
            });
        }
        setInterval(fetchData, 2000);
        setInterval(fetchIncidents, 5000);
        fetchData();
        fetchIncidents();
    </script>
</body>
</html>
"""

# Duration each experiment runs for (seconds) — matches the allow-list in
# monitoring_topo.py (1–60).  Kill uses this as the agent-restart delay.
_EXPERIMENT_DURATION = 20

# Map dashboard problem IDs to the validated command names the control
# socket understands. Also maps semantic alias 'congestion' -> 'flood'.
_PROB_TO_CMD = {
    'flood':      'flood',
    'congestion': 'flood',
    'cpu':        'cpu',
    'mem':        'mem',
    'kill':       'kill',
    'burst':      'burst',
}


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/trigger', methods=['POST'])
def trigger():
    """Inject an experiment into the ACTUAL running Mininet topology via the
    Unix domain control socket opened by monitoring_topo.py.

    No second Mininet instance is created.  Every parameter is validated
    inside the topology's socket handler before any command executes.
    Supports both legacy contract ('problem', 'host', 'rate') and
    frontend contract ('experiment', 'target', 'intensity').
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        # Missing, malformed, or non-object JSON: answer with JSON, not Flask's HTML 400 page.
        return jsonify({
            'status': 'error',
            'message': 'Request body must be a JSON object, e.g. {"problem": "cpu", "host": "h1"}'
        }), 400
    prob_input = data.get('problem') if 'problem' in data else data.get('experiment', '')
    prob = str(prob_input or '').strip().lower()

    host_input = data.get('host') if 'host' in data else data.get('target', 'h1')
    host = str(host_input or 'h1').strip()

    rate_input = data.get('rate') if 'rate' in data else data.get('intensity', '8')
    rate = str(rate_input if rate_input is not None else '8').strip()

    # Dashboard-level guard: reject clearly invalid input before touching the socket.
    if prob not in _PROB_TO_CMD:
        return jsonify({
            'status': 'error',
            'message': f"Unknown problem/experiment: {prob_input!r}. Allowed: flood (or congestion), burst, cpu, mem, kill"
        }), 400

    if host not in ('h1', 'h2', 'h3', 'h4'):
        return jsonify({
            'status': 'error',
            'message': f"Invalid host: {host_input!r}. Allowed: h1, h2, h3, h4"
        }), 400

    cmd = _PROB_TO_CMD[prob]

    # Validate rate where applicable
    if cmd in ('flood', 'burst'):
        if rate not in ('5', '8', '10'):
            return jsonify({
                'status': 'error',
                'message': f"Invalid rate/intensity: {rate_input!r}. Allowed rates for {prob}: 5, 8, 10"
            }), 400
    else:
        # Default/normalize rate for cpu/mem/kill so downstream socket call succeeds
        if rate not in ('5', '8', '10'):
            rate = '8'

    # Fixed duration: burst runs 5s; flood/cpu/mem/kill run _EXPERIMENT_DURATION (20s)
    duration = 6 if cmd == 'burst' else _EXPERIMENT_DURATION

    # Register countdown so the UI shows the active-problem indicator
    # Use canonical cmd and alias prob so both UIs match active-problem indicators
    key = f'{cmd}_{host}'
    with lock:
        active_problems[key] = time.time() + duration
        if prob != cmd:
            active_problems[f'{prob}_{host}'] = time.time() + duration

    # Send the command to the live topology over the control socket
    result = _send_control_command(cmd, host, rate=rate, duration=duration)

    if result.get('status') == 'error':
        # Remove the optimistic countdown entry if the command failed
        with lock:
            active_problems.pop(key, None)
            if prob != cmd:
                active_problems.pop(f'{prob}_{host}', None)
        msg = result.get('message', 'unknown error')
        # If rejected due to an already active experiment, return 409 Conflict
        status_code = 409 if 'already running' in msg.lower() else 503
        return jsonify({'status': 'error', 'message': msg}), status_code

    return jsonify({'status': 'triggered', 'message': result.get('message', '')})


@app.route('/api/experiments/status')
def experiments_status():
    """Return active experiment state, verification progress, and bounded recovery history.

    Source of truth is the live topology process (IPC 'status'). The exported
    state file is only a fallback and is annotated so the UI can show the data
    as unverified instead of presenting a possibly frozen history as current.
    """
    ipc_res = _send_control_command('status', 'h1')
    if ipc_res.get('status') == 'ok' and isinstance(ipc_res.get('experiments'), dict):
        payload = dict(ipc_res['experiments'])
        payload.setdefault('active', None)
        payload.setdefault('history', [])
        payload['source'] = 'live'
        payload['stale'] = False
        return jsonify(payload)

    state_file = os.path.join('/tmp' if sys.platform.startswith('linux') else tempfile.gettempdir(), 'sdn_experiments.json')
    try:
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                payload = json.load(f)
            if isinstance(payload, dict):
                payload.setdefault('active', None)
                payload.setdefault('history', [])
                payload['source'] = 'file'
                # Topology is unreachable, so this snapshot cannot be confirmed as current.
                payload['stale'] = True
                return jsonify(payload)
    except (OSError, json.JSONDecodeError):
        pass
    return jsonify({'active': None, 'history': [], 'source': 'unavailable', 'stale': True})

@app.route('/api/status')
def status():
    utilization = None
    utilization_stale = True
    mitigation_active = False
    try:
        with open('/tmp/sdn_metrics.json', 'r') as f:
            metrics = json.load(f)
            if 'utilization_timestamp' in metrics:
                utilization_timestamp = metrics.get('utilization_timestamp')
            else:
                utilization_timestamp = metrics.get('snapshot_timestamp', metrics.get('timestamp'))
            raw_utilization = metrics.get('utilization')
            metrics_age = time.time() - utilization_timestamp if isinstance(utilization_timestamp, (int, float)) else None
            utilization_stale = (
                not isinstance(raw_utilization, (int, float))
                or not math.isfinite(raw_utilization)
                or metrics_age is None
                or metrics_age < 0
                or metrics_age > 10.0
            )
            if not utilization_stale:
                utilization = raw_utilization * 100
            mitigation_active = metrics.get('mitigation_active', False)
    except (OSError, json.JSONDecodeError): pass

    telemetry = {}
    try:
        with open('/tmp/server_state.json', 'r') as f:
            raw_state = json.load(f)
            if isinstance(raw_state, dict):
                if 'telemetry' in raw_state and isinstance(raw_state['telemetry'], dict):
                    telemetry = raw_state['telemetry']
                else:
                    telemetry = {k: v for k, v in raw_state.items() if k != 'snapshot_timestamp' and isinstance(v, dict)}
    except (OSError, json.JSONDecodeError): pass

    alert_categories = {
        'CPU': [],
        'MEMORY': [],
        'PACKET LOSS': [],
        'TELEMETRY': [],
        'CONTROLLER': [],
        'OTHER': [],
    }
    alert_sources = [
        ('/tmp/monitor_alerts.log', 'SERVER'),
        ('/tmp/sdn_alerts.log', 'CONTROLLER'),
    ]

    for log_path, source in alert_sources:
        try:
            with open(log_path, 'r') as f:
                for line in f:
                    if 'ALERT' not in line.upper():
                        continue

                    if source == 'CONTROLLER':
                        category = 'CONTROLLER'
                    elif re.search(r'high CPU', line, re.I):
                        category = 'CPU'
                    elif re.search(r'high MEM|high memory', line, re.I):
                        category = 'MEMORY'
                    elif re.search(r'packet loss', line, re.I):
                        category = 'PACKET LOSS'
                    elif re.search(r'silent|telemetry', line, re.I):
                        category = 'TELEMETRY'
                    else:
                        category = 'OTHER'

                    alert_categories[category].append(
                        f'[{source}] {line.strip()}'
                    )
        except (OSError, UnicodeDecodeError):
            pass

    alerts = []
    for category_alerts in alert_categories.values():
        alerts.extend(category_alerts[-10:])

    countdowns = {}
    with lock:
        now = time.time()
        for k in list(active_problems.keys()):
            rem = active_problems[k] - now
            if rem > 0:
                countdowns[k] = rem
            else:
                del active_problems[k]

    telemetry_stale = not telemetry or not any(
        isinstance(host.get('age'), (int, float)) and 0 <= host['age'] <= 5.0
        for host in telemetry.values()
    )
    return jsonify({
        'utilization': utilization,
        'utilization_stale': utilization_stale,
        'telemetry_stale': telemetry_stale,
        'data_stale': utilization_stale or telemetry_stale,
        'telemetry': telemetry,
        'alerts': alerts,
        'countdowns': countdowns,
        'mitigation_active': mitigation_active,
    })


# ---------------------------------------------------------------------------
# Priority 2: Configured Mininet Topology & Live Link Telemetry Endpoints
# ---------------------------------------------------------------------------

CONFIGURED_TOPOLOGY = {
    'type': 'configured_mininet',
    'description': 'Configured Mininet static topology from topo/monitoring_topo.py',
    'switches': [
        {'id': 's1', 'label': 's1', 'dpid': '0000000000000001', 'ports': [1, 2, 3, 4, 5]},
        {'id': 's2', 'label': 's2', 'dpid': '0000000000000002', 'ports': [1, 2, 3]},
    ],
    'hosts': [
        {'id': 'h1', 'label': 'h1', 'ip': '10.0.0.1', 'mac': '00:00:00:00:00:01', 'connected_to': 's1', 'switch_port': 1, 'role': 'Host'},
        {'id': 'h2', 'label': 'h2', 'ip': '10.0.0.2', 'mac': '00:00:00:00:00:02', 'connected_to': 's1', 'switch_port': 2, 'role': 'Host'},
        {'id': 'h3', 'label': 'h3', 'ip': '10.0.0.3', 'mac': '00:00:00:00:00:03', 'connected_to': 's1', 'switch_port': 3, 'role': 'Host'},
        {'id': 'h4', 'label': 'h4', 'ip': '10.0.0.4', 'mac': '00:00:00:00:00:04', 'connected_to': 's1', 'switch_port': 4, 'role': 'Host'},
        {'id': 'h5', 'label': 'h5', 'ip': '10.0.0.5', 'mac': '00:00:00:00:00:05', 'connected_to': 's2', 'switch_port': 2, 'role': 'Monitoring Server'},
        {'id': 'h6', 'label': 'h6', 'ip': '10.0.0.6', 'mac': '00:00:00:00:00:06', 'connected_to': 's2', 'switch_port': 3, 'role': 'Host'},
    ],
    'links': [
        {'id': 'h1-s1', 'source': 'h1', 'target': 's1', 'source_port': 'h1-eth0', 'target_port': 1, 'capacity_mbps': 100, 'delay': '1ms', 'is_bottleneck': False},
        {'id': 'h2-s1', 'source': 'h2', 'target': 's1', 'source_port': 'h2-eth0', 'target_port': 2, 'capacity_mbps': 100, 'delay': '1ms', 'is_bottleneck': False},
        {'id': 'h3-s1', 'source': 'h3', 'target': 's1', 'source_port': 'h3-eth0', 'target_port': 3, 'capacity_mbps': 100, 'delay': '1ms', 'is_bottleneck': False},
        {'id': 'h4-s1', 'source': 'h4', 'target': 's1', 'source_port': 'h4-eth0', 'target_port': 4, 'capacity_mbps': 100, 'delay': '1ms', 'is_bottleneck': False},
        {'id': 's1-s2', 'source': 's1', 'target': 's2', 'source_port': 5, 'target_port': 1, 'capacity_mbps': 5, 'delay': '5ms', 'is_bottleneck': True},
        {'id': 's2-h5', 'source': 's2', 'target': 'h5', 'source_port': 2, 'target_port': 'h5-eth0', 'capacity_mbps': 100, 'delay': '1ms', 'is_bottleneck': False},
        {'id': 's2-h6', 'source': 's2', 'target': 'h6', 'source_port': 3, 'target_port': 'h6-eth0', 'capacity_mbps': 100, 'delay': '1ms', 'is_bottleneck': False},
    ]
}

# In-memory sliding window for port rate calculations: { port_key: (timestamp, tx_bytes) }
_prev_port_samples = {}
_port_samples_lock = threading.Lock()

# In-memory sliding window for flow rate calculations: { flow_key: (timestamp, byte_count) }
_prev_flow_samples = {}
_flow_samples_lock = threading.Lock()

# Mapping each link to its primary monitoring switch port key in /tmp/sdn_portstats.json
_LINK_PORT_KEYS = {
    'h1-s1': '1:1',
    'h2-s1': '1:2',
    'h3-s1': '1:3',
    'h4-s1': '1:4',
    's1-s2': '1:5',  # Bottleneck link (s1-eth5 -> s2-eth1)
    's2-h5': '2:2',  # s2 port 2 -> h5 server
    's2-h6': '2:3',  # s2 port 3 -> h6
}

@app.route('/api/topology')
def topology():
    """Return configured Mininet topology."""
    return jsonify(CONFIGURED_TOPOLOGY)

@app.route('/api/network/links')
def network_links():
    """Return link-level traffic, throughput, and utilization based on real
    Ryu OpenFlow port statistics and verified controller metrics.

    Never invents throughput; returns null for unmeasured or single-sample metrics.
    """
    now = time.time()
    raw_stats = {}
    stats_ts = None
    age_seconds = None
    stale = True

    # 1. Read /tmp/sdn_portstats.json
    try:
        with open('/tmp/sdn_portstats.json', 'r') as f:
            data = json.load(f)
            stats_ts = data.get('timestamp')
            raw_stats = data.get('port_stats', {})
            if isinstance(stats_ts, (int, float)):
                age_seconds = max(0.0, round(now - stats_ts, 2))
                stale = age_seconds > 10.0
    except (OSError, json.JSONDecodeError):
        pass

    # 2. Read /tmp/sdn_metrics.json (verified bottleneck utilization from Ryu)
    m = {}
    controller_util = None
    controller_util_age = None
    controller_util_stale = True
    try:
        with open('/tmp/sdn_metrics.json', 'r') as f:
            m = json.load(f)
            controller_util = m.get('utilization')
            if 'utilization_timestamp' in m:
                controller_util_ts = m.get('utilization_timestamp')
            else:
                controller_util_ts = m.get('snapshot_timestamp', m.get('timestamp'))
            if isinstance(controller_util_ts, (int, float)):
                controller_util_age = max(0.0, round(now - controller_util_ts, 2))
                controller_util_stale = (
                    now < controller_util_ts
                    or controller_util_age > 10.0
                    or not isinstance(controller_util, (int, float))
                    or not math.isfinite(controller_util)
                )
    except (OSError, json.JSONDecodeError):
        pass

    links_result = {}

    with _port_samples_lock:
        for link_def in CONFIGURED_TOPOLOGY['links']:
            lid = link_def['id']
            capacity_mbps = link_def['capacity_mbps']
            is_bottleneck = link_def['is_bottleneck']
            port_key = _LINK_PORT_KEYS.get(lid)

            tx_bytes = raw_stats.get(port_key) if raw_stats else None
            # Validate tx_bytes is a non-negative integer
            if not isinstance(tx_bytes, int) or tx_bytes < 0:
                tx_bytes = None

            rate_mbps = None
            utilization_pct = None
            measured = False
            status = 'up'

            # A. Prefer a fresh controller measurement for the bottleneck.
            # Do not fall back to a port delta when its verified sample is stale.
            if is_bottleneck and not controller_util_stale:
                utilization_pct = round(controller_util * 100.0, 2)
                rate_mbps = round((controller_util * (capacity_mbps * 1e6)) / 1e6, 3)
                measured = True
                status = 'congested' if utilization_pct >= 80.0 else 'up'
            elif is_bottleneck:
                status = 'unknown'
            elif tx_bytes is not None and stats_ts is not None:
                # B. Delta calculation for edge links when two valid samples exist
                if port_key in _prev_port_samples:
                    prev_ts, prev_tx = _prev_port_samples[port_key]
                    dt = stats_ts - prev_ts
                    if dt > 0.5 and tx_bytes >= prev_tx:
                        rate_bps = (tx_bytes - prev_tx) * 8.0 / dt
                        rate_mbps = round(rate_bps / 1e6, 3)
                        utilization_pct = round((rate_bps / (capacity_mbps * 1e6)) * 100.0, 2)
                        measured = True
                        status = 'congested' if utilization_pct >= 80.0 else 'up'
                    elif tx_bytes < prev_tx:
                        # Counter reset occurred (interface reboot / Mininet reset)
                        rate_mbps = None
                        utilization_pct = None
                        measured = False
                else:
                    # First sample: no delta measurable yet
                    rate_mbps = None
                    utilization_pct = None
                    measured = False

                # Update sample history
                _prev_port_samples[port_key] = (stats_ts, tx_bytes)

            # If stats file is missing, flag link status as unknown
            if stats_ts is None:
                status = 'unknown'

            links_result[lid] = {
                'id': lid,
                'source': link_def['source'],
                'destination': link_def['target'],
                'source_port': link_def['source_port'],
                'destination_port': link_def['target_port'],
                'capacity_mbps': capacity_mbps,
                'is_bottleneck': is_bottleneck,
                'delay': link_def['delay'],
                'tx_bytes': tx_bytes,
                'rx_bytes': None,
                'throughput_mbps': rate_mbps,
                'utilization': utilization_pct,
                'timestamp': stats_ts,
                'age_seconds': age_seconds,
                'measured': measured,
                'stale': stale or (is_bottleneck and controller_util_stale),
                'status': status,
                'measurement_source': 'Ryu OpenFlow PortStats' if measured else ('Awaiting PortStats delta' if tx_bytes is not None else 'Unmeasured')
            }

    return jsonify({
        'timestamp': stats_ts or now,
        'age_seconds': age_seconds,
        'stale': stale,
        'links': links_result,
    })

@app.route('/api/network/flows')
def network_flows():
    """Return active OpenFlow flow entries, per-flow throughput, and top talkers.

    Reads /tmp/sdn_flows.json produced by Ryu OFPFlowStatsReply.
    Calculates per-flow throughput from consecutive samples (delta_bytes * 8 / dt).
    Separates forwarded vs dropped flows, avoids double counting across switches,
    and isolates bottleneck attribution.
    """
    now = time.time()
    flows_raw = []
    stats_ts = None
    age_seconds = None
    stale = True

    # 1. Read /tmp/sdn_flows.json
    try:
        with open('/tmp/sdn_flows.json', 'r') as f:
            data = json.load(f)
            stats_ts = data.get('timestamp')
            flows_raw = data.get('flows', [])
            if isinstance(stats_ts, (int, float)):
                age_seconds = max(0.0, round(now - stats_ts, 2))
                stale = age_seconds > 10.0
    except (OSError, json.JSONDecodeError):
        pass

    # 2. Read /tmp/sdn_metrics.json for bottleneck throughput attribution
    m = {}
    controller_util = None
    try:
        with open('/tmp/sdn_metrics.json', 'r') as f:
            m = json.load(f)
            controller_util = m.get('utilization')
    except (OSError, json.JSONDecodeError):
        pass

    bottleneck_mbps = None
    if controller_util is not None and isinstance(controller_util, (int, float)):
        util_ratio = controller_util / 100.0 if controller_util > 1.0 else controller_util
        bottleneck_mbps = round(util_ratio * 5.0, 3)
    if 'bottleneck_throughput_mbps' in m and isinstance(m['bottleneck_throughput_mbps'], (int, float)):
        bottleneck_mbps = float(m['bottleneck_throughput_mbps'])

    processed_flows = []
    active_keys = set()

    with _flow_samples_lock:
        for flow in flows_raw:
            dpid = flow.get('dpid', 1)
            switch_name = flow.get('switch', f"s{dpid}")
            table_id = flow.get('table_id', 0)
            priority = flow.get('priority', 0)
            cookie = flow.get('cookie', 0)
            match_dict = flow.get('match', {})
            actions = flow.get('actions', [])
            duration_sec = flow.get('duration_sec', 0)
            idle_timeout = flow.get('idle_timeout', 0)
            hard_timeout = flow.get('hard_timeout', 0)
            packet_count = flow.get('packet_count', 0)
            byte_count = flow.get('byte_count', 0)
            is_mitigation = flow.get('is_mitigation', False)
            is_table_miss = flow.get('is_table_miss', False)

            # Unique stable flow key
            match_sig = ",".join(f"{k}={match_dict[k]}" for k in sorted(match_dict.keys()))
            flow_key = flow.get('id') or f"{dpid}:{table_id}:{priority}:{cookie}:{match_sig}"
            active_keys.add(flow_key)

            rate_mbps = None
            dropped_mbps = None
            measured = False

            if stats_ts is not None and byte_count is not None:
                if flow_key in _prev_flow_samples:
                    prev_ts, prev_bytes = _prev_flow_samples[flow_key]
                    dt = stats_ts - prev_ts
                    if dt > 0.5 and byte_count >= prev_bytes:
                        computed_rate = round(((byte_count - prev_bytes) * 8.0 / dt) / 1e6, 3)
                        measured = True
                        if is_mitigation or "DROP" in actions:
                            dropped_mbps = computed_rate
                            rate_mbps = None
                        else:
                            rate_mbps = computed_rate
                            dropped_mbps = None
                    elif byte_count < prev_bytes:
                        measured = False
                        rate_mbps = None
                        dropped_mbps = None
                else:
                    measured = False
                    rate_mbps = None
                    dropped_mbps = None

                _prev_flow_samples[flow_key] = (stats_ts, byte_count)

            traverses_bottleneck = (dpid == 1 and any('OUTPUT:5' in a for a in actions)) or \
                                   (dpid == 2 and any('OUTPUT:1' in a for a in actions))
            share_of_bottleneck_pct = None
            if traverses_bottleneck and measured and rate_mbps is not None and bottleneck_mbps and bottleneck_mbps > 0.05:
                share_of_bottleneck_pct = round(min(100.0, (rate_mbps / bottleneck_mbps) * 100.0), 1)

            processed_flows.append({
                'id': flow_key,
                'dpid': dpid,
                'switch': switch_name,
                'table_id': table_id,
                'priority': priority,
                'cookie': cookie,
                'match': match_dict,
                'actions': actions,
                'duration_sec': duration_sec,
                'idle_timeout': idle_timeout,
                'hard_timeout': hard_timeout,
                'packet_count': packet_count,
                'byte_count': byte_count,
                'throughput_mbps': rate_mbps,
                'dropped_mbps': dropped_mbps,
                'measured': measured,
                'is_mitigation': is_mitigation,
                'is_table_miss': is_table_miss,
                'traverses_bottleneck': traverses_bottleneck,
                'share_of_bottleneck_pct': share_of_bottleneck_pct,
            })

        # Evict inactive flows from cache (older than 60s)
        stale_keys = [k for k in _prev_flow_samples if k not in active_keys]
        for k in stale_keys:
            if now - _prev_flow_samples[k][0] > 60.0:
                del _prev_flow_samples[k]

    # Top Talkers: Filter forwarding flows at ingress switches to prevent double counting
    candidate_flows = []
    for f in processed_flows:
        if f['is_mitigation'] or f['is_table_miss'] or 'DROP' in f['actions']:
            continue

        in_p = str(f['match'].get('in_port', ''))
        dpid = f['dpid']
        # Double counting filter: skip downstream intermediate hops
        if dpid == 1 and in_p == '5':
            continue
        if dpid == 2 and in_p == '1':
            continue

        candidate_flows.append(f)

    # Sort candidates by throughput (primary) then byte_count (secondary)
    candidate_flows.sort(key=lambda x: (x['throughput_mbps'] or 0.0, x['byte_count']), reverse=True)

    top_talkers = []
    rank = 1
    for f in candidate_flows:
        src = f['match'].get('ipv4_src', f['match'].get('eth_src', f"Port {f['match'].get('in_port', '?')}"))
        dst = f['match'].get('ipv4_dst', f['match'].get('eth_dst', 'Broad/Multicast'))
        proto_num = f['match'].get('ip_proto')
        proto = 'UDP' if proto_num == '17' else ('TCP' if proto_num == '6' else ('ICMP' if proto_num == '1' else ('IP' if 'eth_type' in f['match'] else 'Ethernet')))

        traverses_bottleneck = (f['dpid'] == 1 and any('OUTPUT:5' in a for a in f['actions'])) or \
                               (f['dpid'] == 2 and any('OUTPUT:1' in a for a in f['actions']))

        share_of_bottleneck_pct = None
        if traverses_bottleneck and f['measured'] and f['throughput_mbps'] is not None and bottleneck_mbps and bottleneck_mbps > 0.05:
            share_of_bottleneck_pct = round(min(100.0, (f['throughput_mbps'] / bottleneck_mbps) * 100.0), 1)

        top_talkers.append({
            'rank': rank,
            'flow_id': f['id'],
            'switch': f['switch'],
            'ingress_switch': f['switch'],
            'source': src,
            'destination': dst,
            'protocol': proto,
            'throughput_mbps': f['throughput_mbps'],
            'byte_count': f['byte_count'],
            'packet_count': f['packet_count'],
            'measured': f['measured'],
            'traverses_bottleneck': traverses_bottleneck,
            'share_of_bottleneck_pct': share_of_bottleneck_pct,
        })
        rank += 1
        if rank > 10:
            break

    mitigation_rules = [f for f in processed_flows if f['is_mitigation'] or 'DROP' in f['actions']]

    return jsonify({
        'timestamp': stats_ts or now,
        'age_seconds': age_seconds,
        'stale': stale,
        'flows_count': len(processed_flows),
        'flows': processed_flows,
        'top_talkers': top_talkers,
        'mitigation_rules': mitigation_rules,
    })

# ---------------------------------------------------------------------------
# Priority 4: Network Intelligence, Analytics & Persistence Engine
# ---------------------------------------------------------------------------

_db = None
_db_lock = threading.Lock()

def get_db():
    """Retrieve or lazily initialize the NetwatchDB instance."""
    global _db
    custom_path = app.config.get('DATABASE_PATH')
    with _db_lock:
        if _db is None or (_db.db_path != custom_path and custom_path is not None):
            _db = NetwatchDB(db_path=custom_path)
        return _db

# In-memory tracking for deduplication across polling cycles
_last_persisted_ts = {
    'portstats': 0.0,
    'flows': 0.0,
    'hosts': {},
}
_last_maintenance_ts = 0.0
_collector_lock = threading.Lock()

# 60-second EMA tracking for traffic burst anomaly detection
_ema_state = {}
_ema_lock = threading.Lock()

def _update_ema(key, current_rate, sample_ts):
    """Calculate 60-second EMA and detect traffic bursts.
    Formula: alpha = 1 - exp(-dt / 60.0).
    Missing/stale samples hold EMA constant and do not evaluate bursts or recoveries.
    Returns (ema, is_burst, is_recovered).
    """
    with _ema_lock:
        if current_rate is None or not isinstance(current_rate, (int, float)):
            return None, False, False

        if key not in _ema_state:
            _ema_state[key] = {
                'ema': float(current_rate),
                'last_ts': sample_ts,
                'in_burst': False,
                'burst_peak': float(current_rate),
            }
            return float(current_rate), False, False

        state = _ema_state[key]
        if sample_ts <= state['last_ts']:
            # Duplicate or out-of-order timestamp: hold state without transitions
            return state['ema'], False, False

        dt = sample_ts - state['last_ts']
        if dt > 10.0:
            # Stale interval (>10s gap): hold EMA constant without synthetic updates or state changes
            state['last_ts'] = sample_ts
            return state['ema'], False, False

        alpha = 1.0 - math.exp(-dt / 60.0)
        prev_ema = state['ema']
        new_ema = alpha * float(current_rate) + (1.0 - alpha) * prev_ema
        state['ema'] = new_ema
        state['last_ts'] = sample_ts

        was_in_burst = state.get('in_burst', False)
        is_burst = False
        is_recovered = False

        # Burst trigger: rate >= 3x baseline AND jump >= 2.0 Mbps
        if current_rate >= 3.0 * prev_ema and (current_rate - prev_ema) >= 2.0:
            is_burst = True
            state['in_burst'] = True
            state['burst_peak'] = max(state.get('burst_peak', current_rate), current_rate)
        elif was_in_burst:
            if current_rate <= 1.5 * new_ema:
                # Confirmed recovery: fresh, measured sample <= 1.5x EMA
                state['in_burst'] = False
                state['burst_peak'] = 0.0
                is_recovered = True
            else:
                # Burst ongoing: rate remains above 1.5x EMA
                is_burst = True
                state['burst_peak'] = max(state.get('burst_peak', current_rate), current_rate)

        return new_ema, is_burst, is_recovered

def collect_historical_snapshot(current_time=None):
    """Poll runtime snapshots from /tmp and persist deduplicated records to NetwatchDB.
    Preserves measurement timestamps, updates incident state machines, and tracks anomalies.
    """
    global _last_maintenance_ts
    now = current_time if current_time is not None else time.time()
    db = get_db()
    results = {'links_inserted': 0, 'hosts_inserted': 0, 'flows_inserted': 0}

    with _collector_lock:
        # 1. Process port statistics (/tmp/sdn_portstats.json) & Link Metrics
        portstats_data = {}
        portstats_ts = None
        try:
            with open('/tmp/sdn_portstats.json', 'r') as f:
                d = json.load(f)
                portstats_ts = d.get('timestamp')
                portstats_data = d.get('port_stats', {})
        except (OSError, json.JSONDecodeError):
            pass

        controller_metrics = {}
        try:
            with open('/tmp/sdn_metrics.json', 'r') as f:
                controller_metrics = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

        # Check for confirmed mitigation (Ryu flag + Priority 100 drop rule on s1)
        mitigation_active_flag = bool(controller_metrics.get('mitigation_active', False))
        drop_rule_present = False
        try:
            with open('/tmp/sdn_flows.json', 'r') as f:
                f_data = json.load(f)
                for fl in f_data.get('flows', []):
                    if fl.get('dpid') == 1 and fl.get('priority') == 100 and ('DROP' in fl.get('actions', []) or fl.get('is_mitigation')):
                        drop_rule_present = True
                        break
        except (OSError, json.JSONDecodeError):
            pass

        mitigation_confirmed = mitigation_active_flag and drop_rule_present

        # Deduplicate port statistics by source timestamp
        if portstats_ts and isinstance(portstats_ts, (int, float)) and portstats_ts > _last_persisted_ts['portstats']:
            for link_def in CONFIGURED_TOPOLOGY['links']:
                link_id = link_def['id']
                port_key = _LINK_PORT_KEYS.get(link_id)
                curr_tx = portstats_data.get(port_key) if port_key else None
                capacity_mbps = float(link_def['capacity_mbps'])

                rate_mbps = None
                util_pct = None
                measured = False

                with _port_samples_lock:
                    if curr_tx is not None:
                        if port_key in _prev_port_samples:
                            prev_t, prev_tx = _prev_port_samples[port_key]
                            dt = portstats_ts - prev_t
                            if dt > 0.5 and curr_tx >= prev_tx:
                                rate_mbps = round(((curr_tx - prev_tx) * 8.0 / dt) / 1e6, 3)
                                util_pct = round((rate_mbps / capacity_mbps) * 100.0, 1)
                                measured = True
                        _prev_port_samples[port_key] = (portstats_ts, curr_tx)

                # Use verified controller metrics for bottleneck link
                if link_def['is_bottleneck'] and 'utilization' in controller_metrics:
                    c_util = controller_metrics['utilization']
                    if isinstance(c_util, (int, float)):
                        util_ratio = c_util / 100.0 if c_util > 1.0 else c_util
                        util_pct = round(util_ratio * 100.0, 1)
                        rate_mbps = round(util_ratio * 5.0, 3)
                        measured = True

                status = 'congested' if (util_pct or 0.0) >= 80.0 else 'normal'
                if not measured:
                    status = 'unknown'

                if db.insert_link_sample(portstats_ts, link_id, rate_mbps, util_pct, curr_tx, measured, status):
                    results['links_inserted'] += 1

                # Incident State Machine for Bottleneck Congestion
                if link_def['is_bottleneck'] and measured and util_pct is not None:
                    if util_pct >= 80.0:
                        db.record_or_update_incident(
                            event_type='CONGESTION',
                            target='s1-s2',
                            severity='CRITICAL',
                            timestamp=portstats_ts,
                            current_value=util_pct,
                            trigger_threshold=80.0,
                            description=f'Bottleneck link s1-s2 utilization at {util_pct:.1f}% (threshold: 80.0%)',
                            mitigation_confirmed=mitigation_confirmed
                        )
                    elif util_pct < 75.0 and not mitigation_active_flag and not drop_rule_present:
                        # Fully recovered: util < 75% AND mitigation drop rule cleared
                        db.resolve_incident('CONGESTION', 's1-s2', portstats_ts, 'Utilization < 75% and mitigation cleared')

                    # Traffic burst evaluation on bottleneck
                    _, is_burst, is_recovered = _update_ema('s1-s2', rate_mbps, portstats_ts)
                    if is_burst:
                        db.record_or_update_incident(
                            event_type='TRAFFIC_BURST',
                            target='s1-s2',
                            severity='WARNING',
                            timestamp=portstats_ts,
                            current_value=rate_mbps or 0.0,
                            trigger_threshold=3.0,
                            description=f'Traffic burst detected on s1-s2: rate {rate_mbps:.2f} Mbps exceeds 3x 60s EMA'
                        )
                    elif is_recovered:
                        db.resolve_incident(
                            'TRAFFIC_BURST',
                            's1-s2',
                            portstats_ts,
                            'Traffic burst resolved: rate normalized below 1.5x EMA'
                        )

            _last_persisted_ts['portstats'] = portstats_ts

        # 2. Process Host Telemetry (/tmp/server_state.json)
        server_state = {}
        try:
            with open('/tmp/server_state.json', 'r') as f:
                server_state = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass

        max_vm_cpu = 0.0
        latest_host_ts = 0.0

        # Support both nested 'telemetry' format and flat h1/h2/h3/h4 production format
        if 'telemetry' in server_state and isinstance(server_state['telemetry'], dict):
            host_items = list(server_state['telemetry'].items())
        else:
            host_items = [(k, v) for k, v in server_state.items() if k != 'snapshot_timestamp' and isinstance(v, dict)]

        for host_id, h_metrics in host_items:
            h_ts = h_metrics.get('last_seen')
            if h_ts and isinstance(h_ts, (int, float)):
                if h_ts > _last_persisted_ts['hosts'].get(host_id, 0.0):
                    cpu = h_metrics.get('cpu')
                    mem = h_metrics.get('mem')
                    loss = h_metrics.get('loss')
                    st = h_metrics.get('status', 'OK')
                    if db.insert_host_sample(h_ts, host_id, cpu, mem, loss, st):
                        results['hosts_inserted'] += 1
                    _last_persisted_ts['hosts'][host_id] = h_ts
                    if cpu is not None and isinstance(cpu, (int, float)):
                        max_vm_cpu = max(max_vm_cpu, cpu)
                    latest_host_ts = max(latest_host_ts, h_ts)

        # Host VM-wide CPU saturation state machine
        if latest_host_ts > 0.0:
            if max_vm_cpu >= 85.0:
                db.record_or_update_incident(
                    event_type='VM_CPU_SATURATION',
                    target='mininet-vm',
                    severity='WARNING',
                    timestamp=latest_host_ts,
                    current_value=max_vm_cpu,
                    trigger_threshold=85.0,
                    description=f'Shared Mininet host VM CPU saturated at {max_vm_cpu:.1f}%'
                )
            elif max_vm_cpu < 80.0:
                db.resolve_incident('VM_CPU_SATURATION', 'mininet-vm', latest_host_ts, 'VM CPU normalized below 80%')

        # Detect telemetry silence and recovery
        known_hosts = ['h1', 'h2', 'h3', 'h4']
        host_map = dict(host_items)

        for host_id in known_hosts:
            metrics = host_map.get(host_id, {})
            last_seen = metrics.get('last_seen', 0)

            if not isinstance(last_seen, (int, float)) or last_seen <= 0:
                continue

            silence_duration = now - last_seen

            if silence_duration > 5.0:
                db.record_or_update_incident(
                    event_type='HOST_SILENT',
                    target=host_id,
                    severity='WARNING',
                    timestamp=now,
                    current_value=silence_duration,
                    trigger_threshold=5.0,
                    description=f'{host_id} telemetry missing for {silence_duration:.1f} seconds'
                )
            else:
                db.resolve_incident(
                    'HOST_SILENT',
                    host_id,
                    now,
                    'Telemetry resumed'
                )

        # 3. Process Flow Statistics (/tmp/sdn_flows.json)
        flows_data = {}
        flows_ts = None
        try:
            with open('/tmp/sdn_flows.json', 'r') as f:
                flows_data = json.load(f)
                flows_ts = flows_data.get('timestamp')
        except (OSError, json.JSONDecodeError):
            pass

        if flows_ts and isinstance(flows_ts, (int, float)) and flows_ts > _last_persisted_ts['flows']:
            for fl in flows_data.get('flows', []):
                dpid = fl.get('dpid', 1)
                switch_name = fl.get('switch', f"s{dpid}")
                table_id = fl.get('table_id', 0)
                priority = fl.get('priority', 0)
                cookie = fl.get('cookie', 0)
                match_dict = fl.get('match', {})
                actions = fl.get('actions', [])
                byte_count = fl.get('byte_count', 0)
                is_mitigation = fl.get('is_mitigation', False)

                match_sig = ",".join(f"{k}={match_dict[k]}" for k in sorted(match_dict.keys()))
                flow_id = fl.get('id') or f"s{dpid}:{table_id}:{priority}:{cookie}:{match_sig}"

                rate_mbps = None
                dropped_mbps = None
                measured = False

                with _flow_samples_lock:
                    if byte_count is not None:
                        if flow_id in _prev_flow_samples:
                            p_ts, p_bytes = _prev_flow_samples[flow_id]
                            dt = flows_ts - p_ts
                            if dt > 0.5 and byte_count >= p_bytes:
                                comp_rate = round(((byte_count - p_bytes) * 8.0 / dt) / 1e6, 3)
                                measured = True
                                if is_mitigation or "DROP" in actions:
                                    dropped_mbps = comp_rate
                                else:
                                    rate_mbps = comp_rate
                        _prev_flow_samples[flow_id] = (flows_ts, byte_count)

                traverses_bottleneck = (dpid == 1 and any('OUTPUT:5' in a for a in actions)) or \
                                       (dpid == 2 and any('OUTPUT:1' in a for a in actions))
                share_pct = None
                bottleneck_rate = 5.0 * (controller_metrics.get('utilization', 0.0) / 100.0)
                if traverses_bottleneck and measured and rate_mbps and bottleneck_rate > 0.05:
                    share_pct = round(min(100.0, (rate_mbps / bottleneck_rate) * 100.0), 1)

                src = match_dict.get('ipv4_src', match_dict.get('eth_src', f"Port {match_dict.get('in_port', '?')}"))
                dst = match_dict.get('ipv4_dst', match_dict.get('eth_dst', 'Any'))
                proto_num = match_dict.get('ip_proto')
                proto = 'UDP' if proto_num == '17' else ('TCP' if proto_num == '6' else ('ICMP' if proto_num == '1' else 'Ethernet'))

                if db.insert_flow_sample(
                    flows_ts, dpid, flow_id, switch_name, src, dst, proto,
                    rate_mbps, dropped_mbps, traverses_bottleneck, share_pct,
                    is_mitigation, measured
                ):
                    results['flows_inserted'] += 1

            _last_persisted_ts['flows'] = flows_ts

        # 4. Optional: Process Completed Flow Removals (/tmp/sdn_flow_removals.json)
        try:
            if os.path.exists('/tmp/sdn_flow_removals.json'):
                with open('/tmp/sdn_flow_removals.json', 'r') as f:
                    removals = json.load(f)
                if isinstance(removals, list):
                    for rem in removals:
                        db.insert_completed_flow(
                            rem.get('timestamp', now),
                            rem.get('dpid', 1),
                            rem.get('flow_id', 'unknown'),
                            rem.get('duration_sec', 0.0),
                            rem.get('packet_count', 0),
                            rem.get('byte_count', 0),
                            rem.get('avg_throughput_mbps'),
                            rem.get('reason', 'EXPIRED')
                        )
                with open('/tmp/sdn_flow_removals.json', 'w') as f:
                    f.write('[]')
        except (OSError, json.JSONDecodeError):
            pass

        # 5. Periodic Maintenance: Hourly rollups and retention pruning (every 10 minutes)
        if now - _last_maintenance_ts > 600.0:
            try:
                db.compute_hourly_rollups(closed_before_epoch=now)
                db.prune_expired_records(current_time=now)
                _last_maintenance_ts = now
            except Exception:
                pass

    return results

_collector_thread = None
_collector_running = False

def _collector_worker():
    global _collector_running
    while _collector_running:
        try:
            collect_historical_snapshot()
        except Exception:
            pass
        time.sleep(2.0)

def start_collector_if_needed():
    global _collector_thread, _collector_running
    if not app.config.get('TESTING') and not _collector_running:
        _collector_running = True
        _collector_thread = threading.Thread(target=_collector_worker, daemon=True)
        _collector_thread.start()

@app.before_request
def _ensure_collector():
    start_collector_if_needed()

# ---------------------------------------------------------------------------
# Priority 4 API Endpoints
# ---------------------------------------------------------------------------

@app.route('/api/history/bottleneck')
def history_bottleneck():
    """Return historical time-series for the bottleneck link (s1-s2).
    Supports ranges: 15m, 1h, 3h, 24h.
    Uses mixed-resolution for 24h (hourly rollups for past hours + raw samples for recent hours).
    """
    range_arg = request.args.get('range', '1h')
    ranges = {'15m': 900, '1h': 3600, '3h': 10800, '24h': 86400}
    window_sec = ranges.get(range_arg, 3600)
    now = time.time()
    start_time = now - window_sec
    db = get_db()
    points = []

    if range_arg == '24h':
        # Align raw cutoff to the next hour boundary using the 4-hour retention constraint
        raw_cutoff = int(math.ceil((now - (4 * 3600)) / 3600.0)) * 3600
        with db.connection() as conn:
            # 1. Historical closed hours from rollups
            cur = conn.execute(
                "SELECT hour_bucket, avg_throughput_mbps, max_throughput_mbps, avg_utilization_pct, max_utilization_pct, congested_sample_count "
                "FROM hourly_link_rollups WHERE link_id = 's1-s2' AND hour_bucket >= ? AND hour_bucket < ? ORDER BY hour_bucket ASC;",
                (start_time, raw_cutoff)
            )
            for r in cur.fetchall():
                points.append({
                    'timestamp': r['hour_bucket'],
                    'throughput_mbps': r['avg_throughput_mbps'],
                    'max_throughput_mbps': r['max_throughput_mbps'],
                    'utilization_pct': r['avg_utilization_pct'],
                    'max_utilization_pct': r['max_utilization_pct'],
                    'status': 'congested' if r['congested_sample_count'] > 0 else 'normal',
                    'measured': True,
                    'resolution': '1h_rollup',
                })

            # 2. Recent raw points
            cur_raw = conn.execute(
                "SELECT timestamp, throughput_mbps, utilization_pct, status, measured "
                "FROM link_metrics_history WHERE link_id = 's1-s2' AND timestamp >= ? ORDER BY timestamp ASC;",
                (raw_cutoff,)
            )
            for r in cur_raw.fetchall():
                points.append({
                    'timestamp': r['timestamp'],
                    'throughput_mbps': r['throughput_mbps'],
                    'utilization_pct': r['utilization_pct'],
                    'status': r['status'],
                    'measured': bool(r['measured']),
                    'resolution': 'raw',
                })
    else:
        with db.connection() as conn:
            cur = conn.execute(
                "SELECT timestamp, throughput_mbps, utilization_pct, status, measured "
                "FROM link_metrics_history WHERE link_id = 's1-s2' AND timestamp >= ? ORDER BY timestamp ASC;",
                (start_time,)
            )
            for r in cur.fetchall():
                points.append({
                    'timestamp': r['timestamp'],
                    'throughput_mbps': r['throughput_mbps'],
                    'utilization_pct': r['utilization_pct'],
                    'status': r['status'],
                    'measured': bool(r['measured']),
                    'resolution': 'raw',
                })

    return jsonify({
        'link_id': 's1-s2',
        'range': range_arg,
        'start_time': start_time,
        'end_time': now,
        'capacity_mbps': 5.0,
        'threshold_pct': 80.0,
        'point_count': len(points),
        'points': points,
    })

@app.route('/api/history/hosts')
def history_hosts():
    """Return historical host telemetry.
    Explicitly labels CPU/memory as shared host VM metrics.
    """
    range_arg = request.args.get('range', '1h')
    host_arg = request.args.get('host', 'all')
    ranges = {'15m': 900, '1h': 3600, '3h': 10800, '24h': 86400}
    window_sec = ranges.get(range_arg, 3600)
    now = time.time()
    start_time = now - window_sec
    db = get_db()

    hosts_data = {}
    with db.connection() as conn:
        if host_arg == 'all':
            cur = conn.execute(
                "SELECT timestamp, host_id, vm_cpu_util, vm_mem_util, telemetry_loss_rate, agent_status "
                "FROM host_telemetry_history WHERE timestamp >= ? ORDER BY timestamp ASC;",
                (start_time,)
            )
        else:
            cur = conn.execute(
                "SELECT timestamp, host_id, vm_cpu_util, vm_mem_util, telemetry_loss_rate, agent_status "
                "FROM host_telemetry_history WHERE host_id = ? AND timestamp >= ? ORDER BY timestamp ASC;",
                (host_arg, start_time)
            )

        for r in cur.fetchall():
            h_id = r['host_id']
            if h_id not in hosts_data:
                hosts_data[h_id] = []
            hosts_data[h_id].append({
                'timestamp': r['timestamp'],
                'vm_cpu_util': r['vm_cpu_util'],
                'vm_mem_util': r['vm_mem_util'],
                'telemetry_loss_rate': r['telemetry_loss_rate'],
                'agent_status': r['agent_status'],
            })

    return jsonify({
        'range': range_arg,
        'start_time': start_time,
        'end_time': now,
        'vm_scope': 'Shared Mininet Host VM Namespace',
        'hosts': hosts_data,
    })

@app.route('/api/history/flows/top')
def history_flows_top():
    """Return time-series throughput for the top talkers in the selected time window."""
    range_arg = request.args.get('range', '1h')
    try:
        top_k = min(10, max(1, int(request.args.get('top', 5))))
    except ValueError:
        top_k = 5
    ranges = {'15m': 900, '1h': 3600, '3h': 10800, '24h': 86400}
    window_sec = ranges.get(range_arg, 3600)
    now = time.time()
    start_time = now - window_sec
    db = get_db()

    flows_result = []
    with db.connection() as conn:
        cur_top = conn.execute("""
            SELECT flow_id, switch, source, destination, protocol,
                   MAX(traverses_bottleneck) as traverses_bottleneck,
                   ROUND(AVG(COALESCE(throughput_mbps, 0.0)), 3) as avg_rate,
                   ROUND(MAX(COALESCE(throughput_mbps, 0.0)), 3) as peak_rate
            FROM flow_history
            WHERE timestamp >= ? AND is_mitigation = 0
            GROUP BY flow_id
            ORDER BY avg_rate DESC, peak_rate DESC
            LIMIT ?;
        """, (start_time, top_k))
        top_flows = cur_top.fetchall()

        for tf in top_flows:
            f_id = tf['flow_id']
            cur_pts = conn.execute(
                "SELECT timestamp, throughput_mbps, share_of_bottleneck_pct, measured "
                "FROM flow_history WHERE flow_id = ? AND timestamp >= ? ORDER BY timestamp ASC;",
                (f_id, start_time)
            )
            points = [{
                'timestamp': r['timestamp'],
                'throughput_mbps': r['throughput_mbps'],
                'share_of_bottleneck_pct': r['share_of_bottleneck_pct'],
                'measured': bool(r['measured']),
            } for r in cur_pts.fetchall()]

            flows_result.append({
                'flow_id': f_id,
                'switch': tf['switch'],
                'source': tf['source'],
                'destination': tf['destination'],
                'protocol': tf['protocol'],
                'traverses_bottleneck': bool(tf['traverses_bottleneck']),
                'avg_throughput_mbps': tf['avg_rate'],
                'peak_throughput_mbps': tf['peak_rate'],
                'points': points,
            })

    return jsonify({
        'range': range_arg,
        'start_time': start_time,
        'end_time': now,
        'flows_count': len(flows_result),
        'top_flows': flows_result,
    })

@app.route('/api/analytics/summary')
def analytics_summary():
    """Return executive NOC KPIs (CFP, MTTM, MTTR, Peak Util) and storage metrics."""
    range_arg = request.args.get('range', '1h')
    ranges = {'15m': 900, '1h': 3600, '3h': 10800, '24h': 86400}
    window_sec = ranges.get(range_arg, 3600)
    now = time.time()
    start_time = now - window_sec
    db = get_db()

    with db.connection() as conn:
        # 1. Congestion-Free Percentage and Peak Utilization
        cur_util = conn.execute(
            "SELECT utilization_pct, timestamp FROM link_metrics_history "
            "WHERE link_id = 's1-s2' AND timestamp >= ? AND measured = 1;",
            (start_time,)
        )
        samples = cur_util.fetchall()
        total_samples = len(samples)
        congestion_free_pct = None
        peak_util = None
        peak_ts = None

        if total_samples > 0:
            congested_count = sum(1 for s in samples if s['utilization_pct'] is not None and s['utilization_pct'] >= 80.0)
            congestion_free_pct = round(((total_samples - congested_count) / total_samples) * 100.0, 1)

            valid_utils = [s for s in samples if s['utilization_pct'] is not None]
            if valid_utils:
                peak_sample = max(valid_utils, key=lambda s: s['utilization_pct'])
                peak_util = round(peak_sample['utilization_pct'], 1)
                peak_ts = peak_sample['timestamp']

        # 2. MTTM and MTTR from incident_events
        cur_mttm = conn.execute(
            "SELECT AVG(time_to_mitigation_sec) FROM incident_events "
            "WHERE event_type = 'CONGESTION' AND target = 's1-s2' AND start_time >= ? AND time_to_mitigation_sec IS NOT NULL;",
            (start_time,)
        )
        mttm_row = cur_mttm.fetchone()[0]
        mttm_sec = round(mttm_row, 2) if mttm_row is not None else None

        cur_mttr = conn.execute(
            "SELECT AVG(time_to_recovery_sec) FROM incident_events "
            "WHERE event_type = 'CONGESTION' AND target = 's1-s2' AND start_time >= ? AND state = 'RESOLVED' AND time_to_recovery_sec IS NOT NULL;",
            (start_time,)
        )
        mttr_row = cur_mttr.fetchone()[0]
        mttr_sec = round(mttr_row, 2) if mttr_row is not None else None

        # Incident counts
        cur_active_cnt = conn.execute("SELECT COUNT(*) FROM incident_events WHERE state = 'ACTIVE';")
        active_incidents_cnt = cur_active_cnt.fetchone()[0]

        cur_window_inc = conn.execute("SELECT COUNT(*) FROM incident_events WHERE start_time >= ?;", (start_time,))
        total_incidents_in_window = cur_window_inc.fetchone()[0]

    storage_stats = db.get_storage_stats()

    return jsonify({
        'range': range_arg,
        'start_time': start_time,
        'end_time': now,
        'metrics': {
            'congestion_free_pct': congestion_free_pct,
            'peak_utilization_pct': peak_util,
            'peak_utilization_timestamp': peak_ts,
            'mttm_seconds': mttm_sec,
            'mttr_seconds': mttr_sec,
            'active_incidents_count': active_incidents_cnt,
            'total_incidents_in_window': total_incidents_in_window,
        },
        'storage': {
            'db_size_bytes': storage_stats['db_size_bytes'],
            'wal_size_bytes': storage_stats['wal_size_bytes'],
            'total_size_bytes': storage_stats['total_size_bytes'],
            'raw_retention_hours': int(os.environ.get('NETWATCH_RAW_RETENTION_HOURS', '4')),
            'rollup_retention_days': int(os.environ.get('NETWATCH_ROLLUP_RETENTION_DAYS', '7')),
            'row_counts': storage_stats['row_counts'],
        }
    })

@app.route('/api/analytics/incidents')
def analytics_incidents():
    """Return incidents from the state machine lifecycle log."""
    state_filter = request.args.get('state')
    try:
        limit_arg = min(100, max(1, int(request.args.get('limit', 50))))
    except ValueError:
        limit_arg = 50
    db = get_db()

    with db.connection() as conn:
        if state_filter in ('ACTIVE', 'RESOLVED'):
            cur = conn.execute(
                "SELECT * FROM incident_events WHERE state = ? ORDER BY start_time DESC LIMIT ?;",
                (state_filter, limit_arg)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM incident_events ORDER BY start_time DESC LIMIT ?;",
                (limit_arg,)
            )
        incidents = [dict(r) for r in cur.fetchall()]

    return jsonify({
        'count': len(incidents),
        'incidents': incidents,
    })

@app.route('/api/analytics/anomalies')
def analytics_anomalies():
    """Return detected anomaly events (bursts, VM saturation, heartbeats, congestion)."""
    try:
        limit_arg = min(50, max(1, int(request.args.get('limit', 20))))
    except ValueError:
        limit_arg = 20
    db = get_db()

    with db.connection() as conn:
        cur = conn.execute(
            "SELECT incident_id, event_type, target, severity, start_time, end_time, duration_sec, peak_value, trigger_threshold, description "
            "FROM incident_events WHERE event_type IN ('TRAFFIC_BURST', 'VM_CPU_SATURATION', 'HOST_SILENT', 'CONGESTION') "
            "ORDER BY start_time DESC LIMIT ?;",
            (limit_arg,)
        )
        anomalies = [dict(r) for r in cur.fetchall()]

    return jsonify({
        'count': len(anomalies),
        'anomalies': anomalies,
    })

if __name__ == '__main__':
    print("Starting NETWATCH Dashboard on http://127.0.0.1:5000")
    app.run(host='127.0.0.1', port=5000, debug=False)
