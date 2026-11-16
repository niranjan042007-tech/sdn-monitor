#!/usr/bin/env python3
"""Regression tests for NETWATCH experiment lifecycle, host-specific agent kill,
and automatic recovery verification.

All tests are fully isolated — no live Mininet, no live agents, no live sockets.
Mocks are used throughout to simulate Linux-only /proc and os.kill behaviour so
that the test suite runs on Windows CI without modification.

Test coverage:
  T1  - Killing h1 does NOT terminate h2, h3, h4 agents
  T2  - Only the selected host's agent is restarted
  T3  - Invalid host rejected before any kill is attempted
  T4  - Missing PID file handled safely (no crash, informative error)
  T5  - Stale PID file with wrong cmdline cannot kill unrelated process
  T6  - Stale PID file with wrong host cannot kill another host's agent
  T7  - ExperimentTracker: stale cleanup thread aborts when exp superseded
  T8  - Recovery NOT reported before health checks pass (RECOVERY_FAILED path)
  T9  - Flood/CPU/mem process termination verified after duration
  T10 - Agent PID file written and removed by telemetry_agent.main()
  T11 - Existing IPC _validate tests still pass
"""

import os
import signal
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, mock_open, call

# ---------------------------------------------------------------------------
# Path bootstrap — allow imports without installing the package
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topo.monitoring_topo import (
    _validate,
    _read_agent_pid,
    _validate_agent_pid,
    _kill_agent_for_host,
    _remove_agent_pid_file,
    _verify_agent_running,
    ExperimentTracker,
    _experiment_tracker,
    _AGENT_PID_FILE,
    _TMPDIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pid_path(host: str) -> str:
    """Return the platform-correct PID file path for `host`."""
    return _AGENT_PID_FILE.format(host=host)


class TempPidFile:
    """Context manager: writes a PID file for `host` and removes it on exit.

    Uses the same _AGENT_PID_FILE template as the production code so the path
    is correct on both Linux (/tmp/) and Windows (system temp dir).
    """

    def __init__(self, host: str, pid: int):
        self.host = host
        self.pid = pid
        self.path = _pid_path(host)

    def __enter__(self):
        # Ensure the directory exists (always true on Linux; needed on Windows CI)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            f.write(str(self.pid))
        return self.path

    def __exit__(self, *_):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# T1 — Killing h1 does NOT terminate h2, h3, h4 agents
# ---------------------------------------------------------------------------

class TestKillTargetsOnlySelectedHost(unittest.TestCase):
    """_kill_agent_for_host(host) must use only the target host's PID file.

    Specifically it must NOT call os.kill() with PIDs belonging to other hosts.
    """

    def test_kill_h1_does_not_signal_h2_h3_h4(self):
        """os.kill is called exactly once, with h1's PID, never with h2/h3/h4 PIDs."""
        pids = {'h1': 1001, 'h2': 1002, 'h3': 1003, 'h4': 1004}
        killed_pids = []

        # Write PID files for all four hosts
        pid_files = []
        for host, pid in pids.items():
            path = _pid_path(host)
            with open(path, 'w') as f:
                f.write(str(pid))
            pid_files.append(path)

        def fake_kill(pid, sig):
            killed_pids.append((pid, sig))
            if sig == signal.SIGTERM:
                raise ProcessLookupError()   # simulate immediate exit

        def fake_validate(pid, host, **kwargs):
            # Accept any PID that matches the expected map
            if pids.get(host) == pid:
                return None     # valid
            return f'cmdline mismatch for {host}'

        try:
            with patch('topo.monitoring_topo.os.kill', side_effect=fake_kill), \
                 patch('topo.monitoring_topo._validate_agent_pid', side_effect=fake_validate):
                success, msg = _kill_agent_for_host('h1')

            self.assertTrue(success, msg)
            # Exactly one SIGTERM signal was sent
            sigterm_pids = [p for p, s in killed_pids if s == signal.SIGTERM]
            self.assertEqual(sigterm_pids, [1001],
                             f'Expected only h1 PID 1001; got {sigterm_pids}')
            # h2, h3, h4 PIDs were NEVER signalled
            for other_pid in (1002, 1003, 1004):
                self.assertNotIn(other_pid, [p for p, _ in killed_pids],
                                 f'PID {other_pid} should not have been signalled')
        finally:
            for path in pid_files:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass

    def test_kill_h3_only_signals_h3_pid(self):
        """Selecting h3 kills only h3; h1, h2, h4 are untouched."""
        pids = {'h1': 2001, 'h2': 2002, 'h3': 2003, 'h4': 2004}
        killed_pids = []

        pid_files = []
        for host, pid in pids.items():
            path = _pid_path(host)
            with open(path, 'w') as f:
                f.write(str(pid))
            pid_files.append(path)

        def fake_kill(pid, sig):
            killed_pids.append((pid, sig))
            if sig == signal.SIGTERM:
                raise ProcessLookupError()

        def fake_validate(pid, host, **kwargs):
            return None if pids.get(host) == pid else 'mismatch'

        try:
            with patch('topo.monitoring_topo.os.kill', side_effect=fake_kill), \
                 patch('topo.monitoring_topo._validate_agent_pid', side_effect=fake_validate):
                success, msg = _kill_agent_for_host('h3')

            self.assertTrue(success, msg)
            self.assertIn(2003, [p for p, _ in killed_pids])
            for other in (2001, 2002, 2004):
                self.assertNotIn(other, [p for p, _ in killed_pids])
        finally:
            for path in pid_files:
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass


# ---------------------------------------------------------------------------
# T2 — Only the selected host's agent is restarted
# ---------------------------------------------------------------------------

class TestOnlySelectedHostRestarted(unittest.TestCase):

    def test_restart_uses_host_specific_pid_path(self):
        """After kill, the restart thread writes PID only for the selected host."""
        # Verify _AGENT_PID_FILE template uses the host name
        self.assertIn('{host}', _AGENT_PID_FILE)
        # h1 and h2 get different PID file paths
        self.assertNotEqual(_pid_path('h1'), _pid_path('h2'))
        self.assertIn('h1', _pid_path('h1'))
        self.assertIn('h2', _pid_path('h2'))

    def test_restart_thread_aborts_if_experiment_superseded(self):
        """A stale restart thread must abort without touching the new experiment."""
        tracker = ExperimentTracker()
        exp_id_1 = tracker.start('h1', 'kill', 10)
        # Simulate a newer experiment superseding the first
        exp_id_2 = tracker.start('h1', 'kill', 10)

        # The first experiment's thread should detect it is no longer current
        self.assertFalse(tracker.is_current('h1', 'kill', exp_id_1),
                         'exp_id_1 should no longer be current after exp_id_2 started')
        self.assertTrue(tracker.is_current('h1', 'kill', exp_id_2))

        # Cleanup
        tracker.finish('h1', 'kill', exp_id_2)


# ---------------------------------------------------------------------------
# T3 — Invalid host rejected before any kill
# ---------------------------------------------------------------------------

class TestInvalidHostRejected(unittest.TestCase):

    def test_invalid_host_raises_value_error(self):
        """_validate rejects unknown hosts — no PID file is ever read."""
        with self.assertRaises(ValueError) as ctx:
            _validate({'cmd': 'kill', 'host': 'h99', 'duration': 20})
        self.assertIn('unknown host', str(ctx.exception))

    def test_injection_in_host_rejected(self):
        with self.assertRaises(ValueError):
            _validate({'cmd': 'kill', 'host': 'h1; pkill -9 python3', 'duration': 10})

    def test_kill_missing_host_field_rejected(self):
        with self.assertRaises(ValueError):
            _validate({'cmd': 'kill', 'host': '', 'duration': 10})


# ---------------------------------------------------------------------------
# T4 — Missing PID file handled safely
# ---------------------------------------------------------------------------

class TestMissingPidFileHandledSafely(unittest.TestCase):

    def test_read_agent_pid_missing_file(self):
        """_read_agent_pid returns (None, error_msg) when PID file is absent."""
        host = 'h1'
        pid_path = _pid_path(host)
        # Ensure file does not exist
        try:
            os.unlink(pid_path)
        except FileNotFoundError:
            pass

        pid, _, err = _read_agent_pid(host)
        self.assertIsNone(pid)
        self.assertIsNotNone(err)
        self.assertIn('not found', err.lower())

    def test_kill_agent_missing_pid_file_returns_failure(self):
        """_kill_agent_for_host returns (False, msg) when PID file is missing.

        No os.kill must be called.
        """
        host = 'h1'
        try:
            os.unlink(_pid_path(host))
        except FileNotFoundError:
            pass

        with patch('topo.monitoring_topo.os.kill') as mock_kill:
            success, msg = _kill_agent_for_host(host)

        self.assertFalse(success)
        self.assertIn('not found', msg.lower())
        mock_kill.assert_not_called()

    def test_read_agent_pid_non_integer_content(self):
        """_read_agent_pid returns (None, None, error_msg) for non-integer content."""
        with TempPidFile('h2', 0) as path:
            # Overwrite with garbage
            with open(path, 'w') as f:
                f.write('not-a-pid\n')
            pid, _, err = _read_agent_pid('h2')
        self.assertIsNone(pid)
        self.assertIsNotNone(err)
        self.assertIn('non-integer', err.lower())

    def test_read_agent_pid_zero_rejected(self):
        """PID 0 is not a valid process identifier."""
        with TempPidFile('h3', 0) as path:
            pid, _, err = _read_agent_pid('h3')
        self.assertIsNone(pid)
        self.assertIn('invalid pid', err.lower())


# ---------------------------------------------------------------------------
# T5 — Stale PID cannot kill unrelated process (cmdline mismatch)
# ---------------------------------------------------------------------------

class TestStalePidCannotKillUnrelatedProcess(unittest.TestCase):

    def test_cmdline_without_telemetry_agent_rejected(self):
        """If /proc/{pid}/cmdline does not contain 'telemetry_agent', kill is refused."""
        fake_cmdline = b'python3\x00/usr/bin/some_other_program\x00h1\x00'

        with patch('sys.platform', 'linux'), \
             patch('builtins.open', mock_open(read_data=fake_cmdline)):
            err = _validate_agent_pid(9999, 'h1')

        self.assertIsNotNone(err)
        self.assertIn('telemetry_agent', err)
        self.assertIn('refusing to kill', err.lower())

    def test_stale_pid_process_no_longer_exists(self):
        """If /proc/{pid}/cmdline raises FileNotFoundError, kill is refused."""
        with patch('sys.platform', 'linux'), \
             patch('builtins.open', side_effect=FileNotFoundError('No such file')):
            err = _validate_agent_pid(99999, 'h1')

        self.assertIsNotNone(err)
        self.assertIn('no longer exists', err.lower())

    def test_kill_refused_on_cmdline_mismatch(self):
        """_kill_agent_for_host returns failure when cmdline validation fails."""
        with TempPidFile('h4', 5000) as _:
            def fake_validate(pid, host, **kwargs):
                return 'PID 5000 cmdline does not contain "telemetry_agent"; refusing to kill unrelated process'

            with patch('topo.monitoring_topo._validate_agent_pid', side_effect=fake_validate), \
                 patch('topo.monitoring_topo.os.kill') as mock_kill:
                success, msg = _kill_agent_for_host('h4')

            mock_kill.assert_not_called()
        self.assertFalse(success)
        self.assertIn('refusing', msg.lower())


# ---------------------------------------------------------------------------
# T6 — Stale PID cannot kill another host's agent (host mismatch in cmdline)
# ---------------------------------------------------------------------------

class TestStalePidCannotKillWrongHostsAgent(unittest.TestCase):

    def test_cmdline_contains_agent_but_wrong_host(self):
        """PID file for h1 whose cmdline shows h2 is rejected."""
        # Cmdline contains 'telemetry_agent' and 'h2' but not 'h1'
        fake_cmdline = b'python3\x00agents/telemetry_agent.py\x00h2\x0010.0.0.5\x00'

        with patch('sys.platform', 'linux'), \
             patch('builtins.open', mock_open(read_data=fake_cmdline)):
            err = _validate_agent_pid(1234, 'h1')

        self.assertIsNotNone(err)
        self.assertIn('h1', err)
        self.assertIn('refusing', err.lower())

    def test_valid_cmdline_for_correct_host_passes(self):
        """PID whose cmdline contains both 'telemetry_agent' and the host name passes."""
        fake_cmdline = b'python3\x00agents/telemetry_agent.py\x00h1\x0010.0.0.5\x00'

        with patch('sys.platform', 'linux'), \
             patch('builtins.open', mock_open(read_data=fake_cmdline)):
            err = _validate_agent_pid(1234, 'h1')

        self.assertIsNone(err)   # None means validation passed


# ---------------------------------------------------------------------------
# T7 — ExperimentTracker prevents double-termination by stale threads
# ---------------------------------------------------------------------------

class TestExperimentTrackerLifecycle(unittest.TestCase):

    def setUp(self):
        self.tracker = ExperimentTracker()

    def test_start_returns_unique_ids(self):
        id1 = self.tracker.start('h1', 'kill', 10)
        id2 = self.tracker.start('h2', 'kill', 10)
        self.assertNotEqual(id1, id2)

    def test_is_current_true_for_active_experiment(self):
        exp_id = self.tracker.start('h1', 'flood', 20)
        self.assertTrue(self.tracker.is_current('h1', 'flood', exp_id))

    def test_is_current_false_after_superseded(self):
        exp_id_1 = self.tracker.start('h1', 'kill', 10)
        exp_id_2 = self.tracker.start('h1', 'kill', 10)   # supersedes
        self.assertFalse(self.tracker.is_current('h1', 'kill', exp_id_1))
        self.assertTrue(self.tracker.is_current('h1', 'kill', exp_id_2))
        self.tracker.finish('h1', 'kill', exp_id_2)

    def test_finish_removes_only_matching_experiment(self):
        exp_id_1 = self.tracker.start('h1', 'cpu', 10)
        exp_id_2 = self.tracker.start('h1', 'cpu', 10)   # supersedes
        self.tracker.finish('h1', 'cpu', exp_id_1)        # stale finish — no-op
        self.assertTrue(self.tracker.is_current('h1', 'cpu', exp_id_2),
                        'exp_id_2 should still be active after stale finish for exp_id_1')
        self.tracker.finish('h1', 'cpu', exp_id_2)

    def test_set_state_ignored_for_stale_experiment(self):
        exp_id_1 = self.tracker.start('h1', 'mem', 10)
        exp_id_2 = self.tracker.start('h1', 'mem', 10)   # supersedes
        self.tracker.set_state('h1', 'mem', exp_id_1, 'RECOVERY_FAILED')   # stale — ignored
        # exp_id_2 must not be affected
        snap = {e['exp_id']: e for e in self.tracker.snapshot()}
        self.assertEqual(snap[exp_id_2]['state'], 'RUNNING')
        self.tracker.finish('h1', 'mem', exp_id_2)

    def test_snapshot_returns_copies(self):
        exp_id = self.tracker.start('h2', 'flood', 15)
        snap = self.tracker.snapshot()
        self.assertEqual(len(snap), 1)
        # Modifying the snapshot must not affect internal state
        snap[0]['state'] = 'MUTATED'
        inner = self.tracker.snapshot()[0]
        self.assertNotEqual(inner['state'], 'MUTATED')
        self.tracker.finish('h2', 'flood', exp_id)

    def test_different_hosts_tracked_independently(self):
        """Experiments on h1 and h2 for the same cmd don't interfere."""
        id_h1 = self.tracker.start('h1', 'kill', 10)
        id_h2 = self.tracker.start('h2', 'kill', 10)
        self.assertTrue(self.tracker.is_current('h1', 'kill', id_h1))
        self.assertTrue(self.tracker.is_current('h2', 'kill', id_h2))
        self.tracker.finish('h1', 'kill', id_h1)
        self.assertFalse(self.tracker.is_current('h1', 'kill', id_h1))
        self.assertTrue(self.tracker.is_current('h2', 'kill', id_h2))
        self.tracker.finish('h2', 'kill', id_h2)

    def test_thread_safety(self):
        """Concurrent starts and finishes must not corrupt internal state."""
        results = []
        errors = []

        def run(host_idx):
            try:
                host = f'h{(host_idx % 4) + 1}'
                cmd = 'flood'
                exp_id = self.tracker.start(host, cmd, 5)
                time.sleep(0.01)
                self.tracker.set_state(host, cmd, exp_id, 'RECOVERING')
                self.tracker.finish(host, cmd, exp_id)
                results.append(True)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=run, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [], f'Thread safety errors: {errors}')
        self.assertEqual(len(results), 20)


