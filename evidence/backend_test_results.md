# Backend Test Results
Full log: `test_output/pytest_full.txt` (216 passed, 0 failed, 0 skipped).

| Test ID | Test | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| BE-01 | Full pytest suite | all pass | 216 passed | PASS | test_output/pytest_full.txt |
| BE-02 | Live IPC status preferred over the state file | source=live, file not read | as expected | PASS | test_experiment_status_source.py::test_live_ipc_preferred_over_state_file |
| BE-03 | File fallback flagged | source=file, stale=true | as expected | PASS | ::test_file_fallback_is_marked_stale |
| BE-04 | Nothing available | source=unavailable, empty | as expected | PASS | ::test_nothing_available |
| BE-05 | Export survives a blocking leftover `.tmp` (bug B1) | new history row written | written; **fails on the original code** | PASS | ::test_export_ignores_unwritable_fixed_tmp |
| BE-06 | Bad `/trigger` bodies | JSON 400 | JSON 400 ×3 | PASS | ::test_bad_bodies_get_json_400 |
| BE-07 | Stale snapshots never yield RECOVERED | RECOVERY_TIMEOUT | RECOVERY_TIMEOUT | PASS | test_recovery.py (2 tests) |
| BE-08 | Test suite leaves the live `/tmp/sdn_experiments.json` untouched (bug B2) | file absent after run | absent | PASS | `ls /tmp` after the run (0 matches) |
| BE-09 | `run_system.sh` syntax and shutdown/cleanup | pass | 28 passed | PASS | test_shell_integration.py, test_lifecycle.py |
