#!/usr/bin/env python3
"""Inspect the generated TAR archive and verify corrected run_system.sh and new tests."""
import tarfile, sys, os

tar_path = os.path.join(os.path.dirname(__file__), '..', 'sdn-monitor-deploy.tar.gz')

with tarfile.open(tar_path, 'r:gz') as tar:
    names = sorted(tar.getnames())
    print('Files in archive (%d):' % len(names))
    for n in names:
        print(' ', n)

    # --- Inspect run_system.sh ---
    rsh = tar.extractfile('sdn-monitor/run_system.sh').read().decode('utf-8')
    rlines = rsh.splitlines()

    markers = {}
    for i, line in enumerate(rlines, 1):
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

    print()
    print('=== run_system.sh (from archive) ===')
    for k, v in sorted(markers.items(), key=lambda x: x[1]):
        print('  Line %3d: %s' % (v, k))
    print('  exec python3 dashboard present:', 'exec python3 dashboard.py' in rsh)
    print('  Fail-closed exit 1 present:', 'exit 1' in rsh)
    print('  pkill -P present:', 'pkill -P' in rsh)
    print('  sdn_control.sock NOT deleted:', 'rm -f /tmp/sdn_control.sock' not in rsh)
    print('  OLD id-nG direct launch absent:', 'if id -nG | grep -qw' not in rsh)

    # --- Inspect test_ipc_socket.py ---
    tst = tar.extractfile('sdn-monitor/tests/test_ipc_socket.py').read().decode('utf-8')
    print()
    print('=== tests/test_ipc_socket.py (from archive) ===')
    print('  test_supplementary_vs_effective_gid:', 'test_supplementary_vs_effective_gid' in tst)
    print('  test_startup_ordering_and_orphan_prevention:', 'test_startup_ordering_and_orphan_prevention' in tst)
    print('  assert authorized is False:', 'assert authorized is False' in tst)
    print('  pkill -P orphan check:', 'pkill -P' in tst)
    print('  socket non-deletion check:', 'sdn_control.sock' in tst)

    # --- Ordering assertions ---
    errors = []
    if markers.get('ryu_pid_init', 999) >= markers.get('cleanup_def', 0):
        errors.append('RYU_PID init must precede cleanup def')
    if markers.get('dash_pid_init', 999) >= markers.get('cleanup_def', 0):
        errors.append('DASH_PID init must precede cleanup def')
    if markers.get('cleanup_def', 999) >= markers.get('trap_line', 0):
        errors.append('cleanup def must precede trap registration')
    if markers.get('trap_line', 999) >= markers.get('ryu_start', 0):
        errors.append('trap must precede Ryu start')
    if markers.get('trap_line', 999) >= markers.get('dash_start', 0):
        errors.append('trap must precede dash start')

    print()
    if errors:
        for e in errors:
            print('FAIL:', e)
        sys.exit(1)

    print('ORDERING VERIFIED:')
    print('  trap registered at line', markers['trap_line'])
    print('  Ryu starts at line', markers['ryu_start'])
    print('  Dashboard starts at line', markers['dash_start'])
    print('  -> Ryu cannot be orphaned by dashboard GID failure')