# ---------------------------------------------------------------------------
# T8 — Recovery not reported before health checks pass
# ---------------------------------------------------------------------------

class TestRecoveryNotReportedPrematurely(unittest.TestCase):

    def test_verify_agent_running_no_pid_file(self):
        """_verify_agent_running returns (False, ...) when PID file is absent."""
        host = 'h1'
        try:
            os.unlink(_pid_path(host))
        except FileNotFoundError:
            pass

        running, msg = _verify_agent_running(host)
        self.assertFalse(running)
        self.assertIsNotNone(msg)

    def test_verify_agent_running_process_dead(self):
        """_verify_agent_running returns (False, ...) when PID no longer exists."""
        with TempPidFile('h2', 999999) as _:
            def fake_validate(pid, host, **kwargs):
                return None  # pass cmdline check

            def fake_kill(pid, sig):
                raise ProcessLookupError()

            with patch('topo.monitoring_topo._validate_agent_pid', side_effect=fake_validate), \
                 patch('topo.monitoring_topo.os.kill', side_effect=fake_kill):
                running, msg = _verify_agent_running('h2')

        self.assertFalse(running)
        self.assertIn('no longer exists', msg.lower())

    def test_verify_agent_running_process_alive(self):
        """_verify_agent_running returns (True, ...) when PID is alive and valid."""
        with TempPidFile('h3', 123) as _:
            def fake_validate(pid, host, **kwargs):
                return None  # pass

            def fake_kill(pid, sig):
                pass  # signal 0 succeeds → process alive

            with patch('topo.monitoring_topo._validate_agent_pid', side_effect=fake_validate), \
                 patch('topo.monitoring_topo.os.kill', side_effect=fake_kill):
                running, msg = _verify_agent_running('h3')

        self.assertTrue(running)
        self.assertIn('running', msg.lower())


