#!/usr/bin/env python3
"""Real shell integration tests for run_system.sh.

Executes the actual run_system.sh bash script in an isolated temporary
test directory with mocked binaries (fake ryu-manager, sg, sudo, mn, id, getent).
No real Mininet, Ryu, or root permissions are used.

Tests:
1. test_bash_syntax: runs 'bash -n run_system.sh'
2. test_dashboard_failure_terminates_ryu: executes run_system.sh, fails dashboard
   startup, verifies EXIT trap runs, terminates Ryu, and preserves exit code 1.
3. test_normal_shutdown_terminates_both: executes run_system.sh, simulates normal
   Mininet session exit, verifies EXIT trap stops both Dashboard and Ryu, exit code 0.
4. test_sigterm_handling: sends SIGTERM, verifies cleanup trap fires and reaps processes.
5. test_ipc_socket_protection: verifies socket is never deleted by the script.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RUN_SYSTEM_SH = os.path.join(PROJECT_ROOT, 'run_system.sh')


def find_bash():
    """Locate bash executable cross-platform (Windows Git Bash or Linux bash)."""
    candidates = [
        shutil.which('bash'),
        '/bin/bash',
        '/usr/bin/bash',
        r'C:\Program Files\Git\bin\bash.exe',
        r'C:\Program Files\Git\usr\bin\bash.exe',
    ]
    for c in candidates:
        if c and os.path.exists(c):
            try:
                res = subprocess.run([c, '--version'], capture_output=True, timeout=5)
                if res.returncode == 0:
                    return c
            except Exception:
                pass
    return None


BASH_BIN = find_bash()
LINUX_PROC_AVAILABLE = os.path.exists('/proc/self/stat')


def create_test_env(tmp_dir, ryu_pid_file, dash_pid_file, mode="fail_dashboard"):
    """Set up temporary test directory with fake binaries and copies of needed files."""
    fake_bin = os.path.join(tmp_dir, 'fake_bin')
    os.makedirs(fake_bin, exist_ok=True)
    topo_dir = os.path.join(tmp_dir, 'topo')
    os.makedirs(topo_dir, exist_ok=True)

    # Fake sudo: executes only the exact requested test command.
    with open(os.path.join(fake_bin, 'sudo'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n"$@"\n')

    # Fake ss: report readiness only when the corresponding fake service is alive.
    with open(os.path.join(fake_bin, 'ss'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'if [ -f "$RYU_READY_FILE" ]; then echo "LISTEN 0 10 0.0.0.0:6653 0.0.0.0:*"; fi\n'
                'if [ -f "$DASH_READY_FILE" ]; then echo "LISTEN 0 10 127.0.0.1:5000 0.0.0.0:*"; fi\n')

    # Fake getent: returns sdn-ipc with GID 1002
    with open(os.path.join(fake_bin, 'getent'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'if [ "$1" = "group" ] && [ "$2" = "sdn-ipc" ]; then\n'
                '    echo "sdn-ipc:x:1002:"\n'
                '    exit 0\n'
                'fi\n'
                'exit 1\n')

    # Fake groupadd & usermod
    with open(os.path.join(fake_bin, 'groupadd'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\nexit 0\n')
    with open(os.path.join(fake_bin, 'usermod'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\nexit 0\n')

    # Fake id
    with open(os.path.join(fake_bin, 'id'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'case "$1" in\n'
                '    -un) echo "testuser" ;;\n'
                '    -nG) echo "testuser sdn-ipc" ;;\n'
                '    -g)  echo "${FAKE_EGID:-1000}" ;;\n'
                '    -gn) echo "${FAKE_EGID_NAME:-testuser}" ;;\n'
                '    *)   echo "1000" ;;\n'
                'esac\n')

    # Fake Ryu manager: records its PID, advertises readiness, and waits on a shell builtin.
    with open(os.path.join(fake_bin, 'ryu-manager'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'if [ "${FAKE_RYU_EXIT:-0}" = "1" ]; then exit 17; fi\n'
                'if [ -n "$RYU_PID_FILE" ]; then\n'
                '    echo $$ > "$RYU_PID_FILE"\n'
                'fi\n'
                'touch "$RYU_READY_FILE"\n'
                'cleanup() { trap - EXIT TERM INT; rm -f "$RYU_READY_FILE"; exit 0; }\n'
                'trap cleanup TERM INT EXIT\n'
                'while true; do IFS= read -r -t 1 _ || :; done\n')

    # Fake Ryu interpreter used by the import preflight.
    with open(os.path.join(fake_bin, 'python'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'if [ "${FAKE_IMPORT_FAIL:-0}" = "1" ]; then exit 1; fi\n'
                'exit 0\n')

    # Fake sg
    with open(os.path.join(fake_bin, 'sg'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'if [ "${FAKE_SG_FAIL:-0}" = "1" ]; then\n'
                '    exit 1\n'
                'fi\n'
                'if [ "$1" = "sdn-ipc" ] && [ "$2" = "-c" ]; then\n'
                '    if [ "$3" = "true" ]; then\n'
                '        exit 0\n'
                '    fi\n'
                '    eval "$3"\n'
                '    exit $?\n'
                'fi\n'
                'exit 1\n')

    # Fake python3 handles Flask health-by-port and topology PID/readiness lifecycle.
    with open(os.path.join(fake_bin, 'python3'), 'w', newline='\n') as f:
        f.write('#!/usr/bin/env bash\n'
                'case "$1" in */dashboard.py)\n'
                '    if [ -n "$DASH_PID_FILE" ]; then\n'
                '        echo $$ > "$DASH_PID_FILE"\n'
                '    fi\n'
                '    touch "$DASH_READY_FILE"\n'
                '    cleanup() { trap - EXIT TERM INT; rm -f "$DASH_READY_FILE"; exit 0; }\n'
                '    trap cleanup TERM INT EXIT\n'
                '    while true; do IFS= read -r -t 1 _ || :; done ;;\n'
                '*/monitoring_topo.py)\n'
                '    pid_file="$NETWATCH_TOPOLOGY_PID_FILE"\n'
                '    start=$(awk \'{print $22}\' /proc/$$/stat)\n'
                '    printf "%s:%s:READY\\n" "$$" "$start" > "$pid_file"\n'
                '    cleanup() {\n'
                '      IFS=: read -r saved_pid saved_start state < "$pid_file" 2>/dev/null || true\n'
                '      if [ "$saved_pid" = "$$" ] && [ "$saved_start" = "$start" ]; then rm -f "$pid_file"; fi\n'
                '      trap - EXIT TERM INT\n'
                '      exit 0\n'
                '    }\n'
                '    trap cleanup TERM INT\n'
                '    while [ ! -f stop_topo ]; do IFS= read -r -t 0.1 _ || :; done\n'
                '    cleanup ;;\n'
                '*) exit 0 ;;\n'
                'esac\n')

    # Stub files so filesystem checks in run_system.sh pass
    with open(os.path.join(tmp_dir, 'dashboard.py'), 'w', newline='\n') as f:
        f.write('# stub dashboard\n')
    with open(os.path.join(topo_dir, 'monitoring_topo.py'), 'w', newline='\n') as f:
        f.write('# stub topo\n')

    # Copy the actual run_system.sh script
    shutil.copy2(RUN_SYSTEM_SH, os.path.join(tmp_dir, 'run_system.sh'))


def is_pid_alive(bash_bin, pid):
    """Check if process with `pid` is alive using bash's kill -0."""
    res = subprocess.run([bash_bin, '-c', f'kill -0 {pid} 2>&1'], capture_output=True)
    return res.returncode == 0


