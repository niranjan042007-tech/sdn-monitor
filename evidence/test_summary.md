# Test Summary — 2026-11-15T17:18:26+00:00
Environment: Linux build sandbox, Python 3.13.18, Node v22.23.4, headless Chrome. **Not available:** Mininet, Open vSwitch, Ryu, npm registry (so no frontend install).

| Layer | Executed | Passed | Failed | Not tested / blocked | Evidence |
|---|---:|---:|---:|---|---|
| Python compile (all backend modules) | all | all | 0 | – | test_output/python_compile.txt |
| TypeScript syntax parse (7 files) | 7 | 7 | 0 | Full type check BLOCKED (no node_modules) | test_output/typescript_syntax.txt |
| pytest unit + integration (14 + 1 new files) | 216 (+8 subtests) | 216 | 0 | 0 skipped | test_output/pytest_full.txt |
| Live API contract (Flask running, no topology) | 18 requests | 18 behaved as documented | 0 | – | test_output/api_contract_live.txt |
| Browser tests of the Next.js dashboard | 0 | – | – | **BLOCKED** (frontend could not be installed) | browser_test_results.md |
| Real Mininet experiments | 0 | – | – | **NOT TESTED** (no Mininet) | network_experiment_results.md |

The 3 UDP, process-spawn and Unix-socket tests that earlier needed sandbox escalation **were executed** here and passed (test_ipc_socket, test_shell_integration, test_lifecycle).
