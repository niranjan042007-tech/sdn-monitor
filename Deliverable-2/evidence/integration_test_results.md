# Integration Test Results

| Test ID | Test | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| IT-01 | Every frontend fetch URL exists in Flask | 13 GET + POST /trigger | all present, 200 | PASS | test_output/api_contract_live.txt |
| IT-02 | No telemetry → no fake zeros | utilization null, stale true | `None`, `True`, `data_stale True` | PASS | same |
| IT-03 | `/trigger` validation | 400 for bad experiment/host/rate/body | 400 ×4 | PASS | same |
| IT-04 | `/trigger` with topology down | 503, no fake success | 503 "Control socket not found" | PASS | same |
| IT-05 | `/api/experiments/status` with topology down | stale/unavailable flagged | `source: unavailable, stale: true` | PASS | same |
| IT-06 | IPC ↔ topology experiment lifecycle | RUNNING → RECOVERING → final | covered with a mocked network in test_recovery / test_experiments / test_ipc_socket (real Unix socket) | PASS (mocked net) | test_output/pytest_full.txt |
| IT-07 | Full chain agent → server → Ryu → Flask → UI with real Mininet | live data in UI | – | NOT TESTED | needs the VM |
| IT-08 | Experiment from UI appears in the audit log (pending issue A) | new row with final state | – | NOT TESTED live; root cause fixed + unit-tested | PENDING_WORK P4 |