def _test_environment():
    """Shell setup isolated from real listeners, runtime state, and services."""
    return '''
chmod +x fake_bin/*
export PATH="$(pwd)/fake_bin:$PATH"
export RYU_PID_FILE="$(pwd)/ryu.pid"
export DASH_PID_FILE="$(pwd)/dash.pid"
export RYU_READY_FILE="$(pwd)/ryu.ready"
export DASH_READY_FILE="$(pwd)/dash.ready"
export NETWATCH_RUNTIME_DIR="$(pwd)/runtime"
export NETWATCH_CONTROL_SOCKET="$(pwd)/runtime/sdn_control.sock"
export NETWATCH_RYU_MANAGER="$(pwd)/fake_bin/ryu-manager"
export NETWATCH_PRESERVE_RUNTIME_STATE=1
export FAKE_SG_FAIL=0
export FAKE_EGID=1002
export FAKE_EGID_NAME=sdn-ipc
export TOPO_MODE=sleep
'''


# ===========================================================================
# 1. Syntax Validation: bash -n run_system.sh
# ===========================================================================

def test_bash_syntax():
    """Run `bash -n run_system.sh` to verify syntax of the actual shell script."""
    res = subprocess.run([BASH_BIN, '-n', RUN_SYSTEM_SH], capture_output=True, text=True)
    assert res.returncode == 0, f"bash -n failed with returncode {res.returncode}:\n{res.stderr}"
    print(f"PASS [REAL SHELL]: bash -n run_system.sh syntax valid (using {BASH_BIN})")


