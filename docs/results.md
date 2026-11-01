# Performance Evaluation - Project 6

## Environment
- Ubuntu VM, Mininet, Ryu Controller
- Topology: 4 agents (h1-h4), 1 server (h5), 1 congestion client (h1)
- Bottleneck: 5 Mbps between s1 and s2

## Scenario 1 - Normal Operation
- `pingall` result: 0% dropped.
- The UDP monitoring server receives telemetry on port 9999 (proven by `/tmp/monitor_alerts.log` showing `SERVER STARTED` and `STATUS` lines).
- Controller exports port stats to `/tmp/sdn_portstats.json`.

## Scenario 2 - Congestion
- **Test Setup:** Ran congestion test from h1 to h5, pushing ~5.1 Mbps of traffic through the 5 Mbps bottleneck.
- **Result:** SDN Controller accurately detected utilization > 80% on s1 port 5.
- **Evidence:** `/tmp/sdn_alerts.log` shows `HIGH-UTIL on s1-s2 bottleneck (dpid=1 port=5) util=0.99`.

## Static vs Dynamic Comparison
| Metric | Static (no SDN) | Dynamic (Ryu controller) |
|--------|-----------------|--------------------------|
| Detection | manual | automatic (3s poll) |
| Alert | none | /tmp/sdn_alerts.log |
| Accuracy | N/A | Targets specific bottleneck port |
