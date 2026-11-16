# Known Limitations
1. Port stats are polled every 3 s and mitigation needs 2 congested polls, so the reaction takes about 6 s. Bursts shorter than a poll can be missed.
2. The DROP rule matches the demo flood (UDP dst 5555 → h5) from the top sender, not arbitrary flows. There is no rerouting or QoS (the topology has a single path).
3. Attribution assumes one host per s1 access port (ports 1–4 = h1–h4).
4. CPU and memory telemetry is VM-wide (all Mininet hosts share one kernel). Memory stress really fills VM RAM up to about 90 %.
5. Incident acknowledgement is browser-session only.
6. Topology Builder is a local draft editor; it does not deploy to Mininet.
7. Controller and server snapshot files still use fixed `*.tmp` names (see AUDIT S1).
8. `next.config.mjs` ignores TypeScript build errors.
9. Verified only in a sandbox without Mininet or the frontend. See test_summary.md for what is NOT TESTED or BLOCKED.
