#!/usr/bin/env python3
"""Self-generated congestion test."""
import socket
import time
import sys

DEST = ('10.0.0.5', 5555)
RATE_MBPS = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
DURATION = int(sys.argv[2]) if len(sys.argv) > 2 else 20

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
payload = b'X' * 1200
sent, t0 = 0, time.time()

print(f'[congestion] sending {RATE_MBPS} Mbps for {DURATION}s -> {DEST}')
while time.time() - t0 < DURATION:
    sock.sendto(payload, DEST)
    sent += 1
    # Rate control
    expected_time = (sent * len(payload) * 8) / (RATE_MBPS * 1e6)
    actual_time = time.time() - t0
    if expected_time > actual_time:
        time.sleep(expected_time - actual_time)
        
    if sent % 1000 == 0:
        mbps = sent * 1200 * 8 / (time.time() - t0) / 1e6
        print(f'  sent={sent} avg={mbps:.2f} Mbps')
print(f'[congestion] done, total={sent}')
