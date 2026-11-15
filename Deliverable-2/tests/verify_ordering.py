#!/usr/bin/env python3
"""Verify structural ordering in run_system.sh:
  - PID variables initialised before cleanup definition
  - cleanup() defined before trap registration
  - trap registered before any background process starts
"""
import os
import sys

script_path = os.path.join(os.path.dirname(__file__), '..', 'run_system.sh')
lines = open(script_path, encoding='utf-8').read().splitlines()

markers = {}
for i, line in enumerate(lines, 1):
    s = line.strip()
    if s == 'RYU_PID=""' and 'ryu_pid_init' not in markers:
        markers['ryu_pid_init'] = i
    if s == 'DASH_PID=""' and 'dash_pid_init' not in markers:
        markers['dash_pid_init'] = i
    if s == 'cleanup() {' and 'cleanup_def' not in markers:
        markers['cleanup_def'] = i
    if 'trap cleanup EXIT' in s and 'trap_line' not in markers:
        markers['trap_line'] = i
    if 'RYU_PID=$!' in s and 'ryu_start' not in markers:
        markers['ryu_start'] = i
    if 'DASH_PID=$!' in s and 'dash_start' not in markers:
        markers['dash_start'] = i

print("Detected line markers:")
for k, v in sorted(markers.items(), key=lambda x: x[1]):
    print(f"  Line {v:3}: {k}")

errors = []
if 'ryu_pid_init' not in markers:
    errors.append("MISSING: RYU_PID=\"\" initialisation")
if 'dash_pid_init' not in markers:
    errors.append("MISSING: DASH_PID=\"\" initialisation")
if 'cleanup_def' not in markers:
    errors.append("MISSING: cleanup() definition")
if 'trap_line' not in markers:
    errors.append("MISSING: trap cleanup EXIT ...")
if 'ryu_start' not in markers:
    errors.append("MISSING: RYU_PID=$! assignment")
if 'dash_start' not in markers:
    errors.append("MISSING: DASH_PID=$! assignment")

if errors:
    for e in errors:
        print("ERROR:", e)
    sys.exit(1)

def check(cond, msg):
    if not cond:
        errors.append(msg)

check(markers['ryu_pid_init'] < markers['cleanup_def'],
      f"FAIL: RYU_PID init (line {markers['ryu_pid_init']}) must precede cleanup def (line {markers['cleanup_def']})")
check(markers['dash_pid_init'] < markers['cleanup_def'],
      f"FAIL: DASH_PID init (line {markers['dash_pid_init']}) must precede cleanup def (line {markers['cleanup_def']})")
check(markers['cleanup_def'] < markers['trap_line'],
      f"FAIL: cleanup def (line {markers['cleanup_def']}) must precede trap (line {markers['trap_line']})")
check(markers['trap_line'] < markers['ryu_start'],
      f"FAIL: trap (line {markers['trap_line']}) must precede Ryu start (line {markers['ryu_start']})")
check(markers['trap_line'] < markers['dash_start'],
      f"FAIL: trap (line {markers['trap_line']}) must precede dash start (line {markers['dash_start']})")

if errors:
    for e in errors:
        print("ERROR:", e)
    sys.exit(1)

print()
print("PASS: cleanup trap registered before any background process starts")
