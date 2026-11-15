# Ubuntu 26.04.1 LTS — Setup and Compatibility Guide

> **Status:** Research-based assessment (static analysis + published sources).
> No live Ubuntu 26.04.1 install was available during this audit.
> Every compatibility claim is sourced and reasoned; none are marked "verified"
> unless actually tested on the target system.

---

## 1. Environment Baseline

| Property | Value |
|----------|-------|
| OS | Ubuntu 26.04.1 LTS ("Resolute Raccoon") |
| System Python | **Python 3.14.4** (default `python3`) |
| Virtualisation | Oracle VirtualBox |
| Kernel requirement | ≥ 4.0 (OVS / Mininet requirement) |

---

## 2. Python 3.14 — What Changed and Why It Matters

Python 3.14 (released October 2025) carries several breaking changes that
directly affect this project's dependency chain:

| Breaking change | Removed in | Impact on this project |
|----------------|-----------|------------------------|
| `imp` module removed | Python 3.11 | **Ryu 4.34 crashes on import** |
| `distutils` removed | Python 3.12 | **Ryu 4.34 install fails** |
| `asyncore` removed | Python 3.12 | **eventlet ≤ 0.33 crashes** |
| `eventlet` drops 3.14 | (eventlet team) | **Ryu's async core unavailable** |

> [!CAUTION]
> **Do not attempt `pip install ryu` or `pip install ryu4312` with the system
> `python3.14`.** Both will fail — either at install time (distutils missing)
> or at runtime (imp / asyncore missing / eventlet incompatible).

---

## 3. Compatibility Matrix

### 3.1 Ryu 4.34 (official PyPI)

| Python version | Status | Reason |
|----------------|--------|--------|
| 3.9 | ✅ Works | All deprecated modules still present |
| 3.10 | ✅ Works | distutils still present (deprecated) |
| 3.11 | ❌ Fails at runtime | `imp` removed |
| 3.12+ | ❌ Fails at install | `distutils` removed |
| **3.14.4** | ❌ Fails at install | `distutils` + `imp` + `asyncore` gone |

### 3.2 `ryu4312` community fork (github.com/olibrius/ryu4312)

| Python version | Status | Reason |
|----------------|--------|--------|
| 3.9–3.10 | ✅ Works | Fully patched |
| 3.11–3.12 | ✅ Works (tested by community) | Patches `imp`, `distutils`, eventlet |
| 3.13 | ⚠️ May work | Community reports partial success |
| **3.14.4** | ❌ Not confirmed working | eventlet does not support 3.14; no 3.14 test reports found |

> [!IMPORTANT]
> As of November 2026, no confirmed test report for `ryu4312` on Python 3.14
> was found. The eventlet project itself states Python 3.14 is **not supported**
> and they are planning retirement in favour of asyncio.

### 3.3 Mininet 2.3.x

| Concern | Status | Notes |
|---------|--------|-------|
| Ubuntu 26.04 apt package | ✅ Available | `sudo apt install mininet` |
| From-source install | ✅ Works with patch | Replace `pep8` → `pycodestyle` in `util/install.sh` |
| Python 3.14 interop | ✅ Works | Mininet uses subprocess/ctypes, not Python async |
| `TCLink` (bw/delay) | ✅ Works | Requires `tc` and `iproute2` (default on Ubuntu) |
| `OVSKernelSwitch` | ✅ Works | Requires `openvswitch-switch` apt package |
| sudo requirement | ✅ Required | Mininet must run as root |

> [!NOTE]
> Mininet itself is Python 3.14 compatible because it shells out to `ovs-vsctl`,
> `tc`, and `ip` — it does not use eventlet or any removed stdlib modules.

### 3.4 Open vSwitch (OVS)

| Property | Status |
|----------|--------|
| Ubuntu 26.04 apt | ✅ OVS ≥ 3.x available |
| OpenFlow 1.3 | ✅ Supported since OVS 2.3 |
| `openvswitch-switch` service | ✅ Standard systemd unit |

### 3.5 Flask

| Property | Status |
|----------|--------|
| `pip install flask` with python3.14 | ✅ Compatible |
| `render_template_string`, `jsonify`, `request` | ✅ No breaking changes |
| Runs `dashboard.py` on port 5000 | ✅ No issues expected |

### 3.6 Tkinter (`ui_app.py`)

| Property | Status |
|----------|--------|
| `sudo apt install python3-tk` | ✅ Available |
| Requires display | Needs GNOME/XFCE desktop in VirtualBox |
| Headless VM | ❌ Will fail without `DISPLAY` set or Xvfb |

---

## 4. Recommended Setup — Isolated Python 3.9 venv for Ryu

This approach:
- **Does not replace or downgrade** the system Python 3.14
- **Does not add a PPA** that could break other system packages
- Uses only standard Ubuntu tools (`python3-venv`, `python3.9`)
- Keeps Mininet on the system Python (which works fine with 3.14)

### Step 1 — Verify system Python

```bash
python3 --version
# Expected: Python 3.14.4
```

