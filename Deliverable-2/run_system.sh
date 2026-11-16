#!/usr/bin/env bash
# NETWATCH startup and ownership-scoped shutdown.
set -Eeuo pipefail
umask 077

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR"
PROJECT_TAG="${BASE_DIR##*/}"
RUNTIME_ROOT="${XDG_RUNTIME_DIR:-/tmp}"
RUNTIME_DIR="${NETWATCH_RUNTIME_DIR:-$RUNTIME_ROOT/netwatch-${UID}-${PROJECT_TAG}}"
TOPO_PID_FILE="$RUNTIME_DIR/topology.pid"
STARTUP_TIMEOUT="${NETWATCH_STARTUP_TIMEOUT:-30}"

RYU_PID=""
RYU_START=""
DASH_PID=""
DASH_START=""
TOPO_PID=""
TOPO_START=""
TOPO_LAUNCH_PID=""
TOPO_LAUNCH_START=""
CLEANING=0

proc_starttime() {
    local pid="$1" stat_line rest
    [[ "$pid" =~ ^[0-9]+$ && -r "/proc/$pid/stat" ]] || return 1
    IFS= read -r stat_line < "/proc/$pid/stat" || return 1
    # Remove pid and parenthesized comm; starttime is field 20 in the remainder.
    rest="${stat_line##*) }"
    local -a fields
    read -r -a fields <<< "$rest"
    [[ ${#fields[@]} -gt 19 && ${fields[19]} =~ ^[0-9]+$ ]] || return 1
    printf '%s' "${fields[19]}"
}

pid_is_owned_alive() {
    local pid="$1" expected_start="$2" actual_start stat_line rest process_state
    [[ -n "$pid" && -n "$expected_start" ]] || return 1
    actual_start="$(proc_starttime "$pid" 2>/dev/null)" || return 1
    [[ "$actual_start" == "$expected_start" ]] || return 1

    # A zombie or dead process is no longer running, even if /proc still exists.
    IFS= read -r stat_line < "/proc/$pid/stat" || return 1
    rest="${stat_line##*) }"
    process_state="${rest%% *}"
    [[ "$process_state" != "Z" && "$process_state" != "X" ]]
}

track_child() {
    local pid="$1" start
    start="$(proc_starttime "$pid")" || return 1
    printf '%s:%s' "$pid" "$start"
}

wait_for_exit() {
    local pid="$1" start="$2" attempts="${3:-30}" i
    for ((i = 0; i < attempts; i++)); do
        pid_is_owned_alive "$pid" "$start" || return 0
        sleep 0.2
    done
    return 1
}

signal_owned_pid() {
    local pid="$1" start="$2" sig="$3" privileged="${4:-0}"
    pid_is_owned_alive "$pid" "$start" || return 0
    if [[ "$privileged" == 1 ]]; then
        sudo kill -"$sig" "$pid" 2>/dev/null || true
    else
        kill -"$sig" "$pid" 2>/dev/null || true
    fi
}

stop_owned_pid() {
    local pid="$1" start="$2" privileged="${3:-0}"
    [[ -n "$pid" && -n "$start" ]] || return 0
    signal_owned_pid "$pid" "$start" TERM "$privileged"
    if ! wait_for_exit "$pid" "$start" 20; then
        signal_owned_pid "$pid" "$start" KILL "$privileged"
        wait_for_exit "$pid" "$start" 10 || true
    fi
}

read_topology_identity() {
    local state
    [[ -r "$TOPO_PID_FILE" ]] || return 1
    IFS=: read -r TOPO_PID TOPO_START state < "$TOPO_PID_FILE" || return 1
    [[ "$TOPO_PID" =~ ^[0-9]+$ && "$TOPO_START" =~ ^[0-9]+$ ]]
}

remove_our_topology_pid_file() {
    local pid start state
    [[ -r "$TOPO_PID_FILE" ]] || return 0
    IFS=: read -r pid start state < "$TOPO_PID_FILE" || return 0
    if [[ "$pid" == "$TOPO_PID" && "$start" == "$TOPO_START" ]]; then
        rm -f -- "$TOPO_PID_FILE"
    fi
}

cleanup() {
    local exit_code=$?
    (( CLEANING )) && return "$exit_code"
    CLEANING=1
    trap - EXIT INT TERM
    echo "Shutting down NETWATCH services..."

    # Give the topology a chance to run its finally block and net.stop().
    if read_topology_identity && pid_is_owned_alive "$TOPO_PID" "$TOPO_START"; then
        echo "Stopping owned Mininet topology (PID $TOPO_PID)..."
        signal_owned_pid "$TOPO_PID" "$TOPO_START" TERM 1
        if ! wait_for_exit "$TOPO_PID" "$TOPO_START" 30; then
            signal_owned_pid "$TOPO_PID" "$TOPO_START" KILL 1
            wait_for_exit "$TOPO_PID" "$TOPO_START" 10 || true
        fi
        remove_our_topology_pid_file
    fi
    if [[ -n "$TOPO_LAUNCH_PID" ]]; then
        stop_owned_pid "$TOPO_LAUNCH_PID" "$TOPO_LAUNCH_START"
        wait "$TOPO_LAUNCH_PID" 2>/dev/null || true
    fi

    if [[ -n "$DASH_PID" ]]; then
        echo "Stopping owned Flask backend (PID $DASH_PID)..."
        stop_owned_pid "$DASH_PID" "$DASH_START"
        wait "$DASH_PID" 2>/dev/null || true
    fi
    if [[ -n "$RYU_PID" ]]; then
        echo "Stopping owned Ryu controller (PID $RYU_PID)..."
        stop_owned_pid "$RYU_PID" "$RYU_START"
        wait "$RYU_PID" 2>/dev/null || true
    fi
    echo "NETWATCH shutdown complete."
    return "$exit_code"
}

on_signal() {
    case "$1" in
        INT) exit 130 ;;
        TERM) exit 143 ;;
    esac
}
trap cleanup EXIT
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

mkdir -p -- "$RUNTIME_DIR"
chmod 700 -- "$RUNTIME_DIR"

# Refuse to start over active services; never clear state while a known listener is active.
command -v ss >/dev/null 2>&1 || fail "ss is required for service checks (install iproute2)."
if ss -ltn 2>/dev/null | grep -Eq '(:|\])6653[[:space:]]'; then
    fail "OpenFlow port 6653 is already in use."
fi
if ss -ltn 2>/dev/null | grep -Eq '(:|\])5000[[:space:]]'; then
    fail "Dashboard port 5000 is already in use."
fi
CONTROL_SOCKET="${NETWATCH_CONTROL_SOCKET:-/tmp/sdn_control.sock}"
export NETWATCH_CONTROL_SOCKET="$CONTROL_SOCKET"
if [[ -S "$CONTROL_SOCKET" ]]; then
    if python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.settimeout(.4); s.connect(sys.argv[1]); s.close()' "$CONTROL_SOCKET" >/dev/null 2>&1; then
        fail "An active NETWATCH control socket already exists."
    fi
fi

echo "Starting NETWATCH from $BASE_DIR"

# The Ryu environment used by the existing deployment is preferred explicitly.
RYU_BIN="${NETWATCH_RYU_MANAGER:-}"
if [[ -z "$RYU_BIN" ]]; then
    for candidate in \
        "$HOME/netwatch-latest/ryu39-env/bin/ryu-manager" \
        "$BASE_DIR/ryu39-env/bin/ryu-manager" \
        "$BASE_DIR/ryu-env/bin/ryu-manager"; do
        if [[ -x "$candidate" ]]; then RYU_BIN="$candidate"; break; fi
    done
fi
if [[ -z "$RYU_BIN" ]] && command -v ryu-manager >/dev/null 2>&1; then
    RYU_BIN="$(command -v ryu-manager)"
fi
[[ -n "$RYU_BIN" && -x "$RYU_BIN" ]] || fail "Ryu manager not found; expected ~/netwatch-latest/ryu39-env/bin/ryu-manager."

RYU_PYTHON="$(dirname "$RYU_BIN")/python"
if [[ ! -x "$RYU_PYTHON" ]]; then RYU_PYTHON="$(dirname "$RYU_BIN")/python3"; fi
if [[ ! -x "$RYU_PYTHON" ]]; then RYU_PYTHON="$(command -v python3 || true)"; fi
[[ -n "$RYU_PYTHON" && -x "$RYU_PYTHON" ]] || fail "Could not locate the Python interpreter for Ryu."
if ! PYTHONPATH="$BASE_DIR" "$RYU_PYTHON" -c 'import controller.monitoring_controller' >/dev/null 2>&1; then
    fail "Ryu environment cannot import controller.monitoring_controller from this project."
fi

# Ensure the control-socket group and effective-GID launcher are available before starting services.
if ! getent group sdn-ipc >/dev/null 2>&1; then
    sudo groupadd sdn-ipc || fail "Could not create the sdn-ipc group."
fi
CURRENT_USER="$(id -un)"
if ! id -nG "$CURRENT_USER" | grep -qw sdn-ipc; then
    sudo usermod -aG sdn-ipc "$CURRENT_USER" || fail "Could not add $CURRENT_USER to sdn-ipc."
fi
SDN_IPC_GID="$(getent group sdn-ipc | cut -d: -f3)"
CURRENT_EGID="$(id -g)"
if ! command -v sg >/dev/null 2>&1 && [[ "$CURRENT_EGID" != "$SDN_IPC_GID" ]]; then
    fail "sg is unavailable and the current effective GID is not sdn-ipc."
fi

# A clean, inactive start may discard only NETWATCH's fixed snapshot/log outputs.
if [[ "${NETWATCH_PRESERVE_RUNTIME_STATE:-0}" != 1 ]]; then
    sudo rm -f -- /tmp/sdn_metrics.json /tmp/sdn_portstats.json /tmp/sdn_flows.json \
        /tmp/server_state.json /tmp/sdn_alerts.log /tmp/monitor_alerts.log \
        /tmp/sdn_controller.log /tmp/dashboard.log /tmp/server.log \
        /tmp/sdn_experiments.json /tmp/sdn_experiments.json.tmp
fi

echo "Starting Ryu controller..."
PYTHONPATH="$BASE_DIR" "$RYU_BIN" controller.monitoring_controller \
    >"$RUNTIME_DIR/controller.log" 2>&1 &
RYU_PID=$!
RYU_START="$(proc_starttime "$RYU_PID")" || fail "Could not track the Ryu process."

wait_for_port() {
    local port="$1" label="$2" pid="$3" start="$4" deadline=$((SECONDS + STARTUP_TIMEOUT))
    while (( SECONDS < deadline )); do
        pid_is_owned_alive "$pid" "$start" || fail "$label exited before becoming ready; see $RUNTIME_DIR/controller.log."
        if ss -ltn 2>/dev/null | grep -Eq "(:|\\])${port}[[:space:]]"; then return 0; fi
        sleep 0.2
    done
    fail "$label did not become ready on port $port within ${STARTUP_TIMEOUT}s."
}
wait_for_port 6653 "Ryu controller" "$RYU_PID" "$RYU_START"

echo "Starting Flask backend..."
if command -v sg >/dev/null 2>&1; then
    sg sdn-ipc -c "exec python3 '$BASE_DIR/dashboard.py'" >"$RUNTIME_DIR/dashboard.log" 2>&1 &
else
    python3 "$BASE_DIR/dashboard.py" >"$RUNTIME_DIR/dashboard.log" 2>&1 &
fi
DASH_PID=$!
DASH_START="$(proc_starttime "$DASH_PID")" || fail "Could not track the Flask process."
wait_for_port 5000 "Flask backend" "$DASH_PID" "$DASH_START"

echo "Starting Mininet topology..."

set +e
if [[ -t 0 ]]; then
    script -q -e -c "sudo env NETWATCH_TOPOLOGY_PID_FILE='$TOPO_PID_FILE' python3 '$BASE_DIR/topo/monitoring_topo.py'" /dev/null \
        2>&1 | tee "$RUNTIME_DIR/topology.log"
    pipeline_status=("${PIPESTATUS[@]}")
    topology_status=${pipeline_status[0]}
else
    sudo env NETWATCH_TOPOLOGY_PID_FILE="$TOPO_PID_FILE" python3 "$BASE_DIR/topo/monitoring_topo.py" \
        >"$RUNTIME_DIR/topology.log" 2>&1 &
    TOPO_LAUNCH_PID=$!
    TOPO_LAUNCH_START="$(proc_starttime "$TOPO_LAUNCH_PID" 2>/dev/null || true)"
    wait "$TOPO_LAUNCH_PID" 2>/dev/null
    topology_status=$?
    TOPO_LAUNCH_PID=""
    TOPO_LAUNCH_START=""
fi
set -e

if (( topology_status != 0 )); then
    fail "Mininet topology exited with status $topology_status; see $RUNTIME_DIR/topology.log."
fi
