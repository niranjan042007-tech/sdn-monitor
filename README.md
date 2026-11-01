# Project 6 - Network Monitoring and Alert System

## Overview
This project uses Mininet, Ryu SDN, and UDP sockets to monitor network conditions.
- **Agents (h1-h4):** Send UDP telemetry (CPU, MEM) to the server.
- **Server (h5):** Receives telemetry and logs status every 5s.
- **Controller (Ryu):** Monitors switch port statistics and alerts on 5 Mbps bottlenecks.

## Topology
h1-h4 (s1) -- 5Mbps -- s2 (h5, h6)
Note: h6 is the spare host. Congestion is generated from h1.

## How to Run
Terminal 1 (Ryu):
`PYTHONPATH=. ryu-manager controller.monitoring_controller`

Terminal 2 (Mininet):
`sudo python3 topo/monitoring_topo.py`

Terminal 3 (Watch logs):
`tail -f /tmp/sdn_alerts.log /tmp/monitor_alerts.log`

Inside Mininet CLI:
`mininet> pingall`
`mininet> h1 python3 /home/<USER>/sdn-monitor/tests/congestion_test.py`
*(Note: Replace `<USER>` with your actual username)*

## File Structure
- `controller/`: Ryu SDN app (Port stats, bottleneck alert)
- `agents/`: UDP socket telemetry sender
- `server/`: UDP socket listener (logs loss, status)
- `tests/`: Unit tests (`test_telemetry.py`, `test_loss.py`)
