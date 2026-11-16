# NETWATCH — Implementation Plan and Outcome

Work order was chosen by demo risk: anything that makes the dashboard show **wrong** data first, then UI honesty, then docs and tooling.
A backup of the uploaded project was taken before any change.

| Step | Item | Outcome |
|---|---|---|
| 1 | Audit + baseline test run (211 passed) | Done — AUDIT_REPORT.md |
| 2 | B1: experiment status served from the live topology; atomic state export with logged failures | Done; regression test fails on the old code, passes on the new |
| 3 | B2: isolate tests from `/tmp/sdn_experiments.json` | Done; `/tmp` stays clean after the suite |
| 4 | B3: clean start removes stale experiment state | Done |
| 5 | B4: `RECOVERY_TIMEOUT` state | Done |
| 6 | B5: UI lifecycle states (SUBMITTED / RUNNING / RECOVERING / RECOVERED / FAILED / TIMED OUT / UNKNOWN / UNVERIFIED) | Done; syntax-checked only |
| 7 | B6/B7: JSON 400 on bad `/trigger` body; corrected UI descriptions | Done |
| 8 | Screenshot + evidence tool `tools/capture_screenshots.py` | Done; the mechanics were dry-run against the live Flask backend; real capture needs the VM |
| 9 | Docs: README, ARCHITECTURE, API, TESTING, DEMO, evidence/*.md, FINAL_COMPLETION_REPORT | Done |
| 10 | Package + fresh-extract verification | Done — see FINAL_COMPLETION_REPORT.md |

**Deliberately not done:** a visual redesign of the dashboard (Phase 4). Without a running frontend it could not be checked in a browser, and untested restyling the day before a demo risks breaking working pages. The existing V0 dashboard (seen in the user's screenshot) already has the dark operations look.
