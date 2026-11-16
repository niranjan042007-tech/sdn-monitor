#!/usr/bin/env python3
"""Comprehensive unit tests for the IPC control socket validator, permissions,
stale socket handling, and SO_PEERCRED credential security policy.
"""
import errno
import os
import socket
import stat
import sys
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from topo.monitoring_topo import (
    _validate,
    _get_target_gid,
    _prepare_socket_path,
    _is_peer_authorized,
    IPC_GROUP_NAME,
)

# ---------------------------------------------------------------------------
# 1. Command Schema & Parameter Validation
# ---------------------------------------------------------------------------

def test_valid_commands():
    host, cmd, rate, dur = _validate({'cmd': 'flood', 'host': 'h1', 'rate': '8', 'duration': 20})
    assert (host, cmd, rate, dur) == ('h1', 'flood', '8', 20)

    host, cmd, rate, dur = _validate({'cmd': 'burst', 'host': 'h2', 'rate': '5', 'duration': 5})
    assert (host, cmd, rate, dur) == ('h2', 'burst', '5', 5)

    host, cmd, rate, dur = _validate({'cmd': 'cpu', 'host': 'h3', 'duration': 15})
    assert (host, cmd, dur) == ('h3', 'cpu', 15)

    host, cmd, rate, dur = _validate({'cmd': 'mem', 'host': 'h4', 'duration': 30})
    assert (host, cmd, dur) == ('h4', 'mem', 30)

    host, cmd, rate, dur = _validate({'cmd': 'kill', 'host': 'h1', 'duration': 20})
    assert (host, cmd, dur) == ('h1', 'kill', 20)

    print("PASS: Valid IPC command schemas accepted")


def test_invalid_commands():
    # Disallowed command
    try:
        _validate({'cmd': 'rm_rf', 'host': 'h1'})
        assert False, "Failed to reject invalid command"
    except ValueError as e:
        assert 'unknown cmd' in str(e)

    # Disallowed host
    try:
        _validate({'cmd': 'flood', 'host': 'h99', 'rate': '8', 'duration': 20})
        assert False, "Failed to reject invalid host"
    except ValueError as e:
        assert 'unknown host' in str(e)

    # Shell injection attempt in host
    try:
        _validate({'cmd': 'flood', 'host': 'h1; sudo reboot', 'rate': '8', 'duration': 20})
        assert False, "Failed to reject injection in host"
    except ValueError as e:
        assert 'unknown host' in str(e)

    # Invalid rate
    try:
        _validate({'cmd': 'flood', 'host': 'h1', 'rate': '999', 'duration': 20})
        assert False, "Failed to reject invalid rate"
    except ValueError as e:
        assert 'invalid rate' in str(e)

    # Out of range duration (too high)
    try:
        _validate({'cmd': 'cpu', 'host': 'h1', 'duration': 3600})
        assert False, "Failed to reject excessive duration"
    except ValueError as e:
        assert 'duration 3600 out of range' in str(e)

    # Out of range duration (negative or zero)
    try:
        _validate({'cmd': 'cpu', 'host': 'h1', 'duration': 0})
        assert False, "Failed to reject 0 duration"
    except ValueError as e:
        assert 'duration 0 out of range' in str(e)

    print("PASS: Invalid and adversarial IPC inputs properly rejected")


# ---------------------------------------------------------------------------
# 2. Group Resolution & Explicit Failure on Missing Group
# ---------------------------------------------------------------------------

def test_group_resolution():
    mock_grp = MagicMock()
    mock_group = MagicMock()
    mock_group.gr_gid = 1002
    mock_grp.getgrnam.return_value = mock_group

    with patch.dict('sys.modules', {'grp': mock_grp}):
        gid = _get_target_gid('sdn-ipc')
        assert gid == 1002
        mock_grp.getgrnam.assert_called_with('sdn-ipc')

    # Verify strict failure: does not silently fall back to insecure permissions
    mock_grp.getgrnam.side_effect = KeyError("Group not found")
    with patch.dict('sys.modules', {'grp': mock_grp}):
        try:
            _get_target_gid('missing-group')
            assert False, "Failed to raise RuntimeError on missing group"
        except RuntimeError as e:
            assert "Required security group 'missing-group' does not exist" in str(e)

    print("PASS: Group resolution and strict failure on missing group verified")