### Step 2 — Install Python 3.9 via pyenv (safest on Ubuntu 26.04)

The `deadsnakes` PPA may not package Python 3.9 for Ubuntu 26.04 due to
library conflicts with modern OpenSSL. Use `pyenv` instead — it compiles from
source and does not touch system packages:

```bash
# Install pyenv build dependencies
sudo apt install -y \
    build-essential curl libssl-dev zlib1g-dev \
    libbz2-dev libreadline-dev libsqlite3-dev \
    libffi-dev liblzma-dev tk-dev

# Install pyenv (user-level, no sudo)
curl https://pyenv.run | bash

# Add to ~/.bashrc (or ~/.profile)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'export PATH="$PYENV_ROOT/bin:$PATH"'  >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc
source ~/.bashrc

# Install Python 3.9.21 (latest 3.9 patch)
pyenv install 3.9.21
```

> [!NOTE]
> `pyenv install` compiles Python 3.9 from source. This takes 2–5 minutes on
> a typical VirtualBox VM. The result is completely isolated from `/usr/bin/python3`.

### Step 3 — Create the Ryu venv using Python 3.9

```bash
# Create a venv named 'ryu-env' in the project root
~/.pyenv/versions/3.9.21/bin/python3.9 -m venv ~/sdn-monitor/ryu-env

# Activate it
source ~/sdn-monitor/ryu-env/bin/activate

# Verify
python --version
# Expected: Python 3.9.21
```

### Step 4 — Install Ryu inside the venv

```bash
# Upgrade pip and install build tools first
pip install --upgrade pip setuptools wheel

# Install the community fork (patches distutils, imp, eventlet for 3.9+)
pip install git+https://github.com/olibrius/ryu4312.git

# Pin compatible eventlet version
pip install "eventlet==0.30.2" "dnspython>=2.0"
```

> [!WARNING]
> Verify the fork installs without errors before proceeding. If the GitHub URL
> becomes unavailable, use: `pip install ryu==4.34 eventlet==0.30.2`
> (this is the official PyPI version, Python 3.9 only).

### Step 5 — Run ryu-manager from the venv

```bash
# Activate the venv first
source ~/sdn-monitor/ryu-env/bin/activate

# Run the controller (note: PYTHONPATH=. is required for the package import)
PYTHONPATH=. ryu-manager controller.monitoring_controller
```

### Step 6 — Run Mininet and dashboard with the system Python

Mininet and Flask do **not** need the ryu venv:

```bash
# Terminal 2 — Mininet topology (uses system python3)
sudo python3 topo/monitoring_topo.py

# Terminal 3 — Flask dashboard (uses system python3)
pip install flask   # one-time, with system pip
python3 dashboard.py
```

---

## 5. Implications of the Isolated venv Approach

| Concern | Impact |
|---------|--------|
| ryu-manager uses Python 3.9 venv | ✅ Fully isolated; no system risk |
| Mininet uses system Python 3.14 | ✅ Compatible; Mininet does not need Ryu |
| Dashboard (Flask) uses system Python 3.14 | ✅ Compatible |
| Telemetry agents use system Python 3.14 | ✅ Compatible (stdlib only) |
| Controller import path | `PYTHONPATH=. ryu-manager controller.monitoring_controller` unchanged |
| Need to keep venv activated for ryu | Must `source ryu-env/bin/activate` before running ryu-manager |

---

## 6. What Has NOT Been Verified on Ubuntu 26.04.1

> [!CAUTION]
> The following items are **not verified** — they are recommendations based on
> published sources and static analysis only. Actual testing on Ubuntu 26.04.1
> must be performed to confirm them.

- [ ] `pyenv install 3.9.21` completes without error on Ubuntu 26.04.1
- [ ] `pip install git+https://github.com/olibrius/ryu4312.git` succeeds in a Python 3.9.21 venv
- [ ] `ryu-manager controller.monitoring_controller` starts without import errors
- [ ] `sudo apt install mininet` on Ubuntu 26.04.1 gives a working Mininet 2.3.x
- [ ] `sudo mn --test pingall` passes (OVS + kernel module loaded)
- [ ] `flask` installs cleanly with `pip` under Python 3.14.4
- [ ] The control socket IPC (`/tmp/sdn_control.sock`) works between the Flask process and the Mininet topology process
- [ ] `deadsnakes` PPA availability for Python 3.9 on Ubuntu 26.04.1

---

## 7. Quick Reference — Terminal Layout

```
Terminal 1 (ryu venv):
  source ~/sdn-monitor/ryu-env/bin/activate
  cd ~/sdn-monitor
  PYTHONPATH=. ryu-manager controller.monitoring_controller

Terminal 2 (topology, system python3):
  cd ~/sdn-monitor
  sudo python3 topo/monitoring_topo.py

Terminal 3 (dashboard, system python3):
  cd ~/sdn-monitor
  python3 dashboard.py

Terminal 4 (log monitor, optional):
  tail -f /tmp/sdn_alerts.log /tmp/monitor_alerts.log
```
