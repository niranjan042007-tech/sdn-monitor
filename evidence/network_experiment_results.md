# Network Experiment Results

**No real experiments were run here (no Mininet, OVS or Ryu in the build sandbox). Every row is NOT TESTED until filled in on the VM.**

| Test ID | Experiment | Trigger time | Detection (log line + time) | SDN action / `dump-flows s1` | Recovery time + final state | Status |
|---|---|---|---|---|---|---|
| NW-01 | Baseline (`pingall`, 4 hosts OK) | | | | | NOT TESTED |
| NW-02 | Flood h3, rate 8 | | | | | NOT TESTED |
| NW-03 | Micro-burst h4 | | | | | NOT TESTED |
| NW-04 | CPU stress h2 | | | | | NOT TESTED |
| NW-05 | Memory stress h1 | | | | | NOT TESTED |
| NW-06 | Kill agent h2 | | | | | NOT TESTED |
| NW-07 | Cleanup (`dump-flows` has no priority=100 rule; no stress processes left) | | | | | NOT TESTED |
