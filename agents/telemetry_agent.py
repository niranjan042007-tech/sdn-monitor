#!/usr/bin/env python3
"""UDP telemetry agent."""
import json
import os
import socket
import sys
import time

try:
    import psutil
except ImportError:
    psutil = None

TELEMETRY_PORT = 9999
INTERVAL       = 1.0

def mem_percent():
    if psutil:
        return psutil.virtual_memory().percent
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')
                info[k.strip()] = int(v.split()[0])
        return (1 - info['MemAvailable'] / info['MemTotal']) * 100
    except Exception:
        return 0.0

def build_packet(hostname, seq):
    """Builds the telemetry dictionary. Used by agent and unit tests."""
    return {
        'host'       : hostname,
        'ts'         : time.time(),
        'seq'        : seq,
        'cpu_load'   : round(os.getloadavg()[0], 3),
        'mem_percent': round(mem_percent(), 2),
        'net_bytes'  : psutil.net_io_counters().bytes_recv if psutil else None,
    }

def main():
    hostname  = sys.argv[1] if len(sys.argv) > 1 else socket.gethostname()
    server_ip = sys.argv[2] if len(sys.argv) > 2 else '10.0.0.5'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq  = 0
    print(f'[agent {hostname}] sending to {server_ip}:{TELEMETRY_PORT}')
    try:
        while True:
            seq += 1
            pkt = build_packet(hostname, seq)
            sock.sendto(json.dumps(pkt).encode(), (server_ip, TELEMETRY_PORT))
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print('\n[agent] stopped')

if __name__ == '__main__':
    main()
