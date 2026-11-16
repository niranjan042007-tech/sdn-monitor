# NETWATCH — Audit Report (15 Nov 2026)

**Scope:** source as uploaded in `nwtwatchhhhh.zip`.
**Where:** a Linux sandbox with Python 3.13, Node 22 and headless Chromium/Playwright.
**What the sandbox could not do** (so these are *not* verified here):
- Mininet, Open vSwitch and Ryu are not installed.
- The npm registry is unreachable, so the Next.js frontend could not be installed, built or run.

## 1. What was already working (verified here)
| Area | Evidence |
|---|---|
| Backend test suite: 211 tests passed on the uploaded code (all 14 test files, including UDP, process-spawn and Unix-socket tests) | `evidence/test_output/pytest_full.txt` (now 216 after additions) |
| Every frontend `fetch()` URL exists in Flask, and each returns 200 with the expected top-level keys, even with no topology | `evidence/test_output/api_contract_live.txt` |
| No telemetry → `utilization: null`, `utilization_stale: true`, `data_stale: true` (no misleading 0 %) | same file |
| Input validation on `/trigger` (bad experiment, host or rate → 400 JSON) | same file |
| Topology not running → `/trigger` returns 503 "Control socket not found", not fake success | same file |
| Shell inputs are allow-listed before `h.cmd(...)` (hosts h1–h4, rates 5/8/10, fixed commands) | `topo/monitoring_topo.py` `_validate` |
| Flask binds `127.0.0.1`, `debug=False`; no hard-coded credentials or secrets found | grep in section 4 |
| Congestion attribution by top sender (fix from the previous round) is present | `controller/monitoring_controller.py` `S1_HOST_PORTS` |

## 2. Confirmed bugs (fixed in this round)
| ID | Severity | Description | Root cause | Files | Fix | Test | Status |
|---|---|---|---|---|---|---|---|
| B1 | **High** | A burst submitted from the UI (HTTP 200) never appeared in `/api/experiments/status`; the old history kept showing | (a) `/api/experiments/status` served `/tmp/sdn_experiments.json` **before** asking the live topology, with no freshness check. (b) The topology wrote that file through a fixed name `sdn_experiments.json.tmp` and swallowed `OSError`. A leftover `.tmp` owned by another user is blocked by Ubuntu `fs.protected_regular`, so every export fails silently and the file freezes | `dashboard.py`, `topo/monitoring_topo.py` | Ask the live topology over IPC first. Fall back to the file only with `source:"file", stale:true`. Write via `tempfile.mkstemp` and log failures | `test_experiment_status_source.py` (fails on the original code, passes now) | FIXED (code-level; confirm on VM) |
| B2 | **High** | Fake experiment history (PID 9999, `completed_at: 100`) appeared on the real dashboard | `test_experiments.py` and `test_recovery.py` write the **real** `/tmp/sdn_experiments.json`. Running pytest on the demo VM injected test records into the live UI (the likely source of the "19 history records") | tests | New `tests/conftest.py` gives every test its own private state file. Verified `/tmp` stays clean after the full suite | full suite + `ls /tmp` | FIXED |
| B3 | Medium | Stale experiment history survived restarts | The `run_system.sh` clean start removed every runtime file except `sdn_experiments.json(.tmp)` | `run_system.sh` | Added both files to the cleanup | `test_shell_integration.py`, `bash -n` | FIXED |
| B4 | Medium | "Recovered" vs "failed" could not be told apart from "never confirmed" | Stale or non-advancing snapshots were reported as `RECOVERY_FAILED` | `topo/monitoring_topo.py` | New final state `RECOVERY_TIMEOUT` when no check failed but 2 fresh passes were not seen within 15 s | `test_recovery.py` (2 assertions updated) | FIXED |
| B5 | Medium | UI showed HTTP 200 as success with no lifecycle | No SUBMITTED, UNKNOWN or UNVERIFIED states | `netwatch-prototype-pages.tsx`, `netwatch-api.ts` | SUBMITTED until the topology reports it; UNVERIFIED after 10 s; UNKNOWN when status is stale or the API fails; history shows RECOVERED / FAILED / TIMED OUT | TS syntax check only (see 5) | FIXED (untested in a browser) |
| B6 | Low | Empty or malformed POST `/trigger` body returned Flask's HTML 400 page | `request.json` raises on a bad body | `dashboard.py` | `get_json(silent=True)` and a JSON 400 | `TestTriggerBodyValidation` | FIXED |
| B7 | Low | The Experiment Center text said "Allocates 500 MB" and "iperf" | Text not updated after the backend changed | `netwatch-prototype-pages.tsx` | Text now matches the backend | review | FIXED |

## 3. Suspected / unverified (not fixed)
| ID | Item | Why it is unverified |
|---|---|---|
| S1 | The same fixed `*.tmp` pattern exists in `controller` (metrics, portstats, flows) and `server` (state) | Not touched: controller tests patch these exact calls. Low risk in practice because those writers run as the same user each time and `run_system.sh` deletes the files. Watch for frozen metrics on the VM |
| S2 | `/api/history/*?range=bogus` silently uses 1h | Harmless; documented |
| S3 | `next.config.mjs` has `typescript.ignoreBuildErrors: true` | Type errors would not fail the build. A full `tsc` needs `node_modules` (not possible here) |
| S4 | Incident Acknowledge is browser-session only | Stated on the page itself; the backend log is append-only |
| S5 | All 7 pages, mobile drawer, Topology Builder actions, charts | Need the running frontend: run `tools/capture_screenshots.py` on the VM |

## 4. Security review
- `grep -riE "password|secret|api_key|token ="` → no hits in project code.
- No `shell=True` or `os.system`.
- Mininet `h.cmd` strings are built only from allow-listed values.
- Control socket is restricted to the `sdn-ipc` group (`run_system.sh`).
- `.env.example` was added; it lists only the non-secret `NETWATCH_*` variables the code actually reads.

## 5. Verification limits (honest)
- **Frontend:** only a TypeScript syntax parse of all `.ts/.tsx` files was possible (`evidence/test_output/typescript_syntax.txt`). No build, no type check, no browser run.
- **SDN / network:** no Mininet run. Every real network test is **NOT TESTED** here.