# ===========================================================================
# 2. Real Shell Integration: Dashboard startup failure terminates Ryu
# ===========================================================================

def test_dashboard_failure_terminates_ryu():
    """Execute ACTUAL run_system.sh; simulate dashboard GID rejection;
    verify EXIT trap runs, terminates Ryu, leaves no orphans, and preserves exit code 1.
    """
    tmp_dir = tempfile.mkdtemp(prefix='sdn_test_fail_')
    try:
        ryu_pid_file = os.path.join(tmp_dir, 'ryu.pid')
        dash_pid_file = os.path.join(tmp_dir, 'dash.pid')
        create_test_env(tmp_dir, ryu_pid_file, dash_pid_file)

        # Environment to trigger dashboard failure:
        # FAKE_SG_FAIL=1 (sg unavailable/fails), FAKE_EGID=1000 != sdn-ipc (1002)
        posix_tmp = tmp_dir.replace('\\', '/')
        bash_script = f"""
cd "{posix_tmp}"
{_test_environment()}
export FAKE_SG_FAIL=1
./run_system.sh
"""
        proc = subprocess.run(
            [BASH_BIN, '-c', bash_script],
            capture_output=True,
            text=True,
            timeout=15
        )

        output = proc.stdout + proc.stderr

        # 1. Verify original exit status (1) is preserved
        assert proc.returncode == 1, f"Expected exit code 1 on dashboard failure, got {proc.returncode}. Output:\n{output}"

        # 2. Verify Ryu was started before dashboard failed
        assert "Starting Ryu controller" in output, f"Ryu was not started. Output:\n{output}"
        assert os.path.exists(ryu_pid_file), "RYU_PID_FILE was not created"
        with open(ryu_pid_file, 'r') as f:
            ryu_pid = f.read().strip()
        assert ryu_pid.isdigit(), f"Invalid recorded Ryu PID: {ryu_pid!r}"

        # 3. Verify readiness failure is detected and cleanup follows.
        assert "Flask" in output and "ERROR:" in output, output

        # 4. Verify cleanup trap executed
        assert "Shutting down NETWATCH services..." in output, "Cleanup trap did not run"
        assert f"Stopping owned Ryu controller (PID {ryu_pid})" in output, output
        assert "NETWATCH shutdown complete." in output, output

        # 5. Verify Ryu process is completely TERMINATED (not orphaned)
        time.sleep(0.2)
        assert not is_pid_alive(BASH_BIN, ryu_pid), f"Mock Ryu process (PID {ryu_pid}) is still alive! Orphan leak!"

        # 6. Verify dashboard process was never launched
        assert not os.path.exists(dash_pid_file), "Dashboard should not have been started"

        print(f"PASS [REAL SHELL]: Dashboard failure triggered EXIT trap -> Ryu (PID {ryu_pid}) terminated, exit code 1 preserved")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# 3. Real Shell Integration: Normal startup and clean shutdown
# ===========================================================================

