#!/usr/bin/env python3
"""Packaging tool: Bundles the SDN Monitor project for Ubuntu VirtualBox deployment.

Excludes Windows artifacts, git history, cache directories, and temporary files.
Sets executable permissions on Linux shell scripts and Python entry points.
"""
import os
import tarfile
import zipfile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_TAR = os.path.join(PROJECT_ROOT, "sdn-monitor-deploy.tar.gz")
OUTPUT_ZIP = os.path.join(PROJECT_ROOT, "sdn-monitor-deploy.zip")

EXCLUDE_DIRS = {
    '.git', '__pycache__', '.pytest_cache', 'ryu-env', '.idea', '.vscode', 'node_modules', '.next'
}

EXCLUDE_EXTS = {
    '.pyc', '.pyo', '.pyd', '.tmp', '.log'
}

EXCLUDE_FILES = {
    'sdn-monitor-deploy.tar.gz',
    'sdn-monitor-deploy.zip',
    'desktop.ini',
    'Thumbs.db'
}

EXECUTABLE_FILES = {
    'run_system.sh',
    'dashboard.py',
    'ui_app.py',
    'package_project.py',
    'agents/telemetry_agent.py',
    'controller/monitoring_controller.py',
    'server/monitoring_server.py',
    'topo/monitoring_topo.py',
    'tests/congestion_test.py',
    'tests/cpu_load_test.py',
    'tests/memory_load_test.py',
    'tests/test_loss.py',
    'tests/test_telemetry.py',
    'tests/test_ipc_socket.py',
    'tests/test_lifecycle.py',
    'tests/test_shell_integration.py',
    'tests/test_dashboard_api.py'
}

def should_exclude(rel_path):
    parts = rel_path.replace('\\', '/').split('/')
    for p in parts:
        if p in EXCLUDE_DIRS:
            return True
    base = os.path.basename(rel_path)
    if base in EXCLUDE_FILES or base.endswith('.zip') or base.endswith('.tar.gz'):
        return True
    _, ext = os.path.splitext(base)
    if ext in EXCLUDE_EXTS:
        return True
    return False

def make_tar():
    print(f"Creating {OUTPUT_TAR}...")
    file_count = 0
    with tarfile.open(OUTPUT_TAR, "w:gz") as tar:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in sorted(files):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT).replace('\\', '/')
                if should_exclude(rel_path):
                    continue

                arcname = f"sdn-monitor/{rel_path}"
                tarinfo = tar.gettarinfo(full_path, arcname=arcname)
                # Normalize ownership and set POSIX permissions
                tarinfo.uid = 1000
                tarinfo.gid = 1000
                tarinfo.uname = "mininet"
                tarinfo.gname = "mininet"
                if rel_path in EXECUTABLE_FILES or rel_path.endswith('.sh'):
                    tarinfo.mode = 0o755
                else:
                    tarinfo.mode = 0o644

                with open(full_path, "rb") as f:
                    tar.addfile(tarinfo, f)
                file_count += 1
    print(f"  Added {file_count} files to {os.path.basename(OUTPUT_TAR)}")

def make_zip(output_path=None):
    target_zip = output_path or OUTPUT_ZIP
    print(f"Creating {target_zip}...")
    file_count = 0
    with zipfile.ZipFile(target_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(PROJECT_ROOT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in sorted(files):
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, PROJECT_ROOT).replace('\\', '/')
                if should_exclude(rel_path):
                    continue

                arcname = f"sdn-monitor/{rel_path}"
                zinfo = zipfile.ZipInfo.from_file(full_path, arcname=arcname)
                # Ensure POSIX permissions are preserved in ZIP external attributes
                if rel_path in EXECUTABLE_FILES or rel_path.endswith('.sh'):
                    zinfo.external_attr = (0o100755) << 16
                else:
                    zinfo.external_attr = (0o100644) << 16

                with open(full_path, "rb") as f:
                    z.writestr(zinfo, f.read(), compress_type=zipfile.ZIP_DEFLATED)
                file_count += 1
    print(f"  Added {file_count} files to {os.path.basename(target_zip)}")

def main():
    make_tar()
    make_zip()
    print("\nDeployment packages ready!")
    print(f"1. Linux tarball: {OUTPUT_TAR}")
    print(f"2. Windows/VM zip: {OUTPUT_ZIP}")
    print("\nInstructions for Ubuntu VM:")
    print("  tar -xzf sdn-monitor-deploy.tar.gz")
    print("  cd sdn-monitor")
    print("  chmod +x run_system.sh")
    print("  ./run_system.sh")

if __name__ == '__main__':
    main()