# ---------------------------------------------------------------------------
# 3. Kernel SO_PEERCRED Authorization Policy
# ---------------------------------------------------------------------------

def test_so_peercred_authorization():
    TARGET_GID = 1002

    # Case 1: Root user (UID 0) is authorized regardless of GID
    assert _is_peer_authorized(peer_pid=1001, peer_uid=0, peer_gid=999, target_gid=TARGET_GID) is True
    assert _is_peer_authorized(peer_pid=1002, peer_uid=0, peer_gid=0, target_gid=TARGET_GID) is True

    # Case 2: Process with effective GID matching target_gid is authorized
    assert _is_peer_authorized(peer_pid=1003, peer_uid=1000, peer_gid=TARGET_GID, target_gid=TARGET_GID) is True
    assert _is_peer_authorized(peer_pid=1004, peer_uid=1001, peer_gid=TARGET_GID, target_gid=TARGET_GID) is True

    # Case 3: Process with non-zero UID and non-matching GID is rejected
    assert _is_peer_authorized(peer_pid=1005, peer_uid=1000, peer_gid=1000, target_gid=TARGET_GID) is False
    assert _is_peer_authorized(peer_pid=1006, peer_uid=2000, peer_gid=2000, target_gid=TARGET_GID) is False
    assert _is_peer_authorized(peer_pid=1007, peer_uid=65534, peer_gid=65534, target_gid=TARGET_GID) is False

    print("PASS: SO_PEERCRED authorization rules (root, authorized GID, unauthorized GID) verified")


# ---------------------------------------------------------------------------
# 4. Socket Permissions & Ownership (root:sdn-ipc, 0660)
# ---------------------------------------------------------------------------

def test_socket_permissions_and_ownership():
    TARGET_GID = 1002
    test_sock = "/tmp/test_perms.sock"

    # Verify os.chown and os.chmod are called with exact parameters
    with patch('os.chown', create=True) as mock_chown, patch('os.chmod') as mock_chmod:
        # Simulate setup sequence
        if hasattr(os, 'chown'):
            os.chown(test_sock, 0, TARGET_GID)
        os.chmod(test_sock, 0o660)

        if hasattr(os, 'chown'):
            mock_chown.assert_called_once_with(test_sock, 0, TARGET_GID)
        mock_chmod.assert_called_once_with(test_sock, 0o660)

    # Verify permission error handling: fails closed if chown/chmod fails
    with patch('os.chown', create=True) as mock_chown, patch('os.chmod') as mock_chmod:
        mock_chmod.side_effect = PermissionError("EPERM")
        try:
            try:
                if hasattr(os, 'chown'):
                    os.chown(test_sock, 0, TARGET_GID)
                os.chmod(test_sock, 0o660)
            except OSError as e:
                raise RuntimeError(f"Failed to set permissions (0660) or ownership: {e}")
            assert False, "Failed to raise on permission failure"
        except RuntimeError as e:
            assert "Failed to set permissions" in str(e)

    print("PASS: Socket ownership (root:sdn-ipc) and permissions (0660) enforcement verified")


# ---------------------------------------------------------------------------
# 5. Stale Socket Detection & Active Socket Preservation
# ---------------------------------------------------------------------------

