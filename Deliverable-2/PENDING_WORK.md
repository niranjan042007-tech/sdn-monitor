# NETWATCH — Pending Work (must be done on the Ubuntu VM)

None of these could be run in the build sandbox: it has no Mininet/OVS/Ryu, and the npm registry was blocked.

| # | Task | How | Done when |
|---|---|---|---|
| P1 | Install the frontend | `cd frontend && pnpm install` (or `npm install`) | `pnpm build` succeeds |
| P2 | Start the full system | `sudo ./run_system.sh`, then `cd frontend && pnpm dev` | Header shows **Connected**; 4 hosts reporting |
| P3 | **Capture the required screenshots** | `pip install playwright && python3 -m playwright install chromium`, then `python3 tools/capture_screenshots.py --run-burst` | `evidence/screenshots/01…15*.png`, `evidence/SCREENSHOT_REPORT.md` and `NETWATCH_DASHBOARD_SCREENSHOTS.zip` exist; open each PNG |
| P4 | Confirm fix B1 live | Trigger a micro-burst on h4 from the UI | Monitor shows SUBMITTED → RUNNING → RECOVERING; audit log gets a new `burst / h4` row with RECOVERED, FAILED or TIMED OUT |
| P5 | Real experiments (one at a time) | flood h3, cpu h2, mem h1, kill h2 (see DEMO_GUIDE) | Fill in `evidence/network_experiment_results.md` with real timestamps |
| P6 | Performance table | DEMO_GUIDE section 4 (static vs dynamic) | Measured numbers in the table |
| P7 | Re-run tests on the VM | `python3 -m pytest -q` | Expect 216 passed. The tests no longer touch `/tmp/sdn_experiments.json` |
| P8 | Optional | Turn off `ignoreBuildErrors` in `next.config.mjs`, then run `pnpm build` and fix any type errors | Clean typed build |