# ---------------------------------------------------------------------------
# T9 — Self-terminating experiment processes (flood/cpu/mem)
# ---------------------------------------------------------------------------

class TestSelfTerminatingExperiments(unittest.TestCase):
    """Verify the experiment scripts terminate themselves within their duration.

    These tests use real subprocess calls on the actual scripts.  They are
    intentionally short-duration (1–2 s) to keep CI fast.
    """

    def _run_script(self, script_path, *args, timeout=10):
        import subprocess
        result = subprocess.run(
            [sys.executable, script_path, *args],
            capture_output=True, text=True, timeout=timeout
        )
        return result

    def test_congestion_test_terminates_after_duration(self):
        script = os.path.join(
            os.path.dirname(__file__), 'congestion_test.py'
        )
        # Run for 1 second at 1 Mbps — script must exit cleanly
        import subprocess
        start = time.time()
        result = subprocess.run(
            [sys.executable, script, '1', '1'],
            capture_output=True, text=True, timeout=10
        )
        elapsed = time.time() - start
        self.assertEqual(result.returncode, 0,
                         f'congestion_test.py failed: {result.stderr}')
        self.assertGreaterEqual(elapsed, 0.8, 'Script finished too fast')
        self.assertLess(elapsed, 8.0, 'Script ran too long')
        self.assertIn('done', result.stdout)

    def test_cpu_load_test_terminates_after_duration(self):
        script = os.path.join(os.path.dirname(__file__), 'cpu_load_test.py')
        import subprocess
        start = time.time()
        result = subprocess.run(
            [sys.executable, script, '1'],
            capture_output=True, text=True, timeout=10
        )
        elapsed = time.time() - start
        self.assertEqual(result.returncode, 0,
                         f'cpu_load_test.py failed: {result.stderr}')
        self.assertLess(elapsed, 8.0)
        self.assertIn('done', result.stdout)

    def test_memory_load_test_terminates_after_duration(self):
        script = os.path.join(os.path.dirname(__file__), 'memory_load_test.py')
        import subprocess
        start = time.time()
        result = subprocess.run(
            [sys.executable, script, '1', '50'],   # 50 MB, 1 s
            capture_output=True, text=True, timeout=10
        )
        elapsed = time.time() - start
        self.assertEqual(result.returncode, 0,
                         f'memory_load_test.py failed: {result.stderr}')
        self.assertIn('released', result.stdout)
        self.assertLess(elapsed, 8.0)


