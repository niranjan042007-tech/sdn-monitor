# NETWATCH — Fixes, Demo Guide and Viva Prep (Deliverable 2)

## 1. What was changed in this round

| # | Problem (confirmed in source) | Fix | File |
|---|---|---|---|
| 1 | Mitigation targeted the host with the **highest telemetry loss**, not the host that was actually flooding. An innocent host could be blocked. | The controller now measures each host's sending rate from OpenFlow **port stats on s1** (rx bytes on ports 1–4 = h1–h4) and blocks the **top sender** (must be > 1 Mbps). | `controller/monitoring_controller.py` |
| 2 | Mitigation needed `high utilisation AND telemetry loss > 5 %`. Flood traffic goes to UDP 5555, telemetry to UDP 9999, so loss often never appeared and **mitigation never fired**. | Trigger is now **utilisation > 80 % for 2 consecutive polls** (about 6 s) plus a positively identified heavy sender. Loss is still logged as supporting evidence. | same |
| 3 | Unknown host fell back to a hardcoded `10.0.0.1` (h1 always blamed). | Removed. An unknown host means no rule is installed. | same |
| 4 | Flapping: the DROP rule makes bottleneck utilisation fall, so the old code deleted the rule on the next poll, and the flood came back. | The rule is removed only when the link is uncongested **and** the blocked host's own sending rate is below 1 Mbps (the source really stopped). The 15 s hard timeout is kept as a safety net; if the flood is still running after it expires, the controller re-detects it and installs the rule again. | same |
| 5 | Memory stress used a fixed 500 MB, which never reaches the 85 % alert on a 10 GB VM. | The size is now calculated from `/proc/meminfo` to bring total RAM use to about 90 %, always leaving 400 MB free (safe). The 85 % threshold is unchanged. | `tests/memory_load_test.py`, `topo/monitoring_topo.py` |
| 6 | The microburst (2 Mbps for 5 s) could fall between 3 s polls and be averaged away. | The burst is now **3 Mbps for 6 s**. That covers at least 2 polls, is more than 3× the EMA, and stays below the 4 Mbps (80 %) congestion threshold, so it shows as a burst, not a flood. | `topo/monitoring_topo.py`, `dashboard.py` |

Metrics file `/tmp/sdn_metrics.json` now also exposes `mitigated_host` and `host_ingress_mbps` (per-host sending rate), so the attribution decision can be checked.

### Test status (run in the Linux sandbox, without Mininet/OVS/Ryu installed)
`python3 -m pytest -q` → **203 passed, 0 failed**.
Three controller tests were rewritten because they checked the old buggy rule (block the worst-loss host). They now check that the **top sender** is blocked even when another host has worse loss, and that **no one** is blocked when there is no heavy sender.

**Still needs to be verified in the Ubuntu VM** (not possible in the sandbox): live Mininet/OVS runs, `ovs-ofctl dump-flows`, real Ryu behaviour, the frontend, and the performance numbers in section 4.

---

## 2. Start / stop

```bash
cd ~/netwatch-codex
python3 -m pytest -q                 # should show 203 passed
sudo ./run_system.sh                 # starts Ryu + Mininet + agents + server + Flask (:5000)
cd frontend && pnpm dev              # dashboard on http://localhost:3000 (if not started by script)
```
Logs to keep open in another terminal:
```bash
tail -f /tmp/sdn_alerts.log /tmp/monitor_alerts.log
watch -n1 'sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep -E "priority=100|n_packets"'
```
Stop: `Ctrl+C` in the run_system terminal, then `sudo mn -c` if anything is left over.

---

## 3. Demo sequence (exact steps and what to expect)

Run the experiments from the dashboard's Experiments panel or with curl:
`curl -X POST localhost:5000/api/trigger -H 'Content-Type: application/json' -d '{"problem":"<p>","host":"h3","rate":"8"}'`
(Check the exact JSON field names with `grep -n "request.get_json\|\.get('" dashboard.py | head` if this curl gets a 400 error.)

1. **Topology / normal state**: in Mininet, `pingall` shows 0 % dropped. The dashboard shows h1–h4 telemetry, with CPU/MEM explicitly labelled as shared VM-wide readings.
2. **CPU stress** (`cpu`, h2): VM-wide CPU goes above the threshold and a single `SERVER-ALERT: high CPU on mininet-vm` appears. The alert is VM-level, not specific to h2.
3. **Memory stress** (`mem`, h1): `/tmp/h1_mem.log` shows the auto-sized MB. VM-wide memory goes above 85 %, a `high MEM on mininet-vm` alert fires, and the stress allocation is released after 20 s.
4. **Congestion + SDN** (`flood`, **h3**, rate 8). Pick h3 on purpose to show the attacker is not hardcoded.
   - `HIGH-UTIL on bottleneck ... util≈1.0`
   - about 6 s later: `INTEGRATION ALERT ... top sender h3 at ~8 Mbps. MITIGATING.` and `MITIGATION ACTIVE: Installed DROP rule for h3->h5`
   - `dump-flows s1` shows `priority=100,udp,nw_src=10.0.0.3,nw_dst=10.0.0.5,tp_dst=5555 actions=drop` with **n_packets increasing** (proof that traffic is really being dropped)
   - bottleneck utilisation falls and telemetry from h1/h2/h4 keeps flowing
   - flood stops (20 s), then `MITIGATION REMOVED` or `MITIGATION EXPIRED`, and the rule disappears from `dump-flows`