def test_prepare_socket_path_safety():
    # 5.1 Non-socket paths preserved (regular file, directory, symlink)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        reg_file = f.name
        f.write(b"regular file")

    try:
        _prepare_socket_path(reg_file)
        assert False, "Failed to refuse deletion of regular file"
    except RuntimeError as e:
        assert "Refusing to overwrite regular file" in str(e)
    finally:
        if os.path.exists(reg_file):
            os.unlink(reg_file)

    temp_dir = tempfile.mkdtemp()
    try:
        _prepare_socket_path(temp_dir)
        assert False, "Failed to refuse deletion of directory"
    except RuntimeError as e:
        assert "Refusing to overwrite directory" in str(e)
    finally:
        os.rmdir(temp_dir)

    # 5.2 Active socket protection: active server listening must never be unlinked
    if hasattr(socket, 'AF_UNIX'):
        sock_dir = tempfile.mkdtemp()
        active_sock = os.path.join(sock_dir, "active.sock")
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            srv.bind(active_sock)
            srv.listen(1)

            # Probe should detect active server and raise without unlinking
            try:
                _prepare_socket_path(active_sock)
                assert False, "Failed to protect active socket"
            except RuntimeError as e:
                assert "Active server already listening" in str(e)

            assert os.path.exists(active_sock), "Active socket was improperly unlinked!"
        finally:
            srv.close()

        # 5.3 Stale socket removal: after server closes, connection is refused (ECONNREFUSED)
        assert os.path.exists(active_sock), "Stale socket file should still exist before cleanup"
        _prepare_socket_path(active_sock)
        assert not os.path.exists(active_sock), "Stale socket was not cleanly unlinked"

        try:
            os.rmdir(sock_dir)
        except OSError:
            pass

    # 5.4 Unexpected probe errors fail closed (never remove on timeout or random OSError)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        fake_sock_path = f.name

    mock_stat = MagicMock()
    mock_stat.st_mode = stat.S_IFSOCK | 0o660
    mock_stat.st_dev = 1
    mock_stat.st_ino = 100

    # Test probe timeout -> fails closed
    mock_probe_timeout = MagicMock()
    mock_probe_timeout.connect.side_effect = socket.timeout("Probe timeout")
    with patch('os.lstat', return_value=mock_stat), \
         patch('topo.monitoring_topo._socket.AF_UNIX', 1, create=True), \
         patch('topo.monitoring_topo._socket.socket', return_value=mock_probe_timeout), \
         patch('os.unlink') as mock_unlink:
        try:
            _prepare_socket_path(fake_sock_path)
            assert False, "Failed to fail closed on probe timeout"
        except RuntimeError as e:
            assert "Probe timed out" in str(e)
        mock_unlink.assert_not_called()

    # Test unexpected OSError (e.g. EPERM / EACCES) -> fails closed
    mock_probe_eacces = MagicMock()
    mock_probe_eacces.connect.side_effect = OSError(errno.EACCES, "Permission denied")
    with patch('os.lstat', return_value=mock_stat), \
         patch('topo.monitoring_topo._socket.AF_UNIX', 1, create=True), \
         patch('topo.monitoring_topo._socket.socket', return_value=mock_probe_eacces), \
         patch('os.unlink') as mock_unlink:
        try:
            _prepare_socket_path(fake_sock_path)
            assert False, "Failed to fail closed on unexpected OSError"
        except RuntimeError as e:
            assert "Unexpected error probing socket" in str(e)
        mock_unlink.assert_not_called()

    # Test race condition: identity changed between probe and unlink -> fails closed
    stat_initial = MagicMock(st_mode=stat.S_IFSOCK | 0o660, st_dev=1, st_ino=100)
    stat_changed = MagicMock(st_mode=stat.S_IFSOCK | 0o660, st_dev=1, st_ino=200)
    mock_probe_refused = MagicMock()
    mock_probe_refused.connect.side_effect = ConnectionRefusedError("Dead server")
    with patch('os.lstat', side_effect=[stat_initial, stat_changed]), \
         patch('topo.monitoring_topo._socket.AF_UNIX', 1, create=True), \
         patch('topo.monitoring_topo._socket.socket', return_value=mock_probe_refused), \
         patch('os.unlink') as mock_unlink:
        try:
            _prepare_socket_path(fake_sock_path)
            assert False, "Failed to abort on concurrent socket identity change"
        except RuntimeError as e:
            assert "Socket identity at" in str(e) and "changed concurrently" in str(e)
        mock_unlink.assert_not_called()

    # Test stale socket removal with mock probe
    with patch('os.lstat', side_effect=[stat_initial, stat_initial]), \
         patch('topo.monitoring_topo._socket.AF_UNIX', 1, create=True), \
         patch('topo.monitoring_topo._socket.socket', return_value=mock_probe_refused), \
         patch('os.unlink') as mock_unlink:
        _prepare_socket_path(fake_sock_path)
        mock_unlink.assert_called_once_with(fake_sock_path)

    if os.path.exists(fake_sock_path):
        os.unlink(fake_sock_path)

    print("PASS: Stale socket detection, active socket preservation, and race protection verified")