def test_normal_shutdown_terminates_both():
    """Execute ACTUAL run_system.sh; simulate successful startup and clean Mininet exit;
    verify EXIT trap runs, terminates both Dashboard and Ryu, and exits 0.
    """
    tmp_dir = tempfile.mkdtemp(prefix='sdn_test_norm_')
    try:
        ryu_pid_file = os.path.join(tmp_dir, 'ryu.pid')
        dash_pid_file = os.path.join(tmp_dir, 'dash.pid')
        create_test_env(tmp_dir, ryu_pid_file, dash_pid_file)

        posix_tmp = tmp_dir.replace('\\', '/')
        bash_script = f"""
cd "{posix_tmp}"
{_test_environment()}
./run_system.sh > run.log 2>&1 &
SCRIPT_PID=$!
READY=0
for i in $(seq 1 100); do
    if grep -q ':READY$' runtime/topology.pid 2>/dev/null; then READY=1; break; fi
    if ! kill -0 "$SCRIPT_PID" 2>/dev/null; then break; fi
    sleep 0.1
done
if [ "$READY" = 1 ]; then touch stop_topo; fi
wait "$SCRIPT_PID"
EXIT_CODE=$?
cat run.log
echo "SCRIPT_FINISHED_WITH=$EXIT_CODE"
"""
        proc = subprocess.run(
            [BASH_BIN, '-c', bash_script],
            capture_output=True,
            text=True,
            timeout=30
        )

        output = proc.stdout + proc.stderr

        # 1. Verify topology was ready and normal CLI exit produced a clean shutdown.
        assert 'SCRIPT_FINISHED_WITH=0' in output, output

        # 2. Verify both Ryu and Dashboard started
        assert "Starting Ryu controller" in output, "Ryu not started"
        assert "Starting Flask backend" in output, "Dashboard not started"
        assert os.path.exists(ryu_pid_file), "Ryu PID file missing"
        assert os.path.exists(dash_pid_file), "Dash PID file missing"

        with open(ryu_pid_file, 'r') as f:
            ryu_pid = f.read().strip()
        with open(dash_pid_file, 'r') as f:
            dash_pid = f.read().strip()

        # 3. Verify cleanup trap executed and stopped both
        assert "Shutting down NETWATCH services..." in output, output
        assert "Stopping owned Flask backend" in output, output
        assert "Stopping owned Ryu controller" in output, output
        assert "NETWATCH shutdown complete." in output, output

        # 4. Verify neither Ryu nor Dashboard are alive
        time.sleep(0.2)
        assert not is_pid_alive(BASH_BIN, ryu_pid), f"Ryu (PID {ryu_pid}) is still alive!"
        assert not is_pid_alive(BASH_BIN, dash_pid), f"Dashboard (PID {dash_pid}) is still alive!"
        assert not os.path.exists(os.path.join(tmp_dir, 'runtime', 'topology.pid')), "Topology PID file was left stale"

        print(f"PASS [REAL SHELL]: Normal run -> both Ryu (PID {ryu_pid}) and Dashboard (PID {dash_pid}) terminated cleanly, exit code 0")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# 4. Real Shell Integration: SIGTERM / Interruption handling
# ===========================================================================

def test_sigterm_handling():
    """Execute ACTUAL run_system.sh in background; send SIGTERM to the script;
    verify trap cleanup terminates background Ryu and Dashboard processes.
    """
    tmp_dir = tempfile.mkdtemp(prefix='sdn_test_sig_')
    try:
        ryu_pid_file = os.path.join(tmp_dir, 'ryu.pid')
        dash_pid_file = os.path.join(tmp_dir, 'dash.pid')
        create_test_env(tmp_dir, ryu_pid_file, dash_pid_file)

        posix_tmp = tmp_dir.replace('\\', '/')
        # Script launches run_system.sh, waits for PIDs, then kills script with SIGTERM
        bash_script = f"""
cd "{posix_tmp}"
set -m
{_test_environment()}

./run_system.sh < /dev/null > run.log 2>&1 &
SCRIPT_PID=$!

# Wait until all three services reached their readiness point.
for i in $(seq 1 100); do
    if grep -q ':READY$' runtime/topology.pid 2>/dev/null; then
        break
    fi
    sleep 0.1
done

kill -TERM $SCRIPT_PID 2>/dev/null
wait $SCRIPT_PID 2>/dev/null
EXIT_CODE=$?

cat run.log
echo "SCRIPT_FINISHED_WITH=$EXIT_CODE"
"""
        proc = subprocess.run(
            [BASH_BIN, '-c', bash_script],
            capture_output=True,
            text=True,
            timeout=60
        )

        output = proc.stdout + proc.stderr

        assert os.path.exists(ryu_pid_file), "Ryu PID file missing in SIGTERM test"
        assert os.path.exists(dash_pid_file), "Dash PID file missing in SIGTERM test"
        with open(ryu_pid_file, 'r') as f:
            ryu_pid = f.read().strip()
        with open(dash_pid_file, 'r') as f:
            dash_pid = f.read().strip()

        # Verify trap ran
        assert "Shutting down NETWATCH services..." in output, f"Cleanup trap did not run on SIGTERM. Output:\n{output}"

        # Verify processes were terminated
        time.sleep(0.2)
        assert not is_pid_alive(BASH_BIN, ryu_pid), f"Ryu (PID {ryu_pid}) remained alive after SIGTERM!"
        assert not is_pid_alive(BASH_BIN, dash_pid), f"Dashboard (PID {dash_pid}) remained alive after SIGTERM!"

        print(f"PASS [REAL SHELL]: SIGTERM trapped -> cleanup executed, Ryu ({ryu_pid}) and Dashboard ({dash_pid}) terminated")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# 5. Real Shell Integration: SIGINT (Ctrl+C) handling
