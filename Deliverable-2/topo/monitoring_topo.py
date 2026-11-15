#!/usr/bin/env python3
try:
    from mininet.topo import Topo
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSKernelSwitch
    from mininet.cli import CLI
    from mininet.log import setLogLevel, info
    from mininet.link import TCLink
except ImportError:
    # Stubs to enable offline syntax checking and unit tests in non-Linux environments
    Topo = object
    Mininet = object
    RemoteController = object
    OVSKernelSwitch = object
    CLI = None
    setLogLevel = lambda level: None
    info = lambda msg: None
    TCLink = None
import errno
import json
import math
import os
import signal
import socket as _socket
import stat
import sys
import tempfile
import threading
import time
import uuid

# On Linux (Mininet production), /tmp is always available.
# On Windows (CI/offline tests), use the system temp directory.
_TMPDIR = os.environ.get(
    "NETWATCH_TMPDIR",
    '/tmp' if sys.platform.startswith('linux') else tempfile.gettempdir()
)
TOPOLOGY_PID_FILE = os.environ.get(
    'NETWATCH_TOPOLOGY_PID_FILE', os.path.join(_TMPDIR, 'netwatch_topology.pid')
)


def _write_topology_pid_file(state):
    """Atomically publish this topology process identity and lifecycle state."""
    pid = os.getpid()
    starttime = _get_proc_starttime(pid)
    payload = f'{pid}:{starttime if starttime is not None else 0}:{state}\n'
    fd, tmp_path = tempfile.mkstemp(prefix='.netwatch-topology-', dir=os.path.dirname(TOPOLOGY_PID_FILE) or '.')
    try:
        # The topology runs under sudo while the launcher runs as the user;
        # the launcher must be able to read the process identity and state.
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, 'w') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, TOPOLOGY_PID_FILE)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _remove_topology_pid_file():
    """Remove the PID record only if it still belongs to this process."""
    try:
        with open(TOPOLOGY_PID_FILE, 'r') as f:
            fields = f.read().strip().split(':')
        if len(fields) >= 2 and int(fields[0]) == os.getpid():
            current_start = _get_proc_starttime(os.getpid())
            recorded_start = int(fields[1])
            if recorded_start == 0 or current_start == recorded_start:
                os.unlink(TOPOLOGY_PID_FILE)
    except (OSError, ValueError):
        pass

# ---------------------------------------------------------------------------
# IPC control socket — allows the Flask dashboard to inject experiments into
# the ACTUAL running Mininet topology without spawning a second Mininet
# instance.  Protocol: newline-terminated JSON over a Unix SOCK_STREAM socket.
#
# Security & Permissions:
#   - Ownership: root:sdn-ipc
#   - Permissions: 0660 (srw-rw----)
#   - Kernel peer credential validation via SO_PEERCRED
#
# Request  (dashboard → topology):
#   {"cmd": "flood|cpu|mem|kill|burst",
#    "host": "h1|h2|h3|h4",
#    "rate": "5|8|10",          # Mbps  (flood / burst only)
#    "duration": <int 1-60>}    # seconds
#
# Response (topology → dashboard):
#   {"status": "ok",    "message": "..."}
#   {"status": "error", "message": "..."}
# ---------------------------------------------------------------------------

CONTROL_SOCK = os.environ.get('NETWATCH_CONTROL_SOCKET', '/tmp/sdn_control.sock')
IPC_GROUP_NAME = os.environ.get('SDN_IPC_GROUP', 'sdn-ipc')
_SO_PEERCRED = getattr(_socket, 'SO_PEERCRED', 17)

# Strict allow-lists — anything outside these is rejected before execution
_ALLOWED_HOSTS    = {'h1', 'h2', 'h3', 'h4'}
_ALLOWED_CMDS     = {'flood', 'cpu', 'mem', 'kill', 'burst', 'status'}
_ALLOWED_RATES    = {'5', '8', '10'}
_MIN_DURATION     = 1
_MAX_DURATION     = 60

# Path to the exported experiment state JSON file
EXPERIMENTS_STATE_FILE = os.path.join(_TMPDIR, 'sdn_experiments.json')
_SIGKILL = getattr(signal, 'SIGKILL', signal.SIGTERM)


def _get_target_gid(group_name=IPC_GROUP_NAME):
    """Resolve GID for the dedicated IPC group. Raises RuntimeError on failure.

    Does not silently fall back to insecure permissions if the group is missing.
    """
    try:
        import grp
        return grp.getgrnam(group_name).gr_gid
    except ImportError:
        # Non-POSIX / Windows test fallback
        return 1000
    except KeyError:
        raise RuntimeError(
            f"Required security group '{group_name}' does not exist on this system. "
            f"Create it with 'sudo groupadd {group_name}' and add authorized users "
            f"with 'sudo usermod -aG {group_name} <username>'."
        )