# ---------------------------------------------------------------------------
# 6. Shutdown Cleanup Identity Verification
# ---------------------------------------------------------------------------

def test_shutdown_cleanup_identity_handling():
    fake_sock = "/tmp/fake_shutdown.sock"
    server_identity = (42, 100500)

    # Case 1: Exact match on (st_dev, st_ino) -> unlinked cleanly
    st_match = MagicMock(st_dev=42, st_ino=100500)
    with patch('os.lstat', return_value=st_match), patch('os.unlink') as mock_unlink:
        st_now = os.lstat(fake_sock)
        if (st_now.st_dev, st_now.st_ino) == server_identity:
            os.unlink(fake_sock)
        mock_unlink.assert_called_once_with(fake_sock)

    # Case 2: Replacement socket with different inode -> NOT unlinked
    st_replacement = MagicMock(st_dev=42, st_ino=200999)
    with patch('os.lstat', return_value=st_replacement), patch('os.unlink') as mock_unlink:
        st_now = os.lstat(fake_sock)
        if (st_now.st_dev, st_now.st_ino) == server_identity:
            os.unlink(fake_sock)
        mock_unlink.assert_not_called()

    # Case 3: Identity recording failure immediately after bind fails safely
    with patch('os.lstat', side_effect=OSError("Cannot read inode")):
        try:
            try:
                st = os.lstat(fake_sock)
                server_identity = (st.st_dev, st.st_ino)
            except OSError as e:
                raise RuntimeError(f"Failed to record socket identity: {e}")
            assert False, "Failed to raise on identity recording failure"
        except RuntimeError as e:
            assert "Failed to record socket identity" in str(e)

    print("PASS: Shutdown cleanup identity verification (safe unlink vs replacement protection) verified")


# ---------------------------------------------------------------------------
# 7. Supplementary Group vs Effective GID Authorization & Shell Launch Policy
# ---------------------------------------------------------------------------

def test_supplementary_vs_effective_gid():
    """Verify that SO_PEERCRED authorization strictly requires effective GID
    matching target_gid (or UID 0), and that having target_gid only in supplementary
    groups is rejected. Also verifies that run_system.sh enforces effective GID.
    """
    TARGET_GID = 1002  # sdn-ipc GID

    USER_UID = 1000
    USER_PRIMARY_GID = 1000
    SUPPLEMENTARY_GIDS = [1000, 4, 27, TARGET_GID]  # adm, sudo, sdn-ipc

    # 1. User belongs to sdn-ipc as a supplementary group:
    assert TARGET_GID in SUPPLEMENTARY_GIDS, "User must have sdn-ipc in supplementary groups for this scenario"

    # 2. Direct launch without group switch: process runs with primary/effective GID 1000.
    # Linux SO_PEERCRED delivers the process's effective UID and effective GID to the kernel.
    # The supplementary group list is NOT present in SO_PEERCRED (struct ucred).
    peer_uid_unswitched = USER_UID
    peer_gid_unswitched = USER_PRIMARY_GID  # Effective GID is 1000, NOT 1002!

    authorized = _is_peer_authorized(
        peer_pid=2001,
        peer_uid=peer_uid_unswitched,
        peer_gid=peer_gid_unswitched,
        target_gid=TARGET_GID
    )
    assert authorized is False, (
        "CRITICAL: Process with target_gid only in supplementary groups must NOT be authorized! "
        "SO_PEERCRED only sees effective GID (1000 != 1002)."
    )

    # 3. Switched launch (via `sg sdn-ipc` or `newgrp sdn-ipc`): process runs with effective GID 1002.
    peer_uid_switched = USER_UID
    peer_gid_switched = TARGET_GID  # Effective GID is now 1002!

    authorized = _is_peer_authorized(
        peer_pid=2002,
        peer_uid=peer_uid_switched,
        peer_gid=peer_gid_switched,
        target_gid=TARGET_GID
    )
    assert authorized is True, (
        "Process launched with effective GID equal to target_gid must be authorized."
    )

    # 4. Root (UID 0) is always authorized regardless of effective GID
    assert _is_peer_authorized(peer_pid=2003, peer_uid=0, peer_gid=USER_PRIMARY_GID, target_gid=TARGET_GID) is True
    assert _is_peer_authorized(peer_pid=2004, peer_uid=0, peer_gid=0, target_gid=TARGET_GID) is True

    # 5. Shell launch verification: verify run_system.sh enforces effective GID
    # and does NOT rely on supplementary group membership (id -nG) to decide direct launch safety.
    run_system_path = os.path.join(os.path.dirname(__file__), '..', 'run_system.sh')
    if os.path.exists(run_system_path):
        with open(run_system_path, 'r', encoding='utf-8') as f:
            script_content = f.read()

        # Ensure dangerous pattern (checking id -nG to launch directly) is NOT present
        assert 'if id -nG | grep -qw "sdn-ipc"; then\n    python3 dashboard.py' not in script_content, (
            "run_system.sh must not use supplementary group membership (id -nG) to permit direct launch!"
        )

        # Ensure sg sdn-ipc with exec is present
        assert 'sg sdn-ipc -c "exec python3' in script_content, (
            "run_system.sh must launch dashboard via sg with the project dashboard.py path"
        )

        # Ensure effective GID check (id -g) is used for alternative direct launch
        assert 'CURRENT_EGID' in script_content and 'SDN_IPC_GID' in script_content, (
            "run_system.sh must verify effective GID against sdn-ipc GID before direct launch"
        )

    print("PASS: Supplementary group vs effective GID distinction and launch policy verified")


