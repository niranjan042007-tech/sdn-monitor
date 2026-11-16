#!/usr/bin/env python3
"""UDP telemetry agent — VM-wide CPU/Memory measurement.

NOTE (Defect 6): The CPU and memory values reported by this agent are VM-wide
readings from /proc/stat and /proc/meminfo respectively. They are NOT
per-host or per-container isolated values. In Mininet, all hosts share the
same Linux kernel and the same /proc filesystem, so cpu_load and mem_percent
reflect VM-wide resource usage, not individual Mininet host isolation.
"""
import atexit
import json
import os
import signal
import socket
import sys
import tempfile
import time

TELEMETRY_PORT = 9999
INTERVAL = 1.0

# On Linux (Mininet production), /tmp is always available.
# On Windows (CI/offline tests), use the system temp directory.
_TMPDIR = os.environ.get(
    "NETWATCH_TMPDIR",
    '/tmp' if sys.platform.startswith('linux') else tempfile.gettempdir()
)

# Track previous CPU times for accurate delta calculation
_prev_cpu = {'idle': 0, 'total': 0}

def get_host_cpu_percent():
    """Reads VM-wide CPU usage from /proc/stat.

    NOTE: This reads the global /proc/stat, which reflects the entire VM's CPU
    utilization — NOT an isolated per-Mininet-host value.
    """
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()
        # user, nice, system, idle, iowait, irq, softirq, steal
        user, nice, system, idle, iowait, irq, softirq, steal = map(int, parts[1:9])
        total = user + nice + system + idle + iowait + irq + softirq + steal
        idle_total = idle + iowait

        prev_idle = _prev_cpu['idle']
        prev_total = _prev_cpu['total']
        _prev_cpu['idle'] = idle_total
        _prev_cpu['total'] = total

        total_delta = total - prev_total
        idle_delta = idle_total - prev_idle
        if total_delta == 0:
            return 0.0
        cpu_percent = 100.0 * (total_delta - idle_delta) / total_delta
        return round(cpu_percent, 2)
    except Exception:
        return 0.0

def get_host_mem_percent():
    """Reads VM-wide memory usage from /proc/meminfo.

    NOTE: This reads the global /proc/meminfo, which reflects the entire VM's memory
    utilization — NOT an isolated per-Mininet-host value.
    """
    try:
        info = {}
        with open('/proc/meminfo') as f:
            for line in f:
                k, v = line.split(':')
                info[k.strip()] = int(v.split()[0])
        total = info['MemTotal']
        avail = info.get('MemAvailable', info['MemFree'])
        return round((1 - avail / total) * 100, 2)
    except Exception:
        return 0.0

def build_packet(hostname, seq):
    """Builds the telemetry dictionary with REAL metrics."""
    return {
        'host': hostname,
        'ts': time.time(),
        'seq': seq,
        'cpu_load': get_host_cpu_percent(),
        'mem_percent': get_host_mem_percent(),
    }

# Map well-known Mininet IPs to canonical hostnames
IP_TO_HOST = {
    '10.0.0.1': 'h1',
    '10.0.0.2': 'h2',
    '10.0.0.3': 'h3',
    '10.0.0.4': 'h4',
    '10.0.0.5': 'h5',
    '10.0.0.6': 'h6',
}

def resolve_hostname(arg_name=None, server_ip='10.0.0.5'):
    """Resolve and enforce canonical hostnames (h1..h6)."""
    if arg_name:
        cand = str(arg_name).strip()
        if cand in IP_TO_HOST.values():
            return cand
        if cand in IP_TO_HOST:
            return IP_TO_HOST[cand]

    # Try detecting local IP assigned to Mininet interface
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((server_ip, TELEMETRY_PORT))
        local_ip = s.getsockname()[0]
        s.close()
        if local_ip in IP_TO_HOST:
            return IP_TO_HOST[local_ip]
    except Exception:
        pass

    raw_name = socket.gethostname()
    return IP_TO_HOST.get(raw_name, raw_name or 'h1')

