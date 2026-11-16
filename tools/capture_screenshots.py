#!/usr/bin/env python3
"""Capture genuine screenshots of the RUNNING NETWATCH dashboard (Phase 12).

Run this on the Ubuntu VM AFTER `sudo ./run_system.sh` and the Next.js
frontend (http://localhost:3000) are up. Nothing here fabricates data: it
only opens the real app in headless Chromium, waits for real API responses,
and records what it saw (console errors, failed requests, overflow, API state)
into evidence/SCREENSHOT_REPORT.md.

Setup (once):
    pip install playwright        # add --break-system-packages on Ubuntu 24.04 if needed
    python3 -m playwright install chromium

Usage:
    python3 tools/capture_screenshots.py                 # 7 desktop + mobile pages, no experiments
    python3 tools/capture_screenshots.py --run-burst     # ALSO run ONE supervised 3 Mbps/6 s burst on h4
                                                         # and capture running -> recovering -> audit log

Output:
    evidence/screenshots/*.png
    evidence/SCREENSHOT_REPORT.md
    evidence/browser_console_log.json
    NETWATCH_DASHBOARD_SCREENSHOTS.zip   (project root)
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.request
import zipfile

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'evidence', 'screenshots')

PAGES = [
    ('01-command-center-desktop.png', 'Command Center', '/'),
    ('02-topology-builder-desktop.png', 'Topology Builder', '/topology-builder'),
    ('03-live-network-desktop.png', 'Live Network', '/live-network'),
    ('04-host-monitoring-desktop.png', 'Host Monitoring', '/host-monitoring'),
    ('05-network-intelligence-desktop.png', 'Network Intelligence', '/network-intelligence'),
    ('06-experiment-center-desktop.png', 'Experiment Center', '/experiment-center'),
    ('07-incident-management-desktop.png', 'Incident Management', '/incident-management'),
]
MOBILE = [
    ('08-command-center-mobile.png', 'Command Center', '/'),
    ('09-experiment-center-mobile.png', 'Experiment Center', '/experiment-center'),
    ('10-live-network-mobile.png', 'Live Network', '/live-network'),
    ('11-incident-management-mobile.png', 'Incident Management', '/incident-management'),
]


def api(base, path, method='GET', body=None, timeout=5):
    req = urllib.request.Request(base + path, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or 'null')
    except Exception as exc:  # noqa: BLE001 - record any failure verbatim
        return None, str(exc)


def backend_snapshot(backend):
    code, status = api(backend, '/api/status')
    _, exp = api(backend, '/api/experiments/status')
    hosts = None
    if isinstance(status, dict):
        hosts = status.get('hosts') or status.get('telemetry')
    return {
        'status_http': code,
        'connected': code == 200,
        'utilization': status.get('utilization') if isinstance(status, dict) else None,
        'utilization_stale': status.get('utilization_stale') if isinstance(status, dict) else None,
        'hosts_reported': sorted(hosts.keys()) if isinstance(hosts, dict) else None,
        'experiment_source': exp.get('source') if isinstance(exp, dict) else None,
        'experiment_active': (exp.get('active') or {}).get('state') if isinstance(exp, dict) else None,
    }


def settle(page, extra_ms=2500):
    page.wait_for_load_state('networkidle', timeout=20000)
    page.wait_for_timeout(extra_ms)  # let polling fetch real data at least once


def page_checks(page):
    return page.evaluate("""() => ({
        overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
        devOverlay: !!document.querySelector('nextjs-portal [data-nextjs-dialog], [data-nextjs-toast-errors]'),
        title: document.title,
    })""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--frontend', default='http://localhost:3000')
    ap.add_argument('--backend', default='http://127.0.0.1:5000')
    ap.add_argument('--run-burst', action='store_true',
                    help='run ONE supervised micro-burst on h4 to capture live experiment states')
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    rows, console_log = [], []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        browser_name = f'Chromium {browser.version} (Playwright, headless)'

        def shoot(fname, name, route, viewport, notes='', is_mobile=False, action=None):
            ctx = browser.new_context(viewport=viewport, device_scale_factor=1,
                                      is_mobile=is_mobile, has_touch=is_mobile)
            page = ctx.new_page()
            errs, failed = [], []
            page.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
            page.on('pageerror', lambda e: errs.append(f'pageerror: {e}'))
            page.on('requestfailed', lambda r: failed.append(f'{r.method} {r.url} {r.failure}'))
            page.on('response', lambda r: failed.append(f'{r.status} {r.url}') if r.status >= 400 else None)
            page.goto(args.frontend + route, wait_until='domcontentloaded', timeout=30000)
            settle(page)
            if action:
                action(page)
                page.wait_for_timeout(800)
            chk = page_checks(page)
            snap = backend_snapshot(args.backend)
            page.screenshot(path=os.path.join(OUT, fname), full_page=False)
            ctx.close()
            console_log.append({'file': fname, 'console_errors': errs, 'failed_requests': failed})
            rows.append({
                'file': fname, 'page': name, 'browser': browser_name,
                'viewport': f"{viewport['width']}x{viewport['height']}",
                'captured': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                'backend': snap, 'checks': chk, 'console_errors': len(errs),
                'failed_requests': len(failed), 'notes': notes,
            })
            print(f'[ok] {fname}  console_errors={len(errs)} failed_requests={len(failed)} overflowX={chk["overflowX"]}')

        desktop = {'width': 1440, 'height': 900}
        mobile = {'width': 390, 'height': 844}
        for f, n, r in PAGES:
            shoot(f, n, r, desktop, notes='Page as loaded after data polling')
        for f, n, r in MOBILE:
            shoot(f, n, r, mobile, notes='Mobile layout', is_mobile=True)

        # Mobile drawer open (proof the navigation works)
        def open_drawer(page):
            btn = page.locator('button.nw-mobile-menu').first
            if btn.count():
                btn.click()
        shoot('12-mobile-navigation-open.png', 'Mobile navigation drawer', '/', mobile,
              notes='Drawer opened via the header menu button',
              is_mobile=True, action=open_drawer)

        # Experiment Center: selection controls (UI only, nothing is submitted)
        def pick_burst(page):
            sel = page.locator('select').first
            if sel.count():
                try:
                    sel.select_option('burst')
                except Exception:
                    pass
        shoot('13-experiment-center-selection.png', 'Experiment Center - selection controls',
              '/experiment-center', desktop, notes='Micro-burst selected, NOT submitted', action=pick_burst)

        if args.run_burst:
            code, res = api(args.backend, '/api/experiments/status')
            if isinstance(res, dict) and res.get('active'):
                print('[skip] an experiment is already active; not starting another')
            else:
                code, res = api(args.backend, '/trigger', 'POST',
                                {'problem': 'burst', 'host': 'h4', 'rate': '8'}, timeout=10)
                print(f'[burst] trigger -> {code} {res}')
                t0 = time.time()
                seen = set()
                while time.time() - t0 < 60:
                    _, st = api(args.backend, '/api/experiments/status')
                    state = (st.get('active') or {}).get('state') if isinstance(st, dict) else None
                    if state in ('RUNNING', 'RECOVERING') and state not in seen:
                        seen.add(state)
                        shoot(f'14-experiment-{state.lower()}.png', f'Experiment Center - {state}',
                              '/experiment-center', desktop, notes=f'Live burst on h4, backend state {state}')
                    if isinstance(st, dict) and not st.get('active') and seen:
                        break
                    time.sleep(1)
                shoot('15-experiment-audit-log.png', 'Experiment Center - recovery audit log',
                      '/experiment-center', desktop, notes='After the burst completed; newest history row is the burst',
                      action=lambda pg: pg.mouse.wheel(0, 1400))
        browser.close()

    with open(os.path.join(ROOT, 'evidence', 'browser_console_log.json'), 'w') as f:
        json.dump(console_log, f, indent=2)

    lines = ['# NETWATCH Screenshot Evidence Report', '',
             'Generated automatically by `tools/capture_screenshots.py` from the running application.',
             'Backend columns are read from the live Flask API at capture time; nothing is simulated by this script.', '',
             '| File | Page | Browser | Viewport | Captured | Backend connected | Data source | Hosts reporting | Util (stale?) | Console errors | Failed requests | Overflow-X | Dev overlay | Notes |',
             '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        b = r['backend']
        src = 'LIVE (topology IPC)' if b['experiment_source'] == 'live' else (
            f"NOT LIVE ({b['experiment_source']})" if b['experiment_source'] else 'unknown')
        lines.append(
            f"| {r['file']} | {r['page']} | {r['browser']} | {r['viewport']} | {r['captured']} | "
            f"{'yes' if b['connected'] else 'NO (' + str(b['status_http']) + ')'} | {src} | "
            f"{', '.join(b['hosts_reported']) if b['hosts_reported'] else 'n/a'} | "
            f"{b['utilization']} ({b['utilization_stale']}) | {r['console_errors']} | {r['failed_requests']} | "
            f"{r['checks']['overflowX']} | {r['checks']['devOverlay']} | {r['notes']} |")
    lines += ['', 'Details of console errors / failed requests: `evidence/browser_console_log.json`.',
              '', '**Before submitting: open every PNG and confirm it is readable and shows no error page.**']
    with open(os.path.join(ROOT, 'evidence', 'SCREENSHOT_REPORT.md'), 'w') as f:
        f.write('\n'.join(lines) + '\n')

    zpath = os.path.join(ROOT, 'NETWATCH_DASHBOARD_SCREENSHOTS.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for n in sorted(os.listdir(OUT)):
            z.write(os.path.join(OUT, n), os.path.join('screenshots', n))
        z.write(os.path.join(ROOT, 'evidence', 'SCREENSHOT_REPORT.md'), 'SCREENSHOT_REPORT.md')
    print(f'\nReport: evidence/SCREENSHOT_REPORT.md\nZip:    {zpath}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
