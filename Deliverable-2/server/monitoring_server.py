#!/usr/bin/env python3
"""Phase 1: Bulletproof UDP monitoring server."""
import json, socket, time, os
from collections import defaultdict

PORT = 9999
LOG = '/tmp/monitor_alerts.log'
STATE_FILE = '/tmp/server_state.json'
LOSS_TH = 0.05
CPU_TH = 70.0
MEM_TH = 85.0

state = defaultdict(lambda: {
    'seq_window': [], 'last_seen': time.time(), 'last_status': 0,
    'has_alerted_silent': False, 'loss': 0.0, 'cpu': 0.0, 'mem': 0.0,
    'last_loss_alert': 0, 'last_cpu_alert': 0, 'last_mem_alert': 0
})
ALERT_COOLDOWN = 10.0

# CPU and memory are shared VM-wide measurements, not per-host metrics.
last_vm_cpu_alert = 0.0
last_vm_mem_alert = 0.0

IP_TO_HOST = {
    '10.0.0.1': 'h1', '10.0.0.2': 'h2', '10.0.0.3': 'h3',
    '10.0.0.4': 'h4', '10.0.0.5': 'h5', '10.0.0.6': 'h6'
}

def normalize_host(raw_host, client_ip):
    """Normalize reported hostnames to canonical h1..h6."""
    cand = str(raw_host or '').strip()
    if cand in IP_TO_HOST.values():
        return cand
    if cand in IP_TO_HOST:
        return IP_TO_HOST[cand]
    if client_ip in IP_TO_HOST:
        return IP_TO_HOST[client_ip]
    return cand or client_ip

def calculate_loss(expected, received):
    if expected == 0: return 0.0
    return (expected - received) / expected

def log_msg(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    try:
        with open(LOG, 'a') as f: f.write(line + '\n')
        os.chmod(LOG, 0o644)
    except OSError: pass

def write_state():
    snap = {}
    now = time.time()
    for h, s in state.items():
        age = now - s['last_seen']
        status = "DOWN" if age > 5.0 else ("WARN" if s['loss'] > LOSS_TH or s['cpu'] > CPU_TH or s['mem'] > MEM_TH else "OK")
        snap[h] = {
            'last_seen': s['last_seen'], 'loss': s['loss'],
            'cpu': s['cpu'], 'mem': s['mem'], 'status': status, 'age': age
        }
    # Defect 5: include snapshot_timestamp for freshness tracking
    snap['snapshot_timestamp'] = now
    try:
        tmp = STATE_FILE + '.tmp'
        with open(tmp, 'w') as f: json.dump(snap, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATE_FILE)
    except OSError: pass

def check_silent_hosts():
    now = time.time()
    for host, st in state.items():
        if (now - st['last_seen'] > 5.0) and not st['has_alerted_silent']:
            log_msg(f'SERVER-ALERT: {host} is silent (no telemetry > 5s)')
            st['has_alerted_silent'] = True

def check_vm_resource_alerts(cpu, mem, now):
    """Emit VM-wide CPU/memory alerts with independent shared cooldowns."""
    global last_vm_cpu_alert, last_vm_mem_alert

    if cpu > CPU_TH and (now - last_vm_cpu_alert > ALERT_COOLDOWN):
        log_msg(f'SERVER-ALERT: high CPU on mininet-vm: {cpu:.1f}% (shared VM metric)')
        last_vm_cpu_alert = now

    if mem > MEM_TH and (now - last_vm_mem_alert > ALERT_COOLDOWN):
        log_msg(f'SERVER-ALERT: high MEM on mininet-vm: {mem:.1f}% (shared VM metric)')
        last_vm_mem_alert = now


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    sock.settimeout(1.0)
    log_msg('SERVER STARTED')
    last_silent_check = time.time()

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            pkt = json.loads(data.decode())
            if not isinstance(pkt, dict): continue

            host = normalize_host(pkt.get('host'), addr[0])
            seq = int(pkt.get('seq', 0))
            cpu = float(pkt.get('cpu_load', 0))
            mem = float(pkt.get('mem_percent', 0))
            now = time.time()

            st = state[host]
            st['last_seen'] = now
            st['has_alerted_silent'] = False
            st['cpu'] = cpu
            st['mem'] = mem

            if len(st['seq_window']) > 0 and seq <= st['seq_window'][-1]:
                st['seq_window'] = []
                st['loss'] = 0.0

            st['seq_window'].append(seq)
            if len(st['seq_window']) > 10: st['seq_window'].pop(0)

            if len(st['seq_window']) > 1:
                expected = st['seq_window'][-1] - st['seq_window'][0] + 1
                loss = calculate_loss(expected, len(st['seq_window']))
                st['loss'] = loss
                if loss > LOSS_TH and (now - st['last_loss_alert'] > ALERT_COOLDOWN):
                    log_msg(f'SERVER-ALERT: packet loss on {host}: {loss*100:.1f}%')
                    st['last_loss_alert'] = now

            check_vm_resource_alerts(cpu, mem, now)

            write_state()
        except socket.timeout:
            pass
        except Exception:
            pass

        if time.time() - last_silent_check > 1.0:
            check_silent_hosts()
            write_state()
            last_silent_check = time.time()

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print('\n[server] stopped')