def _prepare_socket_path(sock_path=CONTROL_SOCK):
    """Safely inspect and handle existing socket paths before binding.

    - Uses os.lstat() to inspect the path without following symlinks.
    - Refuses to overwrite regular files, directories, symlinks, or non-sockets.
    - Probes existing sockets using connect().
    - Treats ONLY reliable stale-socket errors (errno.ECONNREFUSED) as permission to unlink.
    - Treats successful connect or timeout as an active server (never removes active socket).
    - Fails closed on any unexpected OSError.
    - Re-checks device and inode identity immediately prior to unlinking to prevent race conditions.
    """
    try:
        st = os.lstat(sock_path)
    except FileNotFoundError:
        return
    except OSError as e:
        raise RuntimeError(f"Cannot inspect path at {sock_path}: {e}")

    # Refuse regular files, directories, symlinks, and any non-socket
    if stat.S_ISLNK(st.st_mode):
        raise RuntimeError(f"Refusing to overwrite symlink at {sock_path}")
    if stat.S_ISDIR(st.st_mode):
        raise RuntimeError(f"Refusing to overwrite directory at {sock_path}")
    if stat.S_ISREG(st.st_mode):
        raise RuntimeError(f"Refusing to overwrite regular file at {sock_path}")
    if not stat.S_ISSOCK(st.st_mode):
        raise RuntimeError(f"Refusing to overwrite non-socket file (mode {oct(st.st_mode)}) at {sock_path}")

    # Probe existing socket to verify whether an active server is listening
    af_unix = getattr(_socket, 'AF_UNIX', None)
    if af_unix is None:
        # Platform does not support Unix domain sockets (e.g. non-POSIX Windows fallback)
        return

    probe = _socket.socket(af_unix, _socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(sock_path)
        # Connection succeeded: active server is listening! Never remove!
        probe.close()
        raise RuntimeError(
            f"Active server already listening on {sock_path}. "
            "Refusing to remove active socket from another running instance."
        )
    except ConnectionRefusedError:
        # Reliable stale-socket error (ECONNREFUSED): server is dead
        probe.close()
    except FileNotFoundError:
        # File was deleted concurrently
        probe.close()
        return
    except _socket.timeout:
        # Server is active but slow/unresponsive: fail closed! Never remove!
        probe.close()
        raise RuntimeError(
            f"Probe timed out connecting to {sock_path}. Server may be active; failing closed."
        )
    except OSError as e:
        probe.close()
        # Fail closed on unexpected errors; only allow ECONNREFUSED
        if getattr(e, 'errno', None) == errno.ECONNREFUSED:
            pass
        else:
            raise RuntimeError(
                f"Unexpected error probing socket at {sock_path} ({e}); failing closed."
            )

    # Recheck socket device and inode identity immediately before unlinking to reduce race conditions
    try:
        st_pre = os.lstat(sock_path)
        if not stat.S_ISSOCK(st_pre.st_mode):
            raise RuntimeError(f"File at {sock_path} changed type before unlinking; aborting.")
        if (st_pre.st_dev, st_pre.st_ino) != (st.st_dev, st.st_ino):
            raise RuntimeError(f"Socket identity at {sock_path} changed concurrently; aborting.")
        os.unlink(sock_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        raise RuntimeError(f"Failed to remove stale socket at {sock_path}: {e}")


def _is_peer_authorized(peer_pid, peer_uid, peer_gid, target_gid):
    """Validate kernel-provided peer credentials against security policy.

    SO_PEERCRED provides the peer process's PID, effective UID, and effective GID.
    It does NOT provide the peer's supplementary group list.

    Consistent Access Policy:
    - UID 0 (root) is authorized.
    - A peer with effective GID matching target_gid (sdn-ipc) is authorized.
    - All other peers are rejected.
    """
    if peer_uid == 0:
        return True
    if peer_gid == target_gid:
        return True
    return False


def _validate(req: dict):
    """Return (host, cmd, rate, duration) or raise ValueError with a message."""
    cmd  = str(req.get('cmd',  '')).strip()
    if cmd == 'status':
        return 'h1', 'status', '8', 1

    host = str(req.get('host', '')).strip()
    rate = str(req.get('rate', '8')).strip()
    try:
        duration = int(req.get('duration', 20))
    except (TypeError, ValueError):
        raise ValueError('duration must be an integer')

    if cmd  not in _ALLOWED_CMDS:
        raise ValueError(f'unknown cmd {cmd!r}; allowed: {sorted(_ALLOWED_CMDS)}')
    if host not in _ALLOWED_HOSTS:
        raise ValueError(f'unknown host {host!r}; allowed: {sorted(_ALLOWED_HOSTS)}')
    if cmd in ('flood', 'burst') and rate not in _ALLOWED_RATES:
        raise ValueError(f'invalid rate {rate!r}; allowed: {sorted(_ALLOWED_RATES)}')
    if not (_MIN_DURATION <= duration <= _MAX_DURATION):
        raise ValueError(f'duration {duration} out of range [{_MIN_DURATION}, {_MAX_DURATION}]')

    return host, cmd, rate, duration


# ---------------------------------------------------------------------------
# Experiment lifecycle tracking
# ---------------------------------------------------------------------------

class ExperimentTracker:
    """Thread-safe registry and supervisor for active and historical experiments.

    Enforces single-experiment concurrency across the network topology,
    tracks child PIDs, prevents stale threads from corrupting state,
    and maintains a bounded historical audit log of experiment outcomes.

    Lifecycle states:
      RUNNING → RECOVERING → RECOVERED | RECOVERY_FAILED | RECOVERY_TIMEOUT
    """

    def __init__(self):
        self._lock = threading.Lock()
        # key: (host, cmd) → experiment dict (for backward-compatible per-host/cmd lookups)
        self._active: dict = {}
        # Single active experiment across the entire topology
        self._current_active: dict | None = None
        # Bounded history of completed experiments (newest last)
        self._history: list = []

    def start(self, host: str, cmd: str, duration: int, rate: str = '8', baseline: dict = None, allow_supersede: bool = True) -> str:
        """Register a new experiment; return its unique experiment ID.

        If allow_supersede is False, rejects the experiment if another experiment
        is currently RUNNING or RECOVERING.
        """
        exp_id = str(uuid.uuid4())
        now = time.time()
        entry = {
            'exp_id':          exp_id,
            'host':            host,
            'cmd':             cmd,
            'rate':            str(rate),
            'duration':        duration,
            'start_time':      now,
            'expiry':          now + duration,
            'state':           'RUNNING',
            'child_pid':       None,
            'baseline':        baseline or {},
            'recovery_checks': {},
            'failed_checks':   [],
            'error':           None,
            'completed_at':    None,
        }
        with self._lock:
            if not allow_supersede and self._current_active is not None and self._current_active.get('state') in ('RUNNING', 'RECOVERING'):
                curr = self._current_active
                raise ValueError(
                    f"Active experiment already running: {curr['cmd']} on {curr['host']} "
                    f"(ID: {curr['exp_id'][:8]}, State: {curr['state']}). "
                    "Wait for recovery before starting a new experiment."
                )
            self._current_active = entry
            self._active[(host, cmd)] = entry
            self._export_state_locked()
        return exp_id

    def set_child_pid(self, host: str, cmd: str, exp_id: str, pid: int | None) -> None:
        """Associate the supervised child process PID with this experiment."""
        with self._lock:
            entry = self._active.get((host, cmd))
            if entry and entry['exp_id'] == exp_id:
                entry['child_pid'] = pid
                self._export_state_locked()

    def is_current(self, host: str, cmd: str, exp_id: str) -> bool:
        """Return True iff exp_id is still the active experiment for (host, cmd)."""
        with self._lock:
            entry = self._active.get((host, cmd))
            return entry is not None and entry['exp_id'] == exp_id

    def set_state(self, host: str, cmd: str, exp_id: str, state: str, recovery_checks: dict = None, failed_checks: list = None) -> None:
        """Update state only if exp_id is still the active experiment."""
        with self._lock:
            entry = self._active.get((host, cmd))
            if entry is not None and entry['exp_id'] == exp_id:
                entry['state'] = state
                if recovery_checks is not None:
                    entry['recovery_checks'] = recovery_checks
                if failed_checks is not None:
                    entry['failed_checks'] = failed_checks
                self._export_state_locked()

    def complete(self, host: str, cmd: str, exp_id: str, final_state: str, recovery_checks: dict = None, failed_checks: list = None, error: str = None) -> None:
        """Complete an experiment, record its outcome, move it to history, and release active slot."""
        with self._lock:
            entry = self._active.get((host, cmd))
            if entry is not None and entry['exp_id'] == exp_id:
                entry['state'] = final_state
                entry['recovery_checks'] = recovery_checks or {}
                entry['failed_checks'] = failed_checks or []
                entry['error'] = error
                entry['completed_at'] = time.time()
                # Append to bounded history (last 50)
                self._history.append(dict(entry))
                if len(self._history) > 50:
                    self._history.pop(0)
                del self._active[(host, cmd)]
                if self._current_active and self._current_active['exp_id'] == exp_id:
                    self._current_active = None
                self._export_state_locked()

    def finish(self, host: str, cmd: str, exp_id: str) -> None:
        """Backward-compatible finish for existing unit tests."""
        self.complete(host, cmd, exp_id, 'RECOVERED')

    def has_active(self, host: str, cmd: str) -> bool:
        """Return True if there is an active experiment for (host, cmd)."""
        with self._lock:
            return (host, cmd) in self._active

    def has_active_global(self) -> bool:
        """Return True if any experiment is currently RUNNING or RECOVERING."""
        with self._lock:
            return self._current_active is not None and self._current_active.get('state') in ('RUNNING', 'RECOVERING')

    def get_active_global(self) -> dict | None:
        with self._lock:
            return dict(self._current_active) if self._current_active else None

    def snapshot(self) -> list:
        """Return a list of all active experiment records (copies)."""
        with self._lock:
            return [dict(v) for v in self._active.values()]

    def get_status(self) -> dict:
        """Return current active experiment and full bounded history."""
        with self._lock:
            return {
                'active': dict(self._current_active) if self._current_active else None,
                'history': [dict(h) for h in reversed(self._history)],
            }

    def _export_state_locked(self) -> None:
        """Atomically export experiment state to EXPERIMENTS_STATE_FILE."""
        try:
            payload = {
                'active': dict(self._current_active) if self._current_active else None,
                'history': [dict(h) for h in reversed(self._history[-25:])],
                'updated_at': time.time(),
            }
            # Use a fresh, uniquely named temp file. A fixed '<file>.tmp' name can be
            # left behind by a non-root test run; with Ubuntu's fs.protected_regular
            # the root topology then cannot reopen it, every export fails, and the
            # dashboard keeps serving a frozen history (the 'missing burst' bug).
            fd, tmp = tempfile.mkstemp(prefix='sdn_experiments.', suffix='.tmp',
                                       dir=os.path.dirname(EXPERIMENTS_STATE_FILE) or '.')
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(payload, f, indent=2)
                os.chmod(tmp, 0o644)
                os.replace(tmp, EXPERIMENTS_STATE_FILE)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as exc:
            # Never fail silently: a stale state file misleads the dashboard.
            try:
                info(f'*** [EXPERIMENT] WARNING: could not export experiment state: {exc}\n')
            except Exception:
                pass


# Module-level tracker — shared by all socket handler threads
_experiment_tracker = ExperimentTracker()


# ---------------------------------------------------------------------------
# Validated, host-specific agent termination
# ---------------------------------------------------------------------------

# Template must match what telemetry_agent.py writes.
# _TMPDIR is /tmp on Linux and the system temp dir on Windows CI.
_AGENT_PID_FILE = os.path.join(_TMPDIR, '{host}_agent.pid')


def _read_agent_pid(host: str):
    """Read and parse /tmp/{host}_agent.pid.

    Supports both legacy format (just PID) and new format (PID:STARTTIME).
    Returns (pid: int, starttime: int|None, error: str|None).
    """
    pid_path = _AGENT_PID_FILE.format(host=host)
    try:
        with open(pid_path) as f:
            raw = f.read().strip()
    except FileNotFoundError:
        return None, None, f'PID file {pid_path} not found (agent may not be running)'
    except OSError as e:
        return None, None, f'Cannot read PID file {pid_path}: {e}'

    if not raw:
        return None, None, f'Empty PID file {pid_path}'

    # Support PID:STARTTIME format (Priority 5 & 5.2)
    if ':' in raw:
        parts = raw.split(':')
        if len(parts) != 2:
            return None, None, f'Invalid PID:STARTTIME format {raw!r} in {pid_path}'
        try:
            pid = int(parts[0])
            starttime = int(parts[1])
        except ValueError:
            return None, None, f'Invalid PID:STARTTIME format {raw!r} in {pid_path}'
        if pid <= 0 or starttime < 0:
            return None, None, f'Invalid PID {pid!r} or starttime {starttime!r} in {pid_path}'
        return pid, starttime, None
    else:
        # Legacy format: PID only. Explicit compatibility policy:
        # returns (pid, None, None) where starttime is None.
        try:
            pid = int(raw)
            if pid <= 0:
                return None, None, f'Invalid PID {pid!r} in {pid_path}'
            return pid, None, None
        except ValueError:
            return None, None, f'Non-integer content {raw!r} in {pid_path}'


def _get_proc_starttime(pid: int) -> int | None:
    """Read process start time (field 22) from /proc/{pid}/stat on Linux.

    The start time is in clock ticks since boot.
    Returns integer start time, or None if unavailable (non-Linux or missing).
    """
    if not sys.platform.startswith('linux'):
        return None
    try:
        with open(f'/proc/{pid}/stat', 'r') as f:
            content = f.read()
        # Field 22 is starttime; fields are split after the comm field (enclosed in parens)
        # Format: pid (comm) state ppid pgroup session tty_nr ... starttime ...
        paren_end = content.rfind(')')
        if paren_end == -1:
            return None
        after_comm = content[paren_end + 1:].split()
        # after_comm[0]=state, [1]=ppid, ... [19]=starttime (index 19, field 22)
        starttime = int(after_comm[19])
        return starttime
    except (OSError, IndexError, ValueError):
        return None


def _validate_agent_pid(pid: int, host: str, expected_starttime: int | None = None) -> str | None:
    """Verify that PID belongs to the telemetry agent for `host`.

    Reads /proc/{pid}/cmdline and checks process start time on Linux.
    On non-Linux platforms (Windows CI), validation is skipped (returns None = valid).

    Defect 4: also validates start time from /proc/{pid}/stat to prevent PID reuse.
    If expected_starttime is provided, the current start time must match.

    Returns None if PID is valid, or an error string if validation fails.
    """
    if not sys.platform.startswith('linux'):
        # Cannot validate on non-Linux; trust the PID file on CI
        return None

    cmdline_path = f'/proc/{pid}/cmdline'
    try:
        with open(cmdline_path, 'rb') as f:
            # Arguments are NUL-separated in /proc/PID/cmdline
            cmdline = f.read().replace(b'\x00', b' ').decode(errors='replace')
    except FileNotFoundError:
        return f'PID {pid} no longer exists (process already dead or stale PID file)'
    except OSError as e:
        return f'Cannot read cmdline for PID {pid}: {e}'

    # Must contain the agent module name
    if 'telemetry_agent' not in cmdline:
        return (
            f'PID {pid} cmdline does not contain "telemetry_agent" '
            f'(got: {cmdline[:120]!r}); refusing to kill unrelated process'
        )

    # Must contain the target host name so we don't kill another host's agent
    if host not in cmdline:
        return (
            f'PID {pid} cmdline does not contain host {host!r} '
            f'(got: {cmdline[:120]!r}); refusing to kill wrong agent'
        )

    # Defect 4: validate start time to prevent PID reuse
    if expected_starttime is not None:
        current_starttime = _get_proc_starttime(pid)
        if current_starttime is None:
            return f'Cannot read start time for PID {pid}; refusing to kill (PID reuse protection)'
        if current_starttime != expected_starttime:
            return (
                f'PID {pid} start time mismatch: expected {expected_starttime}, '
                f'got {current_starttime}; this is a different process (PID reuse)'
            )

    return None  # validation passed


def _kill_agent_for_host(host: str) -> tuple[bool, str]:
    """Terminate ONLY the telemetry agent belonging to `host`.

    Steps:
    1. Read and parse /tmp/{host}_agent.pid (supports PID:STARTTIME format).
    2. Validate the PID against /proc/{pid}/cmdline and start time (Defect 4).
    3. Send SIGTERM; wait up to 3 s; send SIGKILL if still alive.
    4. Remove the PID file on confirmed termination.
    5. Verify via /proc/{pid} that the process is gone before returning True (Defect 4).

    Returns (success: bool, message: str).
    """
    pid, expected_starttime, err = _read_agent_pid(host)
    if pid is None:
        return False, err

    err = _validate_agent_pid(pid, host, expected_starttime=expected_starttime)
    if err is not None:
        return False, err

    # Terminate with SIGTERM first (graceful), then SIGKILL
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        # Agent already dead — still a "success" for the kill experiment
        _remove_agent_pid_file(host)
        return True, f'Agent for {host} (PID {pid}) was already dead'
    except PermissionError as e:
        return False, f'Permission denied terminating PID {pid}: {e}'
    except OSError as e:
        return False, f'OS error sending SIGTERM to PID {pid}: {e}'

    # Wait up to 3 s for graceful shutdown
    deadline = time.time() + 3.0
    while time.time() < deadline:
        try:
            os.kill(pid, 0)   # check if process still exists
        except ProcessLookupError:
            break             # process exited
        except OSError:
            break
        time.sleep(0.1)
    else:
        # Still running — send SIGKILL
        try:
            os.kill(pid, _SIGKILL)
        except (ProcessLookupError, OSError):
            pass  # Already gone

    # Defect 4: verify process is gone before returning True
    still_alive = False
    try:
        os.kill(pid, 0)
        still_alive = True
    except (ProcessLookupError, OSError):
        pass

    if still_alive and not _is_zombie(pid):
        return False, f'Agent PID {pid} did not terminate after SIGKILL'

    _remove_agent_pid_file(host)
    return True, f'Agent for {host} (PID {pid}) terminated'


def _remove_agent_pid_file(host: str) -> None:
    pid_path = _AGENT_PID_FILE.format(host=host)
    try:
        os.unlink(pid_path)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _is_pid_alive(pid: int | None) -> bool:
    """Return True if pid is an active running process."""
    if pid is None:
        return False
    try:
        pid_int = int(pid)
        if pid_int <= 0:
            return False
        if sys.platform == 'win32':
            # On Windows, os.kill(pid, 0) doesn't fail on dead processes if handle is open;
            # use kernel32 WaitForSingleObject with 0ms timeout (258=WAIT_TIMEOUT -> still alive).
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(0x1000 | 0x00100000, False, pid_int)
                if not handle:
                    return False
                try:
                    res = kernel32.WaitForSingleObject(handle, 0)
                    return res == 258
                finally:
                    kernel32.CloseHandle(handle)
            except Exception:
                os.kill(pid_int, 0)
                return True
        else:
            os.kill(pid_int, 0)
            return True
    except (ValueError, TypeError, ProcessLookupError, OSError):
        return False


def _get_proc_children(pid: int) -> list[int]:
    """Recursively discover descendant PIDs from /proc on Linux.

    Uses /proc/{pid}/task/*/children globbing (or falls back to /proc/{pid}/children)
    to discover direct children, then recurses.
    Returns a list of all descendant PIDs (not including the parent).
    Does nothing on non-Linux platforms.
    """
    if not sys.platform.startswith('linux'):
        return []
    import glob
    all_children: list[int] = []
    try:
        # /proc/{pid}/task/*/children lists direct children for each thread
        child_pids: set[int] = set()
        pattern = f'/proc/{pid}/task/*/children'
        for path in glob.glob(pattern):
            try:
                with open(path, 'r') as f:
                    for token in f.read().split():
                        try:
                            child_pids.add(int(token))
                        except ValueError:
                            pass
            except (OSError, ValueError):
                pass
        for cpid in child_pids:
            all_children.append(cpid)
            all_children.extend(_get_proc_children(cpid))  # recurse
    except Exception:
        pass
    return all_children


def _is_zombie(pid: int) -> bool:
    """Return True if the process is in zombie (Z) state."""
    try:
        with open(f'/proc/{pid}/status', 'r') as f:
            for line in f:
                if line.startswith('State:'):
                    return 'Z' in line
    except (OSError, FileNotFoundError):
        pass
    return False


def _safe_reap_zombie(pid: int) -> bool:
    """Attempt to reap a zombie process using waitpid.

    Returns True ONLY if waitpid successfully reaped the process.
    Returns False if waitpid failed (e.g. ChildProcessError for non-child)
    or the process was not reaped. Never claims a non-child was reaped.
    """
    try:
        wpid, _ = os.waitpid(pid, os.WNOHANG if hasattr(os, 'WNOHANG') else 0)
        return wpid == pid
    except (ChildProcessError, OSError):
        return False


def _terminate_process_tree(pid: int, graceful_timeout: float = 2.0) -> None:
    """Gracefully terminate a process (SIGTERM) then escalate to SIGKILL.

    Also terminates descendant child worker processes discovered via /proc task children.
    After SIGKILL, verifies all descendants and parent are dead via os.kill(p, 0).
    Handles zombie state (reaps via waitpid without false claims on non-children).
    Does NOT use pkill -f or any broad pattern-based kill.
    """
    if not _is_pid_alive(pid):
        return

    # Recursively discover child processes (Defect 3)
    children = _get_proc_children(pid) if sys.platform.startswith('linux') else []

    # Send SIGTERM to children first, then parent
    for cpid in children:
        try:
            os.kill(cpid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return

    deadline = time.time() + graceful_timeout
    while time.time() < deadline:
        # Check parent
        parent_dead = not _is_pid_alive(pid)
        if not parent_dead and _is_zombie(pid):
            reaped = _safe_reap_zombie(pid)
            parent_dead = not _is_pid_alive(pid) or reaped

        # Check children
        all_children_dead = True
        for cpid in children:
            if _is_pid_alive(cpid):
                if _is_zombie(cpid):
                    _safe_reap_zombie(cpid)
                if _is_pid_alive(cpid) and not _is_zombie(cpid):
                    all_children_dead = False

        if parent_dead and all_children_dead:
            break
        time.sleep(0.1)

    # Re-scan for any newly spawned children
    if sys.platform.startswith('linux'):
        new_children = _get_proc_children(pid)
        for nc in new_children:
            if nc not in children:
                children.append(nc)

    # Force kill (SIGKILL) any stubborn children
    for cpid in children:
        if _is_pid_alive(cpid) and not _is_zombie(cpid):
            try:
                os.kill(cpid, _SIGKILL)
            except (ProcessLookupError, OSError):
                pass

    # Escalate to SIGKILL for the parent if still alive (Defect 3)
    if _is_pid_alive(pid) and not _is_zombie(pid):
        try:
            os.kill(pid, _SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    # Verification phase: confirm every descendant and parent is dead or reaped
    time.sleep(0.1)
    all_targets = [pid] + children
    for target in all_targets:
        if _is_pid_alive(target):
            if _is_zombie(target):
                _safe_reap_zombie(target)
            # Final check
            try:
                os.kill(target, 0)
                # If still alive, attempt final SIGKILL
                if not _is_zombie(target):
                    try:
                        os.kill(target, _SIGKILL)
                    except (ProcessLookupError, OSError):
                        pass
            except (ProcessLookupError, OSError):
                pass  # confirmed dead


def _verify_no_process(h, pattern: str) -> bool:
    """Return True if no process matching `pattern` exists in host's namespace."""
    try:
        result = h.cmd(f'pgrep -f {pattern}').strip()
        return result == '' or result == '0'
    except Exception:
        return False


def _verify_agent_running(host: str) -> tuple[bool, str]:
    """Check that a valid agent PID file exists and the process is alive.

    Returns (running: bool, message: str).
    """
    pid, expected_starttime, err = _read_agent_pid(host)
    if pid is None:
        return False, err
    err = _validate_agent_pid(pid, host, expected_starttime=expected_starttime)
    if err is not None:
        return False, err
    try:
        os.kill(pid, 0)
        return True, f'Agent for {host} running as PID {pid}'
    except ProcessLookupError:
        return False, f'PID {pid} in PID file no longer exists'
    except OSError:
        return False, f'PID {pid} check failed'


# ---------------------------------------------------------------------------
# Baseline measurement & Real Multi-Metric Recovery Verification
# ---------------------------------------------------------------------------

def _collect_baseline(host: str, cmd: str) -> dict:
    """Collect actual baseline measurements before launching an experiment.

    Captures:
    1. Bottleneck link utilization (from sdn_metrics.json).
    2. Telemetry state, loss, and timestamps for all hosts (from server_state.json).
    3. Active mitigation drop rules (from sdn_flows.json).

    Validates that:
    - Files exist and contain valid JSON objects.
    - Timestamps are present, numeric, not in the future, and not stale.
    - All 4 hosts (h1-h4) are present with valid numeric loss, cpu, mem, last_seen and healthy status.
    If any measurement is missing, incomplete, stale, or invalid, sets data_missing=True
    and records baseline_error describing the failure reason.
    """
    now = time.time()
    util = None
    mit_active = None
    metrics_missing = False
    server_state_missing = False
    flows_missing = False
    metrics_snapshot_timestamp = None
    server_state_snapshot_timestamp = None
    flows_snapshot_timestamp = None
    baseline_errors = []

    # 1. Read & validate sdn_metrics.json
    metrics_file = os.path.join(_TMPDIR, 'sdn_metrics.json')
    try:
        with open(metrics_file, 'r') as f:
            m = json.load(f)
        if not isinstance(m, dict) or not m:
            metrics_missing = True
            baseline_errors.append("sdn_metrics.json is empty or not a JSON object")
        else:
            raw_ts = m.get('snapshot_timestamp')
            if raw_ts is None or not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
                metrics_missing = True
                baseline_errors.append("sdn_metrics.json missing or invalid snapshot_timestamp")
            elif raw_ts > now + 1.0:
                metrics_missing = True
                baseline_errors.append(f"sdn_metrics.json timestamp in future ({raw_ts} > {now})")
            elif now - raw_ts > _STALE_SNAPSHOT_AGE:
                metrics_missing = True
                baseline_errors.append(f"sdn_metrics.json timestamp stale ({now - raw_ts:.1f}s)")
            else:
                metrics_snapshot_timestamp = float(raw_ts)

            # Strict utilization validation: numeric, finite, not bool, 0.0 <= util <= 2.0
            raw_util = m.get('utilization')
            if raw_util is None or not isinstance(raw_util, (int, float)) or isinstance(raw_util, bool):
                metrics_missing = True
                baseline_errors.append(f"sdn_metrics.json missing or non-numeric utilization: {raw_util!r}")
            elif not math.isfinite(raw_util) or not (0.0 <= float(raw_util) <= 2.0):
                metrics_missing = True
                baseline_errors.append(f"sdn_metrics.json non-finite or out-of-range utilization: {raw_util}")
            else:
                util = float(raw_util)

            # Strict mitigation_active validation: must exist and be bool
            raw_mit = m.get('mitigation_active')
            if raw_mit is None or not isinstance(raw_mit, bool):
                metrics_missing = True
                baseline_errors.append(f"sdn_metrics.json missing or non-boolean mitigation_active: {raw_mit!r}")
            else:
                mit_active = raw_mit
    except (OSError, ValueError) as e:
        metrics_missing = True
        baseline_errors.append(f"cannot read sdn_metrics.json: {e}")

    # 2. Read & validate server_state.json
    server_state_file = os.path.join(_TMPDIR, 'server_state.json')
    host_baselines = {}
    try:
        with open(server_state_file, 'r') as f:
            server_state = json.load(f)
        if not isinstance(server_state, dict) or not server_state:
            server_state_missing = True
            baseline_errors.append("server_state.json is empty or not a dict")
        else:
            raw_ts = server_state.get('snapshot_timestamp')
            if raw_ts is None or not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
                server_state_missing = True
                baseline_errors.append("server_state.json missing or invalid snapshot_timestamp")
            elif raw_ts > now + 1.0:
                server_state_missing = True
                baseline_errors.append(f"server_state.json timestamp in future ({raw_ts} > {now})")
            elif now - raw_ts > _STALE_SNAPSHOT_AGE:
                server_state_missing = True
                baseline_errors.append(f"server_state.json timestamp stale ({now - raw_ts:.1f}s)")
            else:
                server_state_snapshot_timestamp = float(raw_ts)

            # Check all 4 hosts
            for h in ('h1', 'h2', 'h3', 'h4'):
                hdata = server_state.get(h)
                if not isinstance(hdata, dict):
                    server_state_missing = True
                    baseline_errors.append(f"missing host {h} in server_state.json")
                    continue
                raw_loss = hdata.get('loss')
                raw_cpu = hdata.get('cpu')
                raw_mem = hdata.get('mem')
                raw_seen = hdata.get('last_seen')
                status = hdata.get('status')
                if not (isinstance(raw_loss, (int, float)) and not isinstance(raw_loss, bool) and math.isfinite(raw_loss) and 0.0 <= raw_loss <= 1.0):
                    server_state_missing = True
                    baseline_errors.append(f"invalid loss for {h}: {raw_loss}")
                if not (isinstance(raw_cpu, (int, float)) and not isinstance(raw_cpu, bool) and math.isfinite(raw_cpu) and 0.0 <= raw_cpu <= 100.0):
                    server_state_missing = True
                    baseline_errors.append(f"invalid cpu for {h}: {raw_cpu}")
                if not (isinstance(raw_mem, (int, float)) and not isinstance(raw_mem, bool) and math.isfinite(raw_mem) and 0.0 <= raw_mem <= 100.0):
                    server_state_missing = True
                    baseline_errors.append(f"invalid mem for {h}: {raw_mem}")
                if not (isinstance(raw_seen, (int, float)) and not isinstance(raw_seen, bool) and math.isfinite(raw_seen) and 0 <= now - raw_seen <= 6.0 and raw_seen <= now + 1.0):
                    server_state_missing = True
                    baseline_errors.append(f"stale/invalid last_seen for {h}")
                # Baseline MUST be genuinely healthy: only OK or UP allowed! WARN and DOWN are rejected!
                if status not in ('OK', 'UP'):
                    server_state_missing = True
                    baseline_errors.append(f"status {status} for {h} (expected healthy OK/UP)")

                host_baselines[h] = {
                    'loss': float(raw_loss) if isinstance(raw_loss, (int, float)) and not isinstance(raw_loss, bool) and math.isfinite(raw_loss) else 0.0,
                    'cpu': float(raw_cpu) if isinstance(raw_cpu, (int, float)) and not isinstance(raw_cpu, bool) and math.isfinite(raw_cpu) else 0.0,
                    'mem': float(raw_mem) if isinstance(raw_mem, (int, float)) and not isinstance(raw_mem, bool) and math.isfinite(raw_mem) else 0.0,
                    'status': status if isinstance(status, str) else 'UNKNOWN',
                    'last_seen': float(raw_seen) if isinstance(raw_seen, (int, float)) and not isinstance(raw_seen, bool) and math.isfinite(raw_seen) else now,
                }
    except (OSError, ValueError) as e:
        server_state_missing = True
        baseline_errors.append(f"cannot read server_state.json: {e}")

    # 3. Read & validate sdn_flows.json
    flows_file = os.path.join(_TMPDIR, 'sdn_flows.json')
    drop_count = 0
    try:
        with open(flows_file, 'r') as f:
            fdata = json.load(f)
        if not isinstance(fdata, dict) or not fdata:
            flows_missing = True
            baseline_errors.append("sdn_flows.json is empty or not a JSON object")
        else:
            raw_ts = fdata.get('timestamp') or fdata.get('snapshot_timestamp')
            if raw_ts is None or not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
                flows_missing = True
                baseline_errors.append("sdn_flows.json missing or invalid timestamp")
            elif raw_ts > now + 1.0:
                flows_missing = True
                baseline_errors.append(f"sdn_flows.json timestamp in future ({raw_ts} > {now})")
            elif now - raw_ts > _STALE_SNAPSHOT_AGE:
                flows_missing = True
                baseline_errors.append(f"sdn_flows.json timestamp stale ({now - raw_ts:.1f}s)")
            else:
                flows_snapshot_timestamp = float(raw_ts)

            # Strict flows list validation
            raw_flows = fdata.get('flows')
            if raw_flows is None or not isinstance(raw_flows, list):
                flows_missing = True
                baseline_errors.append(f"sdn_flows.json missing or non-list flows: {type(raw_flows).__name__}")
            else:
                for idx, flow in enumerate(raw_flows):
                    if not isinstance(flow, dict):
                        flows_missing = True
                        baseline_errors.append(f"sdn_flows.json flow entry [{idx}] is not a dict: {type(flow).__name__}")
                        break
                    if flow.get('is_mitigation') or flow.get('priority') == 100:
                        drop_count += 1
    except (OSError, ValueError) as e:
        flows_missing = True
        baseline_errors.append(f"cannot read sdn_flows.json: {e}")

    is_data_missing = metrics_missing or server_state_missing or flows_missing

    return {
        'timestamp': now,
        'bottleneck_util': util if util is not None else 0.0,
        'mitigation_active': mit_active if mit_active is not None else False,
        'drop_rules_count': drop_count,
        'data_missing': is_data_missing,
        'metrics_missing': metrics_missing,
        'server_state_missing': server_state_missing,
        'flows_missing': flows_missing,
        'baseline_error': '; '.join(baseline_errors) if baseline_errors else None,
        'metrics_snapshot_timestamp': metrics_snapshot_timestamp,
        'server_state_snapshot_timestamp': server_state_snapshot_timestamp,
        'flows_snapshot_timestamp': flows_snapshot_timestamp,
        'hosts': host_baselines if len(host_baselines) == 4 and not server_state_missing else {
            h: {'loss': 0.0, 'cpu': 0.0, 'mem': 0.0, 'status': 'UNKNOWN', 'last_seen': 0.0}
            for h in ('h1', 'h2', 'h3', 'h4')
        },
    }


_STALE_SNAPSHOT_AGE = 10.0  # seconds: threshold for stale snapshot detection


def _evaluate_recovery_sample(
    baseline: dict,
    target_host: str,
    cmd: str,
    child_pid: int | None,
    last_server_state_ts: float | None = None,
    last_metrics_ts: float | None = None,
    last_flows_ts: float | None = None,
    tracked_pids: list[int] | None = None,
) -> tuple[bool, dict, list, float | None, float | None, float | None]:
    """Evaluate one snapshot of recovery health across all critical metrics.

    Returns (all_passed: bool, checks: dict, failed: list,
             new_server_state_ts: float|None, new_metrics_ts: float|None,
             new_flows_ts: float|None).

    Priority 5.2 corrections:
    - Enforces baseline validity (Defect 2): invalid baseline causes explicit FAIL.
    - Fails closed on empty/invalid server_state (Defect 1): requires all 4 hosts (h1-h4)
      with valid numeric loss, cpu, mem, last_seen, and healthy status.
    - Verifies entire process tree (Defect 3): verifies child_pid AND all tracked descendant PIDs.
    - Snapshot freshness (Defect 4): validates timestamps (rejects future and stale), returns
      independent timestamps for server_state, metrics, and flows.
    """
    now = time.time()
    checks = {}
    failed = []

    # 0. Baseline validity check (Defect 2)
    baseline_invalid = (
        not baseline
        or not isinstance(baseline, dict)
        or bool(baseline.get('data_missing'))
        or bool(baseline.get('metrics_missing'))
        or bool(baseline.get('server_state_missing'))
        or bool(baseline.get('flows_missing'))
    )
    if baseline_invalid:
        err = baseline.get('baseline_error', 'Baseline data missing or incomplete') if isinstance(baseline, dict) else 'Missing baseline dictionary'
        checks['baseline_valid'] = {
            'passed': False,
            'details': f"FAIL: Baseline measurements missing, incomplete, stale or invalid ({err}) — cannot establish healthy reference (UNKNOWN)",
            'data_missing': True,
        }
        failed.append('baseline_valid')
    else:
        checks['baseline_valid'] = {
            'passed': True,
            'details': "Baseline measurements valid and healthy",
        }

    # 1. Process termination check (Defect 2 & Defect 3)
    _PROCESS_CMDS = ('flood', 'burst', 'cpu', 'mem')
    if cmd in _PROCESS_CMDS and child_pid is None:
        checks['process_terminated'] = {
            'passed': False,
            'details': 'FAIL: child_pid is None for process experiment — cannot verify termination (data_missing)',
            'data_missing': True,
        }
        failed.append('process_terminated')
    elif child_pid is not None:
        pids_to_check = [child_pid]
        if tracked_pids:
            for tp in tracked_pids:
                if tp and tp not in pids_to_check:
                    pids_to_check.append(tp)
        if sys.platform.startswith('linux'):
            for cpid in _get_proc_children(child_pid):
                if cpid not in pids_to_check:
                    pids_to_check.append(cpid)

        alive_workers = []
        for p in pids_to_check:
            if _is_pid_alive(p):
                # Distinguish zombies from running processes (safe reaping without claiming non-child)
                if _is_zombie(p):
                    _safe_reap_zombie(p)
                if _is_pid_alive(p) and not _is_zombie(p):
                    alive_workers.append(p)

        is_dead = len(alive_workers) == 0
        checks['process_terminated'] = {
            'passed': is_dead,
            'details': (
                f"Experiment process tree (PIDs: {pids_to_check}) completely terminated"
                if is_dead else
                f"FAIL: Experiment processes/workers still running: {alive_workers}"
            ),
        }
        if not is_dead:
            failed.append('process_terminated')
    else:
        # cmd is 'kill' or unrecognised — no child process to supervise
        checks['process_terminated'] = {'passed': True, 'details': 'No child process to supervise'}

    # 2. Read & validate server_state.json (Defect 1: empty/missing/invalid fails closed)
    server_state_file = os.path.join(_TMPDIR, 'server_state.json')
    server_state = {}
    server_state_missing = False
    server_state_error = None
    server_state_ts = None
    try:
        with open(server_state_file, 'r') as f:
            server_state = json.load(f)
        if not isinstance(server_state, dict) or not server_state:
            server_state_missing = True
            server_state_error = "server_state.json is empty or not a dict"
        else:
            raw_ts = server_state.get('snapshot_timestamp')
            if raw_ts is None or not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
                server_state_missing = True
                server_state_error = "server_state.json missing or invalid snapshot_timestamp"
            elif raw_ts > now + 1.0:
                server_state_missing = True
                server_state_error = f"server_state.json snapshot_timestamp in future ({raw_ts} > {now})"
            elif now - raw_ts > _STALE_SNAPSHOT_AGE:
                server_state_missing = True
                server_state_error = f"server_state.json snapshot_timestamp is stale ({now - raw_ts:.1f}s > {_STALE_SNAPSHOT_AGE}s)"
            else:
                server_state_ts = float(raw_ts)
    except (OSError, ValueError) as e:
        server_state_missing = True
        server_state_error = f"cannot read server_state.json: {e}"

    # 3. Read & validate sdn_metrics.json (Defect 1 & Defect 4: strict metric validation, no defaults)
    metrics_file = os.path.join(_TMPDIR, 'sdn_metrics.json')
    metrics = {}
    metrics_missing = False
    metrics_error = None
    metrics_ts = None
    curr_util = None
    curr_mit_active = None
    try:
        with open(metrics_file, 'r') as f:
            metrics = json.load(f)
        if not isinstance(metrics, dict) or not metrics:
            metrics_missing = True
            metrics_error = "sdn_metrics.json is empty or not a dict"
        else:
            raw_ts = metrics.get('snapshot_timestamp')
            if raw_ts is None or not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
                metrics_missing = True
                metrics_error = "sdn_metrics.json missing or invalid snapshot_timestamp"
            elif raw_ts > now + 1.0:
                metrics_missing = True
                metrics_error = f"sdn_metrics.json snapshot_timestamp in future ({raw_ts} > {now})"
            elif now - raw_ts > _STALE_SNAPSHOT_AGE:
                metrics_missing = True
                metrics_error = f"sdn_metrics.json snapshot_timestamp is stale ({now - raw_ts:.1f}s > {_STALE_SNAPSHOT_AGE}s)"
            else:
                metrics_ts = float(raw_ts)

            # Strict utilization validation: numeric, finite, not bool, 0.0 <= util <= 2.0
            raw_util = metrics.get('utilization')
            if raw_util is None or not isinstance(raw_util, (int, float)) or isinstance(raw_util, bool):
                metrics_missing = True
                metrics_error = f"sdn_metrics.json missing or non-numeric utilization: {raw_util!r}"
            elif not math.isfinite(raw_util) or not (0.0 <= float(raw_util) <= 2.0):
                metrics_missing = True
                metrics_error = f"sdn_metrics.json non-finite or out-of-range utilization: {raw_util}"
            else:
                curr_util = float(raw_util)

            # Strict mitigation_active validation: must exist and be bool
            raw_mit = metrics.get('mitigation_active')
            if raw_mit is None or not isinstance(raw_mit, bool):
                metrics_missing = True
                metrics_error = f"sdn_metrics.json missing or non-boolean mitigation_active: {raw_mit!r}"
            else:
                curr_mit_active = raw_mit
    except (OSError, ValueError) as e:
        metrics_missing = True
        metrics_error = f"cannot read sdn_metrics.json: {e}"

    # 4. Read & validate sdn_flows.json (Defect 1, 4 & 7: strict flow validation, no defaults)
    flows_file = os.path.join(_TMPDIR, 'sdn_flows.json')
    drop_count = 0
    flows_missing = False
    flows_error = None
    flows_ts = None
    try:
        with open(flows_file, 'r') as f:
            fdata = json.load(f)
        if not isinstance(fdata, dict) or not fdata:
            flows_missing = True
            flows_error = "sdn_flows.json is empty or not a JSON object"
        else:
            raw_ts = fdata.get('timestamp') or fdata.get('snapshot_timestamp')
            if raw_ts is None or not isinstance(raw_ts, (int, float)) or isinstance(raw_ts, bool):
                flows_missing = True
                flows_error = "sdn_flows.json missing or invalid timestamp"
            elif raw_ts > now + 1.0:
                flows_missing = True
                flows_error = f"sdn_flows.json timestamp in future ({raw_ts} > {now})"
            elif now - raw_ts > _STALE_SNAPSHOT_AGE:
                flows_missing = True
                flows_error = f"sdn_flows.json timestamp is stale ({now - raw_ts:.1f}s > {_STALE_SNAPSHOT_AGE}s)"
            else:
                flows_ts = float(raw_ts)

            # Strict flows list validation
            raw_flows = fdata.get('flows')
            if raw_flows is None or not isinstance(raw_flows, list):
                flows_missing = True
                flows_error = f"sdn_flows.json missing or non-list flows: {type(raw_flows).__name__}"
            else:
                for idx, flow in enumerate(raw_flows):
                    if not isinstance(flow, dict):
                        flows_missing = True
                        flows_error = f"sdn_flows.json flow entry [{idx}] is not a dict: {type(flow).__name__}"
                        break
                    if flow.get('is_mitigation') or flow.get('priority') == 100:
                        drop_count += 1
    except (OSError, ValueError) as e:
        flows_missing = True
        flows_error = f"cannot read sdn_flows.json: {e}"

    # Validate individual host entries in server_state (Defect 1 & 2: strict validation, WARN rejected)
    EXPECTED_HOSTS = ('h1', 'h2', 'h3', 'h4')
    missing_hosts = []
    invalid_hosts = []
    stale_hosts = []
    down_hosts = []
    warn_hosts = []
    host_metrics = {}

    if not server_state_missing:
        for h in EXPECTED_HOSTS:
            hdata = server_state.get(h)
            if hdata is None or not isinstance(hdata, dict):
                missing_hosts.append(h)
                continue

            status = hdata.get('status')
            raw_seen = hdata.get('last_seen')
            raw_loss = hdata.get('loss')
            raw_cpu = hdata.get('cpu')
            raw_mem = hdata.get('mem')

            fields_valid = True
            if not isinstance(status, str) or status not in ('OK', 'WARN', 'UP', 'DOWN'):
                fields_valid = False
            if raw_seen is None or not isinstance(raw_seen, (int, float)) or isinstance(raw_seen, bool) or not math.isfinite(raw_seen):
                fields_valid = False
            if raw_loss is None or not isinstance(raw_loss, (int, float)) or isinstance(raw_loss, bool) or not math.isfinite(raw_loss) or not (0.0 <= raw_loss <= 1.0):
                fields_valid = False
            if raw_cpu is None or not isinstance(raw_cpu, (int, float)) or isinstance(raw_cpu, bool) or not math.isfinite(raw_cpu) or not (0.0 <= raw_cpu <= 100.0):
                fields_valid = False
            if raw_mem is None or not isinstance(raw_mem, (int, float)) or isinstance(raw_mem, bool) or not math.isfinite(raw_mem) or not (0.0 <= raw_mem <= 100.0):
                fields_valid = False

            if not fields_valid:
                invalid_hosts.append(h)
                continue

            last_seen = float(raw_seen)
            if last_seen > now + 1.0:
                invalid_hosts.append(f"{h}(future last_seen)")
                continue

            age = now - last_seen
            if age > 6.0:
                stale_hosts.append(f"{h}(age={age:.1f}s)")
            if status == 'DOWN':
                down_hosts.append(h)
            elif status == 'WARN':
                warn_hosts.append(h)

            host_metrics[h] = {
                'loss': float(raw_loss),
                'cpu': float(raw_cpu),
                'mem': float(raw_mem),
                'status': status,
                'last_seen': last_seen,
            }

    # Evaluate Host Telemetry Freshness check (Defect 2: WARN is treated as unrecovered)
    if server_state_missing or missing_hosts or invalid_hosts:
        reasons = []
        if server_state_error: reasons.append(server_state_error)
        if missing_hosts: reasons.append(f"missing hosts: {', '.join(missing_hosts)}")
        if invalid_hosts: reasons.append(f"invalid host metrics: {', '.join(invalid_hosts)}")
        checks['telemetry_fresh'] = {
            'passed': False,
            'details': f"FAIL: Host telemetry missing or invalid ({'; '.join(reasons)}) — cannot verify telemetry (UNKNOWN)",
            'data_missing': True,
        }
        failed.append('telemetry_fresh')
    elif stale_hosts or down_hosts or warn_hosts:
        reasons = []
        if stale_hosts: reasons.append(f"stale hosts: {', '.join(stale_hosts)}")
        if down_hosts: reasons.append(f"down hosts: {', '.join(down_hosts)}")
        if warn_hosts: reasons.append(f"warn hosts: {', '.join(warn_hosts)}")
        checks['telemetry_fresh'] = {
            'passed': False,
            'details': f"FAIL: Telemetry unrecovered ({'; '.join(reasons)})",
            'data_missing': False,
        }
        failed.append('telemetry_fresh')
    else:
        checks['telemetry_fresh'] = {
            'passed': True,
            'details': "All 4 hosts reporting fresh and valid telemetry (healthy OK/UP)",
        }

    # Evaluate Packet Loss check
    if server_state_missing or missing_hosts or invalid_hosts:
        checks['packet_loss_recovered'] = {
            'passed': False,
            'details': 'FAIL: Host telemetry missing or invalid — cannot verify packet loss (UNKNOWN)',
            'data_missing': True,
        }
        failed.append('packet_loss_recovered')
    else:
        high_loss_hosts = []
        for h in EXPECTED_HOSTS:
            loss = host_metrics[h]['loss']
            base_loss = float(baseline.get('hosts', {}).get(h, {}).get('loss', 0.0))
            if loss > max(0.05, base_loss + 0.05):
                high_loss_hosts.append(f"{h}(loss={loss*100:.1f}%)")

        loss_ok = len(high_loss_hosts) == 0
        checks['packet_loss_recovered'] = {
            'passed': loss_ok,
            'details': "UDP sequence loss within acceptable threshold" if loss_ok else f"High loss: {', '.join(high_loss_hosts)}",
        }
        if not loss_ok:
            failed.append('packet_loss_recovered')

    # Evaluate VM-wide CPU & Memory check (Defect 6: VM-wide metrics)
    if server_state_missing or missing_hosts or invalid_hosts:
        checks['vm_resources_baseline'] = {
            'passed': False,
            'details': 'FAIL: Host telemetry missing or invalid — cannot verify VM-wide CPU/memory (UNKNOWN)',
            'data_missing': True,
        }
        failed.append('vm_resources_baseline')
    else:
        curr_cpu = max(host_metrics[h]['cpu'] for h in EXPECTED_HOSTS)
        curr_mem = max(host_metrics[h]['mem'] for h in EXPECTED_HOSTS)
        base_cpu = max(
            (float(hdata.get('cpu', 0.0)) for hdata in baseline.get('hosts', {}).values()),
            default=0.0,
        )
        base_mem = max(
            (float(hdata.get('mem', 0.0)) for hdata in baseline.get('hosts', {}).values()),
            default=0.0,
        )
        resources_ok = (curr_cpu <= base_cpu + 25.0) and (curr_mem <= base_mem + 15.0)
        checks['vm_resources_baseline'] = {
            'passed': resources_ok,
            'details': f"VM-wide CPU/memory within baseline (CPU: {curr_cpu:.1f}%, Mem: {curr_mem:.1f}%)",
        }
        if not resources_ok:
            failed.append('vm_resources_baseline')

    # Evaluate Agent Restarted check (for cmd == 'kill')
    if cmd == 'kill':
        agent_running, verify_msg = _verify_agent_running(target_host)
        target_healthy = (
            not server_state_missing
            and target_host in host_metrics
            and target_host not in stale_hosts
            and target_host not in down_hosts
            and target_host not in warn_hosts
        )
        passed_kill = agent_running and target_healthy
        checks['agent_restarted'] = {
            'passed': passed_kill,
            'details': verify_msg if passed_kill else f"Agent restart verification failed: {verify_msg} (healthy={target_healthy})",
        }
        if not passed_kill:
            failed.append('agent_restarted')

    # Evaluate Bottleneck link utilization check (Defect 1: strict validation, no healthy defaults)
    if metrics_missing or curr_util is None:
        checks['bottleneck_util_recovered'] = {
            'passed': False,
            'details': f"FAIL: sdn_metrics.json missing or invalid ({metrics_error}) — cannot verify bottleneck utilization (UNKNOWN)",
            'data_missing': True,
        }
        failed.append('bottleneck_util_recovered')
    else:
        base_util = float(baseline.get('bottleneck_util', 0.0))
        util_ok = curr_util <= max(0.20, base_util + 0.15)
        checks['bottleneck_util_recovered'] = {
            'passed': util_ok,
            'details': f"Bottleneck utilization at {curr_util*100:.1f}% (baseline: {base_util*100:.1f}%)",
        }
        if not util_ok:
            failed.append('bottleneck_util_recovered')

    # Evaluate Mitigation drop rules cleared check (Defect 1 & Defect 7: strict validation, no healthy defaults)
    if metrics_missing or curr_mit_active is None:
        checks['mitigation_cleared'] = {
            'passed': False,
            'details': f"FAIL: sdn_metrics.json missing or invalid ({metrics_error}) — cannot verify mitigation state (UNKNOWN)",
            'data_missing': True,
        }
        failed.append('mitigation_cleared')
    elif flows_missing:
        checks['mitigation_cleared'] = {
            'passed': False,
            'details': f"FAIL: sdn_flows.json missing or invalid ({flows_error}) — cannot verify mitigation flow count (UNKNOWN)",
            'data_missing': True,
        }
        failed.append('mitigation_cleared')
    else:
        mit_cleared = (not curr_mit_active) and (drop_count == 0)
        checks['mitigation_cleared'] = {
            'passed': mit_cleared,
            'details': "Mitigation drop rules cleared" if mit_cleared else f"Mitigation active or {drop_count} drop rules remaining",
        }
        if not mit_cleared:
            failed.append('mitigation_cleared')

    all_passed = len(failed) == 0
    return all_passed, checks, failed, server_state_ts, metrics_ts, flows_ts

    all_passed = len(failed) == 0
    return all_passed, checks, failed, server_state_ts, metrics_ts, flows_ts


def _supervise_and_verify(net, base, host: str, cmd: str, duration: int, exp_id: str, child_pid: int | None, baseline: dict):
    """Background supervisor thread: enforces timeout, reaps processes, and verifies network recovery.

    Priority 5.2 corrections:
    - Enforces baseline validity: fails closed if baseline data is missing or invalid.
    - Tracks and reaps the entire process tree (descendants & worker processes).
    - Requires genuinely fresh server telemetry and SDN metrics for each recovery cycle.
    - Requires fresh flow statistics whenever mitigation verification is required.
    - Does NOT use an OR condition: all required sources must independently advance.
    - Duplicate or stale snapshots reset the consecutive-pass count to 0.
    """
    try:
        # Check baseline validity up front
        if not baseline or baseline.get('data_missing'):
            info(f"*** [SUPERVISOR] Experiment {exp_id} baseline is missing or invalid: {baseline.get('baseline_error', 'data_missing')}. Recovery will fail closed.\n")

        # Discover initial child worker PIDs if running
        tracked_worker_pids = set()
        if cmd in ('flood', 'burst', 'cpu', 'mem') and child_pid is not None:
            tracked_worker_pids.add(child_pid)
            if sys.platform.startswith('linux'):
                tracked_worker_pids.update(_get_proc_children(child_pid))

        # Sleep for expected experiment duration
        time.sleep(duration)

        if not _experiment_tracker.is_current(host, cmd, exp_id):
            return  # superseded

        # For process-based experiments: verify exit and force terminate if hanging
        if cmd in ('flood', 'burst', 'cpu', 'mem') and child_pid is not None:
            time.sleep(1.5)  # brief grace period for self-exit
            if sys.platform.startswith('linux'):
                tracked_worker_pids.update(_get_proc_children(child_pid))

            workers_alive = [p for p in tracked_worker_pids if _is_pid_alive(p) and not _is_zombie(p)]
            if workers_alive or _is_pid_alive(child_pid):
                info(f'*** [SUPERVISOR] Process tree {child_pid} (workers: {workers_alive}) did not exit in {duration}s; terminating.\n')
                _terminate_process_tree(child_pid, graceful_timeout=2.0)
                if sys.platform.startswith('linux'):
                    tracked_worker_pids.update(_get_proc_children(child_pid))

        elif cmd == 'kill':
            # Relaunch agent
            h = net.get(host)
            h.cmd(
                f'python3 {base}/agents/telemetry_agent.py {host} 10.0.0.5 '
                f'> /tmp/{host}_agent.log 2>&1 &'
            )
            # Wait up to 5s for agent PID file
            pid_path = _AGENT_PID_FILE.format(host=host)
            deadline = time.time() + 5.0
            while time.time() < deadline:
                if os.path.exists(pid_path):
                    break
                time.sleep(0.2)

        # Transition to RECOVERING state
        _experiment_tracker.set_state(host, cmd, exp_id, 'RECOVERING')

        # Recovery verification loop (timeout: 15.0s, requires 2 consecutive passing cycles
        # each with independently advancing fresh snapshot timestamps).
        deadline = time.time() + 15.0
        consecutive_passes = 0
        last_checks = {}
        last_failed = []
        last_server_state_ts: float | None = None
        last_metrics_ts: float | None = None
        last_flows_ts: float | None = None

        while time.time() < deadline:
            if not _experiment_tracker.is_current(host, cmd, exp_id):
                return

            sample_res = _evaluate_recovery_sample(
                baseline, host, cmd, child_pid,
                last_server_state_ts=last_server_state_ts,
                last_metrics_ts=last_metrics_ts,
                last_flows_ts=last_flows_ts,
                tracked_pids=list(tracked_worker_pids),
            )
            passed = sample_res[0]
            last_checks = sample_res[1]
            last_failed = sample_res[2]
            new_ss_ts = sample_res[3] if len(sample_res) > 3 else None
            new_m_ts = sample_res[4] if len(sample_res) > 4 else None
            new_flows_ts = sample_res[5] if len(sample_res) > 5 else None

            # Priority 5.2 Requirement 4: Snapshot freshness logic
            # Require genuinely fresh server telemetry and SDN metrics for each recovery cycle.
            # Require fresh flow statistics whenever mitigation verification is required.
            # Do NOT use an OR condition to allow one advancing timestamp to substitute for another.
            ss_advanced = (new_ss_ts is not None and (last_server_state_ts is None or new_ss_ts > last_server_state_ts))
            m_advanced = (new_m_ts is not None and (last_metrics_ts is None or new_m_ts > last_metrics_ts))

            # Flow statistics freshness required for traffic experiments (flood/burst) or when flows ts present
            flows_required = cmd in ('flood', 'burst') or new_flows_ts is not None
            flows_advanced = (new_flows_ts is not None and (last_flows_ts is None or new_flows_ts > last_flows_ts)) if flows_required else True

            # All required sources must independently advance
            snapshots_advanced = ss_advanced and m_advanced and flows_advanced

            if passed and snapshots_advanced:
                consecutive_passes += 1
                last_server_state_ts = new_ss_ts
                last_metrics_ts = new_m_ts
                if new_flows_ts is not None:
                    last_flows_ts = new_flows_ts
                if consecutive_passes >= 2:
                    break
            else:
                # Priority 5.2 Requirement 4: Duplicate, stale, non-advancing, or failing
                # snapshots MUST reset the consecutive-pass count!
                consecutive_passes = 0
                if passed and not snapshots_advanced:
                    info(f'*** [SUPERVISOR] Duplicate/stale snapshot detected (ss_adv={ss_advanced}, m_adv={m_advanced}, f_adv={flows_advanced}) — reset consecutive passes to 0.\n')

            time.sleep(3.5)

        if consecutive_passes >= 2:
            final_state = 'RECOVERED'
            info(f'*** [EXPERIMENT] {cmd} on {host} successfully RECOVERED.\n')
        elif last_failed:
            final_state = 'RECOVERY_FAILED'
            info(f'*** [EXPERIMENT] {cmd} on {host} RECOVERY_FAILED: {", ".join(last_failed)}.\n')
        else:
            # Checks were not failing, but two consecutive fresh passing samples
            # were not observed before the 15 s deadline (e.g. stale snapshots).
            final_state = 'RECOVERY_TIMEOUT'
            last_failed = ['recovery_not_confirmed_before_deadline']
            info(f'*** [EXPERIMENT] {cmd} on {host} RECOVERY_TIMEOUT: recovery not confirmed within 15s.\n')

        _experiment_tracker.complete(host, cmd, exp_id, final_state, last_checks, last_failed)

    except Exception as exc:
        info(f'*** [SUPERVISOR ERROR] Experiment {exp_id} exception: {exc}\n')
        _experiment_tracker.complete(host, cmd, exp_id, 'RECOVERY_FAILED', {}, ['supervisor_exception'], error=str(exc))


def _handle_command(net, base, req):
    """Execute a validated experiment command inside the running Mininet topology.

    All host.cmd() calls that start background work append '&' so the socket
    handler returns immediately.  The topology and CLI remain fully functional.

    Enforces single active experiment concurrency, tracks child process PIDs,
    and supervises automatic multi-metric network recovery.
    """
    cmd_raw = str(req.get('cmd', '')).strip().lower()
    if cmd_raw == 'status':
        return {'experiments': _experiment_tracker.get_status()}

    host, cmd, rate, duration = _validate(req)
    h = net.get(host)

    # Priority 3: Global concurrency check — reject if an experiment is RUNNING or RECOVERING
    if _experiment_tracker.has_active_global():
        active = _experiment_tracker.get_active_global()
        raise ValueError(
            f"Active experiment already running: {active['cmd']} on {active['host']} "
            f"(ID: {active['exp_id'][:8]}, State: {active['state']}). "
            "Wait for recovery before starting a new experiment."
        )

    # Priority 4: Collect pre-experiment baseline measurements
    baseline = _collect_baseline(host, cmd)

    # Register experiment
    exp_id = _experiment_tracker.start(
        host, cmd, duration, rate=rate, baseline=baseline, allow_supersede=False
    )

    child_pid = None
    exp_pid_file = os.path.join(_TMPDIR, f"exp_{exp_id[:8]}.pid")
    try:
        if os.path.exists(exp_pid_file):
            os.unlink(exp_pid_file)
    except OSError:
        pass

    if cmd == 'flood':
        h.cmd(
            f'python3 {base}/tests/congestion_test.py {rate} {duration} '
            f'> /tmp/{host}_flood.log 2>&1 & echo $! > {exp_pid_file}'
        )
    elif cmd == 'burst':
        duration = 6
        # 3 Mbps for 6 s: well above the 60 s EMA (>3x) and spans >=2 of the
        # controller's 3 s polls so it cannot be averaged away, but stays below
        # the 80% (4 Mbps) congestion threshold so it is a burst, not a flood.
        h.cmd(
            f'python3 {base}/tests/congestion_test.py 3 6 '
            f'> /tmp/{host}_burst.log 2>&1 & echo $! > {exp_pid_file}'
        )
    elif cmd == 'cpu':
        h.cmd(
            f'python3 {base}/tests/cpu_load_test.py {duration} '
            f'> /tmp/{host}_cpu.log 2>&1 & echo $! > {exp_pid_file}'
        )
    elif cmd == 'mem':
        h.cmd(
            f'python3 {base}/tests/memory_load_test.py {duration} '
            f'> /tmp/{host}_mem.log 2>&1 & echo $! > {exp_pid_file}'
        )
    elif cmd == 'kill':
        success, kill_msg = _kill_agent_for_host(host)
        if not success:
            _experiment_tracker.complete(host, cmd, exp_id, 'RECOVERY_FAILED', {}, ['kill_initial_failed'], error=kill_msg)
            raise ValueError(f'kill failed for {host}: {kill_msg}')
        info(f'*** [experiment] kill: {kill_msg}\n')

    # Capture child process PID for process-based experiments
    if cmd in ('flood', 'burst', 'cpu', 'mem'):
        deadline = time.time() + 1.5
        while time.time() < deadline:
            if os.path.exists(exp_pid_file):
                try:
                    with open(exp_pid_file) as f:
                        raw_cpid = f.read().strip()
                    if raw_cpid:
                        child_pid = int(raw_cpid)
                    os.unlink(exp_pid_file)
                    break
                except (ValueError, OSError):
                    pass
            time.sleep(0.05)
        _experiment_tracker.set_child_pid(host, cmd, exp_id, child_pid)

    # Launch supervisor thread
    threading.Thread(
        target=_supervise_and_verify,
        args=(net, base, host, cmd, duration, exp_id, child_pid, baseline),
        daemon=True
    ).start()

    if cmd == 'flood':
        return f'flood started: {rate} Mbps for {duration}s on {host} [exp={exp_id[:8]}]'
    elif cmd == 'burst':
        return f'burst started: 3 Mbps for 6s on {host} [exp={exp_id[:8]}]'
    elif cmd == 'cpu':
        return f'cpu stress started for {duration}s on {host} [exp={exp_id[:8]}]'
    elif cmd == 'mem':
        return f'mem stress started: auto-sized to ~90% RAM for {duration}s on {host} [exp={exp_id[:8]}]'
    elif cmd == 'kill':
        return (
            f'agent for {host} (PID from PID file) terminated; '
            f'restart scheduled in {duration}s [exp={exp_id[:8]}]'
        )

    raise ValueError(f'unhandled cmd {cmd!r}')


def _control_server(net, base, stop_event=None, ready_event=None):
    """Unix-domain socket listener with root:sdn-ipc ownership, 0660 permissions,
    stale socket safety checks, and kernel SO_PEERCRED credential validation.
    Runs in a daemon thread alongside CLI.
    """
    stop_event = stop_event or threading.Event()
    ready_event = ready_event or threading.Event()

    # 1. Resolve authorized group ID (explicit failure if missing)
    target_gid = _get_target_gid(IPC_GROUP_NAME)

    # 2. Inspect path and safely remove stale socket (refuses active socket removal)
    _prepare_socket_path(CONTROL_SOCK)

    af_unix = getattr(_socket, 'AF_UNIX', None)
    if af_unix is None:
        raise RuntimeError("AF_UNIX domain sockets are not supported on this platform.")

    srv = _socket.socket(af_unix, _socket.SOCK_STREAM)
    server_identity = None
    try:
        srv.bind(CONTROL_SOCK)

        # 3. Set ownership to root:target_gid and permissions to 0660
        try:
            if hasattr(os, 'chown'):
                os.chown(CONTROL_SOCK, 0, target_gid)
            os.chmod(CONTROL_SOCK, 0o660)
        except OSError as e:
            raise RuntimeError(
                f"Failed to set permissions (0660) or ownership (root:{IPC_GROUP_NAME}) on {CONTROL_SOCK}: {e}"
            )

        # Record both device and inode. Fail safely if identity recording fails.
        try:
            st = os.lstat(CONTROL_SOCK)
            server_identity = (st.st_dev, st.st_ino)
        except OSError as e:
            raise RuntimeError(
                f"Failed to record socket identity (device, inode) on {CONTROL_SOCK}: {e}"
            )

        srv.listen(4)
        srv.settimeout(1.0)             # allows the thread to exit cleanly
        info(f'*** Control socket listening on {CONTROL_SOCK} (owner=root:{IPC_GROUP_NAME}, mode=0660)\n')
        ready_event.set()

        while not stop_event.is_set():
            try:
                conn, _ = srv.accept()
            except _socket.timeout:
                continue

            # 4. Kernel peer credential validation (SO_PEERCRED on Linux)
            if hasattr(_socket, 'SO_PEERCRED') or sys.platform.startswith('linux'):
                try:
                    import struct
                    creds = conn.getsockopt(_socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize('3i'))
                    peer_pid, peer_uid, peer_gid = struct.unpack('3i', creds)
                    if not _is_peer_authorized(peer_pid, peer_uid, peer_gid, target_gid):
                        info(
                            f'*** [SECURITY] Rejected unauthorized peer PID={peer_pid}, '
                            f'UID={peer_uid}, GID={peer_gid} (not in group {IPC_GROUP_NAME})\n'
                        )
                        conn.close()
                        continue
                except Exception as cred_err:
                    info(f'*** [SECURITY] Failed to verify peer credentials: {cred_err}\n')
                    conn.close()
                    continue

            conn.settimeout(3.0)
            resp = json.dumps({'status': 'error', 'message': 'request timed out'})
            try:
                raw = b''
                while not raw.endswith(b'\n'):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    raw += chunk

                req  = json.loads(raw.decode())
                msg  = _handle_command(net, base, req)
                if isinstance(msg, dict):
                    resp_dict = {'status': 'ok'}
                    resp_dict.update(msg)
                    resp = json.dumps(resp_dict)
                else:
                    resp = json.dumps({'status': 'ok', 'message': msg})
            except (json.JSONDecodeError, ValueError) as exc:
                resp = json.dumps({'status': 'error', 'message': str(exc)})
            except _socket.timeout:
                resp = json.dumps({'status': 'error', 'message': 'request timed out'})
            except Exception as exc:
                resp = json.dumps({'status': 'error', 'message': f'internal: {exc}'})
            finally:
                try:
                    conn.sendall((resp + '\n').encode())
                except Exception:
                    pass
                conn.close()

    finally:
        srv.close()
        # 5. Safe cleanup: remove ONLY the socket created by this current server instance
        if server_identity is not None:
            try:
                st_now = os.lstat(CONTROL_SOCK)
                if (st_now.st_dev, st_now.st_ino) == server_identity:
                    os.unlink(CONTROL_SOCK)
                else:
                    info(
                        f'*** [SECURITY] Socket identity at {CONTROL_SOCK} changed '
                        f'(expected {server_identity}, found {(st_now.st_dev, st_now.st_ino)}); refusing to unlink.\n'
                    )
            except FileNotFoundError:
                pass
            except OSError as cleanup_err:
                info(f'*** [CLEANUP] Failed to remove control socket {CONTROL_SOCK}: {cleanup_err}\n')

class MonitoringTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        for i in range(1, 5):
            h = self.addHost(f'h{i}', ip=f'10.0.0.{i}/24')
            self.addLink(h, s1, bw=100, delay='1ms')

        h5 = self.addHost('h5', ip='10.0.0.5/24')
        h6 = self.addHost('h6', ip='10.0.0.6/24')

        self.addLink(s1, s2, bw=5, delay='5ms', max_queue_size=100)
        self.addLink(h5, s2, bw=100, delay='1ms')
        self.addLink(h6, s2, bw=100, delay='1ms')

def run():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    topo = MonitoringTopo()
    net = Mininet(topo=topo, controller=RemoteController,
                  switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net_start_attempted = False
    stop_event = threading.Event()
    control_ready = threading.Event()
    control_errors = []
    ctrl_thread = None
    old_sigterm = None

    def handle_sigterm(_signum, _frame):
        raise KeyboardInterrupt

    try:
        try:
            old_sigterm = signal.signal(signal.SIGTERM, handle_sigterm)
        except (ValueError, AttributeError):
            pass

        _write_topology_pid_file('STARTING')
        net_start_attempted = True
        net.start()

        info('*** Starting monitoring server on h5\n')
        h5 = net.get('h5')
        h5.cmd(f'cd {base} && python3 server/monitoring_server.py > /tmp/server.log 2>&1 &')

        info('*** Starting telemetry agents on h1..h4\n')
        for i in range(1, 5):
            hname = f'h{i}'
            h = net.get(hname)
            h.cmd(
                f'cd {base} && python3 agents/telemetry_agent.py {hname} 10.0.0.5 '
                f'> /tmp/{hname}_agent.log 2>&1 &'
            )

        def serve_control():
            try:
                _control_server(net, base, stop_event, control_ready)
            except BaseException as exc:
                control_errors.append(exc)
                control_ready.set()

        ctrl_thread = threading.Thread(target=serve_control, name='netwatch-control', daemon=True)
        ctrl_thread.start()

        ready_deadline = time.monotonic() + 15.0
        while not control_ready.wait(0.2):
            if not ctrl_thread.is_alive():
                break
            if time.monotonic() >= ready_deadline:
                break
        if control_errors:
            raise RuntimeError(f'Control socket startup failed: {control_errors[0]}')
        if not control_ready.is_set() or not ctrl_thread.is_alive():
            raise RuntimeError('Control socket did not become ready')

        _write_topology_pid_file('READY')
        info('*** Topology ready.  Control socket: /tmp/sdn_control.sock\n')
        CLI(net)
    finally:
        stop_event.set()
        if ctrl_thread is not None:
            ctrl_thread.join(timeout=3.0)
        try:
            if net_start_attempted:
                net.stop()
        finally:
            _remove_topology_pid_file()
            if old_sigterm is not None:
                try:
                    signal.signal(signal.SIGTERM, old_sigterm)
                except (ValueError, AttributeError):
                    pass

if __name__ == '__main__':
    setLogLevel('info')
    try:
        run()
    except KeyboardInterrupt:
        pass
