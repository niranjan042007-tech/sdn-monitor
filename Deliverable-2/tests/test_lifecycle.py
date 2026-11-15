#!/usr/bin/env python3
"""Startup/cleanup lifecycle real shell integration tests for run_system.sh.

Executes the actual Bash script in an isolated temporary environment.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import unittest
from test_shell_integration import (
    BASH_BIN,
    LINUX_PROC_AVAILABLE,
    test_bash_syntax,
    test_dashboard_failure_terminates_ryu,
    test_normal_shutdown_terminates_both,
    test_sigterm_handling,
    test_sigint_handling,
    test_ipc_socket_protection,
    test_import_failure_prevents_service_start,
    test_early_ryu_exit_prevents_backend_and_topology_start,
    test_repeated_startup_shutdown_cleans_owned_runtime_state,
)

class TestLifecycle(unittest.TestCase):
    @unittest.skipUnless(BASH_BIN is not None and LINUX_PROC_AVAILABLE,
                         "Requires Bash and Linux /proc process identity")
    def test_lifecycle_all(self):
        test_bash_syntax()
        test_dashboard_failure_terminates_ryu()
        test_normal_shutdown_terminates_both()
        test_sigterm_handling()
        test_sigint_handling()
        test_ipc_socket_protection()
        test_import_failure_prevents_service_start()
        test_early_ryu_exit_prevents_backend_and_topology_start()
        test_repeated_startup_shutdown_cleans_owned_runtime_state()

if __name__ == '__main__':
    if BASH_BIN is not None and LINUX_PROC_AVAILABLE:
        print("=" * 70)
        print("RUNNING LIFECYCLE TESTS (REAL SHELL INTEGRATION)")
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
        print("ALL LIFECYCLE TESTS PASSED")
        print("=" * 70)
    else:
        print("SKIP: bash not available in this environment")
    unittest.main()