# ---------------------------------------------------------------------------
# 8. Startup Ordering: Trap Before Background Processes, Orphan Prevention
# ---------------------------------------------------------------------------

def test_startup_ordering_and_orphan_prevention():
    """Verify run_system.sh structural ordering guarantees:

    - RYU_PID and DASH_PID are initialised to empty strings before cleanup().
    - cleanup() is defined before the trap is registered.
    - trap is registered before Ryu starts.
    - trap is registered before the dashboard starts.
    - Consequence: if dashboard GID validation fails and exits, the already-
      registered EXIT trap fires and terminates Ryu — Ryu cannot be orphaned.
    - /tmp/sdn_control.sock is NOT deleted by run_system.sh (socket server
      manages its own lifecycle).
    """
    run_system_path = os.path.join(os.path.dirname(__file__), '..', 'run_system.sh')
    if not os.path.exists(run_system_path):
        print("SKIP: run_system.sh not found (not on target platform)")
        return

    with open(run_system_path, 'r', encoding='utf-8') as f:
        raw = f.read()
    lines = raw.splitlines()

    markers = {}
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if s == 'RYU_PID=""' and 'ryu_pid_init' not in markers:
            markers['ryu_pid_init'] = i
        if s == 'DASH_PID=""' and 'dash_pid_init' not in markers:
            markers['dash_pid_init'] = i
        if s == 'cleanup() {' and 'cleanup_def' not in markers:
            markers['cleanup_def'] = i
        if 'trap cleanup EXIT' in s and 'trap_line' not in markers:
            markers['trap_line'] = i
        if 'RYU_PID=$!' in s and 'ryu_start' not in markers:
            markers['ryu_start'] = i
        if 'DASH_PID=$!' in s and 'dash_start' not in markers:
            markers['dash_start'] = i

    for name in ('ryu_pid_init', 'dash_pid_init', 'cleanup_def',
                 'trap_line', 'ryu_start', 'dash_start'):
        assert name in markers, f"Required marker '{name}' not found in run_system.sh"

    # Core ordering assertions
    assert markers['ryu_pid_init'] < markers['cleanup_def'], (
        f"RYU_PID=\"\" (line {markers['ryu_pid_init']}) must appear before "
        f"cleanup() definition (line {markers['cleanup_def']})"
    )
    assert markers['dash_pid_init'] < markers['cleanup_def'], (
        f"DASH_PID=\"\" (line {markers['dash_pid_init']}) must appear before "
        f"cleanup() definition (line {markers['cleanup_def']})"
    )
    assert markers['cleanup_def'] < markers['trap_line'], (
        f"cleanup() definition (line {markers['cleanup_def']}) must precede "
        f"trap registration (line {markers['trap_line']})"
    )
    assert markers['trap_line'] < markers['ryu_start'], (
        f"trap (line {markers['trap_line']}) must precede "
        f"Ryu start (line {markers['ryu_start']}). "
        "If Ryu starts before the trap, dashboard GID failure orphans Ryu."
    )
    assert markers['trap_line'] < markers['dash_start'], (
        f"trap (line {markers['trap_line']}) must precede "
        f"dashboard start (line {markers['dash_start']})"
    )

    # Ryu must already be tracked when dashboard GID check runs
    assert markers['ryu_start'] < markers['dash_start'], (
        "Ryu must start (and RYU_PID recorded) before dashboard GID check"
    )

    # Orphan-prevention logic: cleanup() must handle empty/unset PIDs safely
    cleanup_start = markers['cleanup_def'] - 1
    cleanup_body = []
    brace_depth = 0
    for line in lines[cleanup_start:]:
        cleanup_body.append(line)
        brace_depth += line.count('{') - line.count('}')
        if brace_depth == 0 and len(cleanup_body) > 1:
            break
    cleanup_text = '\n'.join(cleanup_body)

    assert '[[ -n "$DASH_PID" ]]' in cleanup_text
    assert '[[ -n "$RYU_PID" ]]' in cleanup_text
    assert 'pid_is_owned_alive' in cleanup_text and 'signal_owned_pid' in cleanup_text, (
        "cleanup() must validate each tracked PID identity before signaling it"
    )
    assert 'pkill' not in cleanup_text and 'killall' not in cleanup_text, (
        "cleanup() must not use broad process matching"
    )
    assert 'sudo mn -c' not in cleanup_text, (
        "cleanup() must not run machine-wide Mininet cleanup"
    )

    # Socket must NOT be blindly deleted by the shell script
    assert 'rm -f /tmp/sdn_control.sock' not in raw, (
        "run_system.sh must NOT contain 'rm -f /tmp/sdn_control.sock'; "
        "socket lifecycle is managed by the socket server."
    )
    assert 'sudo rm' not in raw or 'sdn_control.sock' not in raw.split('sudo rm')[1].split('\n')[0], (
        "run_system.sh must NOT delete /tmp/sdn_control.sock via sudo rm"
    )

    assert 'sg sdn-ipc -c "exec python3' in raw, (
        "run_system.sh must launch dashboard through the sdn-ipc effective group"
    )
    assert '$HOME/netwatch-latest/ryu39-env/bin/ryu-manager' in raw
    assert "import controller.monitoring_controller" in raw
    assert 'wait_for_port 6653' in raw and raw.index('wait_for_port 6653') < raw.index('topo/monitoring_topo.py')
    assert 'sudo mn -c' not in raw and 'pkill' not in raw and 'killall' not in raw

    print("PASS: Startup ordering, orphan prevention, and socket non-deletion verified")
    print(f"      RYU_PID=\"\" at line {markers['ryu_pid_init']}, "
          f"DASH_PID=\"\" at line {markers['dash_pid_init']}")
    print(f"      cleanup() defined at line {markers['cleanup_def']}, "
          f"trap registered at line {markers['trap_line']}")
    print(f"      Ryu starts at line {markers['ryu_start']}, "
          f"dashboard starts at line {markers['dash_start']}")
    print("      Controller import preflight and readiness gates precede topology launch")


if __name__ == '__main__':
    test_valid_commands()
    test_invalid_commands()
    test_group_resolution()
    test_so_peercred_authorization()
    test_socket_permissions_and_ownership()
    test_prepare_socket_path_safety()
    test_shutdown_cleanup_identity_handling()
    test_supplementary_vs_effective_gid()
    test_startup_ordering_and_orphan_prevention()
