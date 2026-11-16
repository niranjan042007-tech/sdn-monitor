# NETWATCH — Final Completion Report (15 Nov 2026)

## Read this first: what this delivery is and is not
This round was done in a build sandbox that has Python, Node and headless Chrome, but:
- **no Mininet / Open vSwitch / Ryu**, and
- **no access to the npm registry**, so the Next.js frontend could not be installed or run.

That means:
- **No dashboard screenshots are included.** Taking screenshots of anything other than the real running dashboard would be fake evidence. Instead, `tools/capture_screenshots.py` produces every required screenshot, the SCREENSHOT_REPORT.md and NETWATCH_DASHBOARD_SCREENSHOTS.zip automatically when run on the VM (about 2 minutes).
- Every real network test is **NOT TESTED** here.

## What was already working
- Backend: 211/211 tests passed on the uploaded code.
- Every API route the frontend calls exists and answers correctly.
- Stale or missing data is reported as `null`/stale, not as zero.
- Input validation and the concurrency lock (409) work.
- The previous round's SDN fixes (top-sender attribution, no flapping, auto-sized memory stress, 3 Mbps/6 s burst) are present.

## What was fixed (details in AUDIT_REPORT.md)
1. **Pending issue A, the "missing burst" in Experiment Center.** Two root causes:
   - The status API served a state file that could silently freeze. Writes to it failed without any error because of a fixed `.tmp` name plus Ubuntu `fs.protected_regular`. The API now asks the live topology first and flags any fallback as stale. Writes are safe and failures are logged.
   - **The test suite wrote fake experiment history into the live `/tmp/sdn_experiments.json`.** Running pytest on the VM put test records (PID 9999) on the real dashboard. Tests are now isolated.
2. A clean start now removes old experiment state.
3. New `RECOVERY_TIMEOUT` state, so "not confirmed" is separate from "failed".
4. The Experiment Center UI now distinguishes SUBMITTED / RUNNING / RECOVERING / RECOVERED / FAILED / TIMED OUT / UNKNOWN / UNVERIFIED. HTTP 200 is no longer shown as recovered.
5. `/trigger` answers a bad body with a JSON 400 instead of an HTML page.
6. The Experiment Center descriptions now match the backend (memory sizing, flood generator).

## What was added
- `tools/capture_screenshots.py`: the Phase 12 screenshot and evidence generator.
- 5 new regression tests and `tests/conftest.py`.
- Docs: README (rewritten), ARCHITECTURE, API_DOCUMENTATION, TESTING_GUIDE, DEMO_GUIDE §7–8, AUDIT_REPORT, PENDING_WORK, IMPLEMENTATION_PLAN, `.env.example`, `evidence/*.md`.

## Tested
| | Result |
|---|---|
| pytest | **216 passed / 0 failed / 0 skipped** (+8 subtests) |
| Python compile | pass |
| TS syntax (7 files) | pass |
| Live API contract (18 requests) | pass |
| Fresh-extract check of the ZIP | see the bottom of this file |

## Not verified (honest)
- Browser rendering of all 7 pages, desktop and mobile.
- `next build` / type check.
- The new Experiment Center UI states in a browser.
- All Mininet experiments, mitigation via `ovs-ofctl`, and performance numbers.

All of these are listed with exact commands in PENDING_WORK.md.

## Not done, and why
- **Phase 4 visual redesign.** It could not be seen or tested without the running frontend, and an unverified restyle the day before a demo risks breaking working pages. The current V0 dark operations look (as in the user's screenshot) is kept.

## Readiness (evidence-based)
| Area | Readiness | Basis |
|---|---|---|
| Frontend | **Medium**. Code has been reviewed and its API contract verified, but it has not been built or rendered in this round | FE-02/03/06 blocked |
| Backend | **High** | 216 tests + live API checks |
| SDN controller | **Medium-High**. Logic is unit-tested; never run against real OVS here | NW-* not tested |
| Network experiments | **Unverified here** | needs the VM |
| Integration | **Medium**. IPC lifecycle tested with real sockets and a mocked network | IT-07/08 not tested |
| Testing | **High** for backend, **low** for UI/network | test_summary.md |
| Demo | **Ready after PENDING_WORK P1–P4 on the VM** (~15 min) | DEMO_GUIDE.md |

## Run it (Ubuntu VM)
```bash
cd ~/netwatch-codex
python3 -m pytest -q                       # 216 passed
sudo ./run_system.sh                       # terminal 1
cd frontend && pnpm install && pnpm dev    # terminal 2 → http://localhost:3000
python3 tools/capture_screenshots.py --run-burst   # terminal 3, after the header shows Connected
```

## Fresh-extract verification (2026-11-15T17:19:39+00:00)
The ZIP was extracted into an empty directory and checked:
- `compileall`: OK
- `bash -n run_system.sh`: OK
- **pytest: 216 passed, 0 failed**
- TS syntax: 4/4 component/lib files OK
- `tools/capture_screenshots.py` parses
- `python3 dashboard.py` started, and `GET /api/status` returned 200

Not possible in this environment: `pnpm install` (registry blocked) and Mininet start.

Excluded from the ZIP: node_modules, .next, __pycache__, .pytest_cache, *.bak, and the pre-fix backup folders and files (`netwatch_pre_*`, `dashboard_pre_incident_ui_*.py`, `DEMO_GUIDE_pre_vm_metrics_fix.md`). Lockfile included: `frontend/pnpm-lock.yaml`.

File manifest: `MANIFEST.txt`.
