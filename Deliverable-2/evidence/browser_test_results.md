# Browser Test Results

**Status: BLOCKED in the build sandbox.** Chromium and Playwright were available, but the Next.js frontend could not be installed (npm registry unreachable). Without it, no screenshot here could honestly show the NETWATCH dashboard, so none were produced.

`tools/capture_screenshots.py` was dry-run against the live Flask backend only to prove it executes (13 captures, report and zip written). Those images showed Flask's legacy page, **not** the dashboard, and were discarded.

To produce the real evidence on the VM:
```bash
python3 tools/capture_screenshots.py --run-burst
```
This writes `evidence/screenshots/01-command-center-desktop.png … 09-experiment-center-mobile.png` (plus extras), `evidence/SCREENSHOT_REPORT.md` (browser, viewport, time, backend connection, live/stale data source, console errors, failed requests, overflow and dev overlay per screenshot) and `NETWATCH_DASHBOARD_SCREENSHOTS.zip`.
