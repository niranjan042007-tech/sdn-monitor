#!/usr/bin/env python3
"""Extract both deployment archives to separate temp dirs and verify contents."""
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile

PROJECT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
TAR_PATH = os.path.join(PROJECT, 'sdn-monitor-deploy.tar.gz')
ZIP_PATH = os.path.join(PROJECT, 'sdn-monitor-deploy.zip')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest().upper()


def verify_extracted(root, label):
    """Verify required files inside an extracted directory."""
    checks = {}
    rsh_path = os.path.join(root, 'sdn-monitor', 'run_system.sh')
    tst_path = os.path.join(root, 'sdn-monitor', 'tests', 'test_lifecycle.py')
    ipc_path = os.path.join(root, 'sdn-monitor', 'tests', 'test_ipc_socket.py')
    sh_int_path = os.path.join(root, 'sdn-monitor', 'tests', 'test_shell_integration.py')

    for name, path in [('run_system.sh', rsh_path),
                       ('test_lifecycle.py', tst_path),
                       ('test_ipc_socket.py', ipc_path),
                       ('test_shell_integration.py', sh_int_path)]:
        if not os.path.exists(path):
            print(f"  FAIL [{label}]: {name} not found at {path}")
            checks[name] = False
            continue
        content = open(path, encoding='utf-8').read()
        lines = content.splitlines()

        if name == 'run_system.sh':
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

            ok = (
                markers.get('ryu_pid_init', 999) < markers.get('cleanup_def', 0)
                and markers.get('dash_pid_init', 999) < markers.get('cleanup_def', 0)
                and markers.get('cleanup_def', 999) < markers.get('trap_line', 0)
                and markers.get('trap_line', 999) < markers.get('ryu_start', 0)
                and markers.get('trap_line', 999) < markers.get('dash_start', 0)
                and 'rm -f /tmp/sdn_control.sock' not in content
                and 'exec python3 dashboard.py' in content
                and 'if id -nG | grep -qw' not in content
            )
            checks[name] = ok
            status = 'OK' if ok else 'FAIL'
            print(f"  [{label}] {name}: {status}")
            if ok:
                print(f"           trap={markers['trap_line']}, "
                      f"Ryu={markers['ryu_start']}, "
                      f"dash={markers['dash_start']}")

        elif name == 'test_lifecycle.py':
            ok = (
                'test_shell_integration' in content
                and 'test_dashboard_failure_terminates_ryu' in content
                and 'test_normal_shutdown_terminates_both' in content
            )
            checks[name] = ok
            print(f"  [{label}] {name}: {'OK' if ok else 'FAIL'}")

        elif name == 'test_ipc_socket.py':
            ok = (
                'test_supplementary_vs_effective_gid' in content
                and 'test_startup_ordering_and_orphan_prevention' in content
            )
            checks[name] = ok
            print(f"  [{label}] {name}: {'OK' if ok else 'FAIL'}")

        elif name == 'test_shell_integration.py':
            ok = (
                'test_dashboard_failure_terminates_ryu' in content
                and 'test_normal_shutdown_terminates_both' in content
                and 'test_sigterm_handling' in content
                and 'test_sigint_handling' in content
                and 'test_bash_syntax' in content
            )
            checks[name] = ok
            print(f"  [{label}] {name}: {'OK' if ok else 'FAIL'}")

    return all(checks.values())


results = {}

for archive_path, label, is_tar in [
    (TAR_PATH, 'TAR', True),
    (ZIP_PATH, 'ZIP', False),
]:
    print(f"\n{'='*60}")
    print(f"Archive: {os.path.basename(archive_path)}")
    print(f"SHA-256: {sha256(archive_path)}")
    print(f"Size:    {os.path.getsize(archive_path):,} bytes")

    tmpdir = tempfile.mkdtemp(prefix=f'sdn_verify_{label}_')
    try:
        if is_tar:
            with tarfile.open(archive_path, 'r:gz') as tar:
                file_count = len(tar.getnames())
                print(f"Files:   {file_count}")
                tar.extractall(tmpdir)
        else:
            with zipfile.ZipFile(archive_path, 'r') as z:
                file_count = len(z.namelist())
                print(f"Files:   {file_count}")
                z.extractall(tmpdir)

        ok = verify_extracted(tmpdir, label)
        results[label] = ok
        print(f"Overall [{label}]: {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

print()
if all(results.values()):
    print("VERIFICATION COMPLETE: Both archives contain corrected run_system.sh and new tests.")
else:
    print("VERIFICATION FAILED: See above for details.")
    sys.exit(1)
