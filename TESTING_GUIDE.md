# NETWATCH Testing Guide

## 1. Automated (any Linux with Python 3 + Flask + pytest)
```bash
python3 -m compileall -q agents controller server storage topo tests dashboard.py
python3 -m pytest -q                 # expect: 216 passed
```
- The suite does **not** need Mininet or Ryu; the controller and Mininet are mocked.
- It does use real UDP, Unix sockets and subprocesses.
- `tests/conftest.py` isolates the experiment state file, so running the tests on the demo VM no longer pollutes the live dashboard.

Key regression tests added in this round: `tests/test_experiment_status_source.py`
- the live IPC answer is preferred over the state file;
- a file fallback is flagged `stale`;
- the export survives a blocking leftover `.tmp`;
- `/trigger` rejects bad bodies with a JSON 400.

## 2. Frontend
```bash
cd frontend && pnpm install && pnpm build
```
Note: `ignoreBuildErrors` is on; run `npx tsc --noEmit` for a real type check.

## 3. Browser + screenshots (system running)
```bash
pip install playwright && python3 -m playwright install chromium
python3 tools/capture_screenshots.py              # UI only, no experiments
python3 tools/capture_screenshots.py --run-burst  # + ONE supervised 3 Mbps/6 s burst on h4
```
For every page, the script records console errors, failed requests (≥400 or network failure), horizontal overflow, Next.js dev-overlay presence, and the live backend state at capture time. Outputs go to `evidence/SCREENSHOT_REPORT.md` and `evidence/browser_console_log.json`.

## 4. Real network tests (VM only, one at a time)
Follow `DEMO_GUIDE.md` section 3. Record timestamps from `/tmp/sdn_alerts.log` and `/tmp/monitor_alerts.log`, and the `ovs-ofctl -O OpenFlow13 dump-flows s1` output, in `evidence/network_experiment_results.md`. Never loop real stress experiments.

## 5. Result vocabulary
PASS only with evidence. Otherwise use FAIL, BLOCKED or NOT TESTED.
