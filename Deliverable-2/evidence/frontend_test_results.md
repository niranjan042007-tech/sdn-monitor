# Frontend Test Results

| Test ID | Test | Expected | Actual | Status | Evidence |
|---|---|---|---|---|---|
| FE-01 | TS/TSX syntax of all 7 source files | parses | 7/7 OK | PASS | test_output/typescript_syntax.txt |
| FE-02 | `pnpm install` / `npm install` | installs | registry unreachable (ENOTFOUND registry.npmjs.org) | BLOCKED | build sandbox network policy |
| FE-03 | `next build` / `tsc --noEmit` | no errors | – | BLOCKED | needs FE-02 |
| FE-04 | API URL/method/params match the backend | match | all 12 calls in `lib/netwatch-api.ts` (11 GET + POST /trigger) map to existing Flask routes with the documented params | PASS (static + live backend) | API_DOCUMENTATION.md, api_contract_live.txt |
| FE-05 | Experiment lifecycle states rendered (new) | SUBMITTED/RUNNING/RECOVERING/RECOVERED/FAILED/TIMED OUT/UNKNOWN/UNVERIFIED | code added | NOT TESTED in a browser | netwatch-prototype-pages.tsx |
| FE-06 | Seven routes render, desktop + mobile | render, no console errors | – | NOT TESTED (run tools/capture_screenshots.py on the VM) | browser_test_results.md |