5. **Microburst** (`burst`, h4): the dashboard shows the incident `Traffic burst detected on s1-s2 ... exceeds 3x 60s EMA`, then `resolved`. No DROP rule is installed (correct, because utilisation stays below 80 %).
6. **Host failure** (`kill`, h2): h2 shows DOWN / host-silent incident after about 5 s, the other hosts stay OK, and h2 recovers when the agent restarts.
7. **History**: show the incidents page and SQLite-backed history.

---

## 4. Performance comparison: measure it, don't invent it

Run each case 3 times in the VM and fill in the averages.

**Static baseline** (controller detects only, no mitigation): temporarily comment out the `self._apply_mitigation(offender)` line, restart, run flood h3 rate 8.
**Dynamic**: normal code.

Measure during the flood from another host:
```bash
mininet> h5 iperf -s -u -p 6000 &
mininet> h2 iperf -c 10.0.0.5 -u -p 6000 -b 1M -t 20 -i 1   # victim throughput / loss
mininet> h2 ping -c 20 10.0.0.5                              # latency
```
Detection time = time of `MITIGATING` line minus flood start. Recovery time = time of `REMOVED`/`EXPIRED` line minus flood end (both are in `/tmp/sdn_alerts.log`).

| Metric | Static | Dynamic (SDN) |
|---|---|---|
| h2 iperf loss % during flood | | |
| h2 iperf throughput (Mbps) | | |
| h2 ping avg RTT (ms) | | |
| Bottleneck utilisation during flood | | |
| Detection time (s) | n/a | |
| Recovery time (s) | n/a | |

Expected trend (to be confirmed by measurement): static shows high victim loss and RTT for the whole flood; dynamic shows them only for about 6 s before the rule goes in.

---

## 5. Limitations (say these honestly)
- Detection uses **3 s port-stat polling**, so the reaction time is about 6 s (2 confirming polls). Bursts shorter than a poll can be missed.
- The mitigation rule is specific to the demo flood (UDP dst 5555 to h5). A real system would match the offender's actual top flow.
- Attribution uses the host's s1 ingress port, which assumes **one host per access port**.
- The action is **drop/block**. There is no rerouting or QoS (the topology has only one path).
- Memory stress fills real VM RAM because Mininet hosts share the kernel. CPU/MEM values are VM-wide, not per-host isolated.

## 6. Likely viva questions
- **Why SDN here?** The controller sees all switch counters centrally and can change forwarding instantly with a FlowMod, with no per-device configuration.
- **How is congestion detected?** OFPPortStatsRequest every 3 s. Utilisation = Δbytes×8 / Δt / 5 Mbps on the s1↔s2 ports. Above 80 % for 2 polls means congested.
- **How do you know who caused it?** By the rx byte rate on each host's s1 port, which measures what each host is sending into the network. The highest sender above 1 Mbps is blamed. It is not based on telemetry loss (which only shows a victim) and not hardcoded.
- **Why priority 100?** It must beat the priority-1 L2 learning flows and the priority-0 table-miss rule, so matching packets hit the drop rule first.
- **What does an empty action list mean?** In OpenFlow 1.3, a flow with no actions drops the packet.
- **Why hard_timeout=15 and a cookie?** The hard timeout is a fail-safe so a stale block cannot stay forever. The cookie lets the controller delete exactly its own rule (`OFPFC_DELETE` with a cookie mask), and `OFPFF_SEND_FLOW_REM` tells the controller when the switch removed it.
- **Why didn't the rule flap?** Removal checks that the offender's own sending rate fell, not just bottleneck utilisation (which the drop rule itself lowers).
- **Burst vs congestion?** A burst is a rate above 3× the 60 s EMA but below 80 % utilisation, so it is only alerted. Sustained > 80 % is congestion and gets mitigated.
- **Why UDP for telemetry?** It is lightweight and connectionless, and lost telemetry packets themselves become a loss metric.

---

## 7. Showing the seven dashboard pages (added 15 Nov)
Open http://localhost:3000 once the header shows **Connected**.

| Page | What to show | What to say |
|---|---|---|
| Command Center (`/`) | Bottleneck %, hosts reporting, mitigation state, alerts | Values come from `/api/status`. If data is stale, the header says "Stale data" and utilization shows "—", never a fake 0 % |
| Topology Builder | Add a host/switch, link them, Undo, Fit View, Export | This is a **local draft** stored in the browser. It does not change the running Mininet network. "Load live topology" reads `/api/topology` |
| Live Network | s1–s2 link utilization, flows table, top talkers | From OpenFlow port and flow stats (3 s polling). During a flood, the `priority=100` DROP rule appears under mitigation rules |
| Host Monitoring | h1–h4 cards, last-seen age, history chart | CPU and MEM are **VM-wide** (shared kernel); loss and heartbeat are per host |
| Network Intelligence | 15m/1h/3h/24h charts, incidents, anomalies | Read from SQLite history (raw samples + hourly rollups) |
| Experiment Center | Pick an experiment, Inject, watch the recovery monitor | States: SUBMITTED → RUNNING → RECOVERING → RECOVERED / FAILED / TIMED OUT. UNKNOWN or UNVERIFIED if the topology can't be reached. One experiment at a time |
| Incident Management | Alert list with source and time; Acknowledge | Acknowledge is browser-session only (the log is append-only) |

## 8. Capturing the screenshots for submission
With everything running:
```bash
pip install playwright && python3 -m playwright install chromium
python3 tools/capture_screenshots.py --run-burst
```
This produces `evidence/screenshots/01…15*.png`, `evidence/SCREENSHOT_REPORT.md` and `NETWATCH_DASHBOARD_SCREENSHOTS.zip`. It runs at most **one** 6-second micro-burst. Open every PNG before submitting.