# ===========================================================================

def test_sigint_handling():
    """Execute ACTUAL run_system.sh in background; send SIGINT to the script;
    verify trap cleanup terminates background Ryu and Dashboard processes.
    """
    tmp_dir = tempfile.mkdtemp(prefix='sdn_test_sigint_')
    try:
        ryu_pid_file = os.path.join(tmp_dir, 'ryu.pid')
        dash_pid_file = os.path.join(tmp_dir, 'dash.pid')
        create_test_env(tmp_dir, ryu_pid_file, dash_pid_file)

        posix_tmp = tmp_dir.replace('\\', '/')
        bash_script = f"""
cd "{posix_tmp}"
set -m
{_test_environment()}

./run_system.sh < /dev/null > run.log 2>&1 &
SCRIPT_PID=$!

# Wait until topology readiness is published.
for i in $(seq 1 100); do
    if grep -q ':READY$' runtime/topology.pid 2>/dev/null; then
        break
    fi
    sleep 0.1
done

kill -INT $SCRIPT_PID 2>/dev/null
wait $SCRIPT_PID 2>/dev/null
EXIT_CODE=$?

cat run.log
echo "SCRIPT_FINISHED_WITH=$EXIT_CODE"
"""
        proc = subprocess.run(
            [BASH_BIN, '-c', bash_script],
            capture_output=True,
            text=True,
            timeout=60
        )

        output = proc.stdout + proc.stderr

        assert os.path.exists(ryu_pid_file), "Ryu PID file missing in SIGINT test"
        assert os.path.exists(dash_pid_file), "Dash PID file missing in SIGINT test"
        with open(ryu_pid_file, 'r') as f:
            ryu_pid = f.read().strip()
        with open(dash_pid_file, 'r') as f:
            dash_pid = f.read().strip()

        # Verify trap ran
        assert "Shutting down NETWATCH services..." in output, f"Cleanup trap did not run on SIGINT. Output:\n{output}"

        # Verify processes were terminated
        time.sleep(0.2)
        assert not is_pid_alive(BASH_BIN, ryu_pid), f"Ryu (PID {ryu_pid}) remained alive after SIGINT!"
        assert not is_pid_alive(BASH_BIN, dash_pid), f"Dashboard (PID {dash_pid}) remained alive after SIGINT!"

        print(f"PASS [REAL SHELL]: SIGINT trapped -> cleanup executed, Ryu ({ryu_pid}) and Dashboard ({dash_pid}) terminated")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# 6. Socket Protection & Inode Lifecycle Check
# ===========================================================================

def test_ipc_socket_protection():
    """Verify socket lifecycle and cleanup remain ownership scoped."""
    with open(RUN_SYSTEM_SH, 'r', encoding='utf-8') as f:
        content = f.read()

    assert "rm -f /tmp/sdn_control.sock" not in content, \
        "Forbidden unconditional socket deletion found in run_system.sh!"
    assert "sudo rm -f /tmp/sdn_control.sock" not in content, \
        "Forbidden sudo socket deletion found in run_system.sh!"
    assert 'sudo mn -c' not in content, "Machine-wide Mininet cleanup is forbidden."
    assert 'pkill' not in content and 'killall' not in content, "Broad process killing is forbidden."
    assert 'pid_is_owned_alive' in content and 'signal_owned_pid' in content

    print("PASS [UNIT]: launcher uses owned PIDs and preserves socket/Mininet boundaries")


