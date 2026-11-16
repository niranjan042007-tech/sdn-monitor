#!/usr/bin/env python3
"""Multi-worker CPU load generator to reliably stress multi-core VMs."""
import multiprocessing
import os
import sys
import time

def _worker(end_time):
    # Busy spin to saturate core without sleeping
    while time.time() < end_time:
        _ = 0
        for i in range(100000):
            _ += i

def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    num_workers = max(1, os.cpu_count() or 1)
    print(f'[cpu_load] starting {num_workers} worker(s) for {duration}s...')
    end = time.time() + duration

    procs = []
    for _ in range(num_workers):
        p = multiprocessing.Process(target=_worker, args=(end,))
        p.daemon = True
        p.start()
        procs.append(p)

    for p in procs:
        p.join()
    print('[cpu_load] done')

if __name__ == '__main__':
    main()
