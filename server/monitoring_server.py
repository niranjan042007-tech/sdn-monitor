#!/usr/bin/env python3
"""Bulletproof UDP monitoring server with Sliding Window Loss Detection."""
import json
import socket
import time
from collections import defaultdict

PORT = 9999
LOG = '/tmp/monitor_alerts.log'
STATE_FILE = '/tmp/server_state.json'
LOSS_TH = 0.05
CPU_TH = 2.0
MEM_TH = 85.0

# Per-host state (We add 'loss' here so it remembers)
state = defaultdict(lambda: {'seq_window': [], 'last_seen': 0, 'last_status': 0, 'has_alerted_silent': False, 'loss': 0.0})

def calculate_loss(expected, received):
    if expected == 0: return 0.0
    return (expected - received) / expected

def log_msg(msg):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line, flush=True)
    try:
        with open(LOG, 'a') as f: f.write(line + '\n')
    except OSError: pass

def write_state():
    # Now we just read from the state dictionary which remembers the loss!
    snap = {h: {'last_seen': s['last_seen'], 'loss': s['loss']} for h, s in state.items()}
    try:
        with open(STATE_FILE, 'w') as f: json.dump(snap, f)
    except OSError: pass

def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', PORT))
    sock.settimeout(1.0)
    log_msg('SERVER STARTED')

    while True:
        try:
            data, addr = sock.recvfrom(4096)
            pkt = json.loads(data.decode())
            if not isinstance(pkt, dict): continue
            
            host = str(pkt.get('host', addr[0]))
            seq = int(pkt.get('seq', 0))
            cpu = float(pkt.get('cpu_load', 0))
            mem = float(pkt.get('mem_percent', 0))

            st = state[host]
            st['last_seen'] = time.time()
            st['has_alerted_silent'] = False
            
            # 1. SLIDING WINDOW LOGIC
            st['seq_window'].append(seq)
            if len(st['seq_window']) > 10:
                st['seq_window'].pop(0)

            if len(st['seq_window']) > 1:
                expected = st['seq_window'][-1] - st['seq_window'][0] + 1
                loss = calculate_loss(expected, len(st['seq_window']))
                st['loss'] = loss # SAVE THE LOSS HERE
                
                if loss > LOSS_TH:
                    log_msg(f'SERVER-ALERT: packet loss on {host}: {loss*100:.1f}%')
            else:
                st['loss'] = 0.0

            # 2. CPU / MEM THRESHOLDS
            if cpu > CPU_TH: log_msg(f'SERVER-ALERT: high CPU on {host}')
            if mem > MEM_TH: log_msg(f'SERVER-ALERT: high MEM on {host}')

            # 3. STATUS LOGS
            if time.time() - st['last_status'] > 5.0:
                log_msg(f'STATUS {host} rx={len(st["seq_window"])} loss={st["loss"]*100:.1f}%')
                st['last_status'] = time.time()

            # 4. WRITE STATE FOR SDN CONTROLLER
            write_state()

        except socket.timeout:
            now = time.time()
            for host, st in state.items():
                if (now - st['last_seen'] > 5.0) and not st['has_alerted_silent']:
                    log_msg(f'SERVER-ALERT: {host} is silent (no telemetry > 5s)')
                    st['has_alerted_silent'] = True
        except Exception:
            pass

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print('\n[server] stopped')