def test_import_failure_prevents_service_start():
    tmp_dir = tempfile.mkdtemp(prefix='netwatch_test_import_')
    try:
        create_test_env(tmp_dir, os.path.join(tmp_dir, 'ryu.pid'), os.path.join(tmp_dir, 'dash.pid'))
        posix_tmp = tmp_dir.replace('\\', '/')
        script = f'cd "{posix_tmp}"\n{_test_environment()}export FAKE_IMPORT_FAIL=1\n./run_system.sh'
        result = subprocess.run([BASH_BIN, '-c', script], capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert 'cannot import controller.monitoring_controller' in output
        assert not os.path.exists(os.path.join(tmp_dir, 'ryu.pid'))
        assert not os.path.exists(os.path.join(tmp_dir, 'dash.pid'))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_early_ryu_exit_prevents_backend_and_topology_start():
    tmp_dir = tempfile.mkdtemp(prefix='netwatch_test_ryu_exit_')
    try:
        create_test_env(tmp_dir, os.path.join(tmp_dir, 'ryu.pid'), os.path.join(tmp_dir, 'dash.pid'))
        posix_tmp = tmp_dir.replace('\\', '/')
        script = f'cd "{posix_tmp}"\n{_test_environment()}export FAKE_RYU_EXIT=1\n./run_system.sh'
        result = subprocess.run([BASH_BIN, '-c', script], capture_output=True, text=True, timeout=10)
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert 'Ryu' in output and 'ERROR:' in output
        assert not os.path.exists(os.path.join(tmp_dir, 'dash.pid'))
        assert not os.path.exists(os.path.join(tmp_dir, 'runtime', 'topology.pid'))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_repeated_startup_shutdown_cleans_owned_runtime_state():
    tmp_dir = tempfile.mkdtemp(prefix='netwatch_test_repeat_')
    try:
        create_test_env(tmp_dir, os.path.join(tmp_dir, 'ryu.pid'), os.path.join(tmp_dir, 'dash.pid'))
        posix_tmp = tmp_dir.replace('\\', '/')
        script = f'''
cd "{posix_tmp}"
{_test_environment()}
for cycle in 1 2; do
    rm -f stop_topo
    ./run_system.sh > "run-$cycle.log" 2>&1 &
    script_pid=$!
    ready=0
    for i in $(seq 1 100); do
        if grep -q ':READY$' runtime/topology.pid 2>/dev/null; then ready=1; break; fi
        sleep 0.1
    done
    if [ "$ready" = 1 ]; then touch stop_topo; fi
    wait "$script_pid"
    status=$?
    echo "CYCLE-$cycle=$status"
    cat "run-$cycle.log"
    [ "$status" = 0 ] || exit "$status"
done
'''
        result = subprocess.run([BASH_BIN, '-c', script], capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert 'CYCLE-1=0' in output and 'CYCLE-2=0' in output, output
        assert not os.path.exists(os.path.join(tmp_dir, 'runtime', 'topology.pid'))
        assert not os.path.exists(os.path.join(tmp_dir, 'ryu.ready'))
        assert not os.path.exists(os.path.join(tmp_dir, 'dash.ready'))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


import unittest


class TestShellIntegration(unittest.TestCase):

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_bash_syntax(self):
        test_bash_syntax()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_dashboard_failure_terminates_ryu(self):
        test_dashboard_failure_terminates_ryu()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_normal_shutdown_terminates_both(self):
        test_normal_shutdown_terminates_both()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_sigterm_handling(self):
        test_sigterm_handling()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_sigint_handling(self):
        test_sigint_handling()

    def test_ipc_socket_protection(self):
        test_ipc_socket_protection()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_import_failure_prevents_service_start(self):
        test_import_failure_prevents_service_start()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_early_ryu_exit_prevents_later_startup(self):
        test_early_ryu_exit_prevents_backend_and_topology_start()

    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_repeated_startup_shutdown(self):
        test_repeated_startup_shutdown_cleans_owned_runtime_state()

if __name__ == '__main__':
    if BASH_BIN is not None and LINUX_PROC_AVAILABLE:
        print("=" * 70)
        print("RUNNING REAL SHELL INTEGRATION TESTS FOR run_system.sh")
        print("=" * 70)
        test_bash_syntax()
        test_dashboard_failure_terminates_ryu()
        test_normal_shutdown_terminates_both()
        test_sigterm_handling()
        test_sigint_handling()
        test_ipc_socket_protection()
        test_import_failure_prevents_service_start()
        test_early_ryu_exit_prevents_backend_and_topology_start()
        test_repeated_startup_shutdown_cleans_owned_runtime_state()
        print("=" * 70)
        print("ALL REAL SHELL INTEGRATION TESTS PASSED")
        print("=" * 70)
    else:
        print("SKIP: bash not available in this environment")
    unittest.main()