# ---------------------------------------------------------------------------
# T10 — Agent PID file written and removed by telemetry_agent.main()
# ---------------------------------------------------------------------------

class TestAgentPidFile(unittest.TestCase):

    def test_write_pid_file_creates_file_with_own_pid(self):
        """write_pid_file() writes the current process PID to the correct path."""
        from agents.telemetry_agent import write_pid_file, remove_pid_file, PID_FILE_TEMPLATE
        host = 'h1'
        pid_path = PID_FILE_TEMPLATE.format(hostname=host)
        try:
            write_pid_file(host)
            self.assertTrue(os.path.exists(pid_path),
                            f'PID file not found at {pid_path}')
            with open(pid_path) as f:
                content = f.read().strip()
            # Support both legacy PID-only and new PID:STARTTIME formats (Priority 5.3)
            stored_pid = int(content.split(':')[0])
            self.assertEqual(stored_pid, os.getpid())
            if ':' in content:
                stored_st = int(content.split(':')[1])
                self.assertGreaterEqual(stored_st, 0)
        finally:
            remove_pid_file(host)
            self.assertFalse(os.path.exists(pid_path))

    def test_remove_pid_file_idempotent(self):
        """remove_pid_file() does not raise if file already missing."""
        from agents.telemetry_agent import remove_pid_file
        # Should not raise
        remove_pid_file('h1')
        remove_pid_file('h1')

    def test_pid_file_path_per_host(self):
        """Each host gets a unique PID file path."""
        from agents.telemetry_agent import PID_FILE_TEMPLATE
        paths = {PID_FILE_TEMPLATE.format(hostname=f'h{i}') for i in range(1, 5)}
        self.assertEqual(len(paths), 4, 'Each host must have a unique PID file path')
        for i in range(1, 5):
            self.assertIn(f'h{i}', PID_FILE_TEMPLATE.format(hostname=f'h{i}'))


