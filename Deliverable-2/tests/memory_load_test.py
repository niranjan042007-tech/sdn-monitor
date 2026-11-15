#!/usr/bin/env python3
import sys, time
def auto_size_mb(target_pct=90.0, keep_free_mb=400):
    """Size the allocation so total RAM use reaches ~target_pct (above the
    85% alert threshold) while always leaving keep_free_mb available."""
    info = {}
    with open('/proc/meminfo') as f:
        for line in f:
            k, v = line.split(':', 1)
            info[k] = int(v.split()[0]) // 1024  # MB
    total, avail = info['MemTotal'], info.get('MemAvailable', info['MemFree'])
    used = total - avail
    need = int(total * target_pct / 100.0) - used
    return max(100, min(need, avail - keep_free_mb))

def main():
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    size_mb = int(sys.argv[2]) if len(sys.argv) > 2 else auto_size_mb()
    print(f'[mem_load] allocating {size_mb} MB for {duration}s...')
    try:
        blob = bytearray(size_mb * 1024 * 1024)
        for i in range(0, len(blob), 4096):
            blob[i] = 1
        time.sleep(duration)
        del blob
        print('[mem_load] released')
    except MemoryError:
        print('[mem_load] out of memory')
if __name__ == '__main__':
    main()