# PID file path template: {_TMPDIR}/{hostname}_agent.pid
# Written on startup so the topology's kill experiment can target exactly this
# process without relying on command-line pattern matching.
PID_FILE_TEMPLATE = os.path.join(_TMPDIR, '{hostname}_agent.pid')


def _get_starttime(pid: int) -> int | None:
    """Read process start time (field 22) from /proc/{pid}/stat on Linux.

    Returns integer start time (clock ticks since boot), or None if unavailable.
    Used for PID reuse protection (Defect 4).
    """
    try:
        with open(f'/proc/{pid}/stat', 'r') as f:
            content = f.read()
        paren_end = content.rfind(')')
        if paren_end == -1:
            return None
        after_comm = content[paren_end + 1:].split()
        return int(after_comm[19])  # field 22 (0-indexed after comm)
    except (OSError, IndexError, ValueError):
        return None


def write_pid_file(hostname: str) -> str:
    """Write this process's PID (and start time) to {_TMPDIR}/{hostname}_agent.pid atomically.

    Format: PID:STARTTIME (Defect 4: start time enables PID reuse protection).
    Falls back to PID-only format on non-Linux platforms.

    Uses a temporary file and atomic os.replace() to guarantee that no reader
    ever observes an empty (0-byte) or partial PID file.

    Returns the path written, or empty string on failure.
    """
    pid_path = PID_FILE_TEMPLATE.format(hostname=hostname)
    tmp_path = f"{pid_path}.tmp.{os.getpid()}"
    pid = os.getpid()

    # Attempt to get start time for PID reuse protection
    starttime = _get_starttime(pid)
    if starttime is not None:
        content = f"{pid}:{starttime}\n"
    else:
        content = f"{pid}\n"

    try:
        with open(tmp_path, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, pid_path)
        return pid_path
    except OSError as e:
        print(f'[agent {hostname}] WARNING: could not write atomic PID file {pid_path}: {e}')
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return ''


def remove_pid_file(hostname: str) -> None:
    """Remove /tmp/{hostname}_agent.pid on clean exit.

    Handles both legacy PID-only format and new PID:STARTTIME format.
    """
    pid_path = PID_FILE_TEMPLATE.format(hostname=hostname)
    try:
        if os.path.exists(pid_path):
            with open(pid_path) as f:
                content = f.read().strip()
            # Support both "PID" and "PID:STARTTIME" formats
            pid_str = content.split(':', 1)[0]
            # Only remove if the PID file belongs to this process
            if pid_str == str(os.getpid()):
                os.unlink(pid_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        print(f'[agent {hostname}] WARNING: could not remove PID file {pid_path}: {e}')


def _sigterm_handler(signum, frame):
    """Ensure SIGTERM cleanly executes through the finally/atexit handlers."""
    sys.exit(0)


def main():
    server_ip = sys.argv[2] if len(sys.argv) > 2 else '10.0.0.5'
    hostname = resolve_hostname(sys.argv[1] if len(sys.argv) > 1 else None, server_ip)

    # Register signal handler for SIGTERM to ensure clean exit and PID removal
    try:
        signal.signal(signal.SIGTERM, _sigterm_handler)
    except (ValueError, AttributeError):
        pass  # Non-main thread or unsupported platform

    # Register atexit safety net
    atexit.register(remove_pid_file, hostname)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = 0
    print(f'[agent {hostname}] sending real CPU/MEM to {server_ip}:{TELEMETRY_PORT}')

    # Write PID file immediately so the kill experiment can find this process.
    pid_path = write_pid_file(hostname)
    if pid_path:
        print(f'[agent {hostname}] PID {os.getpid()} written atomically to {pid_path}')

    # Prime the CPU delta
    get_host_cpu_percent()
    time.sleep(0.1)
    get_host_cpu_percent()

    try:
        while True:
            seq += 1
            pkt = build_packet(hostname, seq)
            sock.sendto(json.dumps(pkt).encode(), (server_ip, TELEMETRY_PORT))
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print('\n[agent] stopped')
    finally:
        remove_pid_file(hostname)

if __name__ == '__main__':
    main()