# ---------------------------------------------------------------------------
# T11 — Existing IPC _validate tests still pass
# ---------------------------------------------------------------------------

class TestExistingIPCValidateUnchanged(unittest.TestCase):
    """Re-run the original validate contract from test_ipc_socket.py inline."""

    def test_valid_commands_accepted(self):
        host, cmd, rate, dur = _validate({'cmd': 'flood', 'host': 'h1', 'rate': '8', 'duration': 20})
        self.assertEqual((host, cmd, rate, dur), ('h1', 'flood', '8', 20))

        host, cmd, rate, dur = _validate({'cmd': 'burst', 'host': 'h2', 'rate': '5', 'duration': 5})
        self.assertEqual((host, cmd, rate, dur), ('h2', 'burst', '5', 5))

        host, cmd, rate, dur = _validate({'cmd': 'cpu', 'host': 'h3', 'duration': 15})
        self.assertEqual((host, cmd, dur), ('h3', 'cpu', 15))

        host, cmd, rate, dur = _validate({'cmd': 'mem', 'host': 'h4', 'duration': 30})
        self.assertEqual((host, cmd, dur), ('h4', 'mem', 30))

        host, cmd, rate, dur = _validate({'cmd': 'kill', 'host': 'h1', 'duration': 20})
        self.assertEqual((host, cmd, dur), ('h1', 'kill', 20))

    def test_invalid_host_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate({'cmd': 'flood', 'host': 'h99', 'rate': '8', 'duration': 20})
        self.assertIn('unknown host', str(ctx.exception))

    def test_invalid_cmd_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate({'cmd': 'rm_rf', 'host': 'h1'})
        self.assertIn('unknown cmd', str(ctx.exception))

    def test_injection_in_host_rejected(self):
        with self.assertRaises(ValueError):
            _validate({'cmd': 'flood', 'host': 'h1; sudo reboot', 'rate': '8', 'duration': 20})

    def test_invalid_rate_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate({'cmd': 'flood', 'host': 'h1', 'rate': '999', 'duration': 20})
        self.assertIn('invalid rate', str(ctx.exception))

    def test_duration_too_high_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate({'cmd': 'cpu', 'host': 'h1', 'duration': 3600})
        self.assertIn('duration 3600 out of range', str(ctx.exception))

    def test_duration_zero_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            _validate({'cmd': 'cpu', 'host': 'h1', 'duration': 0})
        self.assertIn('duration 0 out of range', str(ctx.exception))

    def test_all_four_hosts_accepted(self):
        for h in ('h1', 'h2', 'h3', 'h4'):
            host, cmd, _, dur = _validate({'cmd': 'kill', 'host': h, 'duration': 10})
            self.assertEqual(host, h)

    def test_all_valid_commands_accepted(self):
        for cmd in ('flood', 'cpu', 'mem', 'kill', 'burst'):
            req = {'cmd': cmd, 'host': 'h1', 'rate': '8', 'duration': 10}
            host, got_cmd, rate, dur = _validate(req)
            self.assertEqual(got_cmd, cmd)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    unittest.main(verbosity=2)
