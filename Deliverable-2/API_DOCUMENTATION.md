# NETWATCH REST API (Flask, `http://127.0.0.1:5000`)

All responses are JSON. The frontend reaches these through the Next.js rewrites (`/api/*`, `/trigger`).
The status codes and keys below were checked against the running backend; see `evidence/test_output/api_contract_live.txt`.

| Method | Path | Params / body | Response keys | Errors |
|---|---|---|---|---|
| GET | `/api/status` | – | `utilization` (float or **null** when unavailable), `utilization_stale`, `telemetry_stale`, `data_stale`, `telemetry` {host: {cpu, mem, loss, status, last_seen}}, `alerts`, `countdowns`, `mitigation_active` | – |
| GET | `/api/topology` | – | `type`, `description`, `switches`, `hosts`, `links` | – |
| GET | `/api/network/links` | – | `links`, `timestamp`, `age_seconds`, `stale` | – |
| GET | `/api/network/flows` | – | `flows`, `flows_count`, `top_talkers`, `mitigation_rules`, `timestamp`, `age_seconds`, `stale` | – |
| GET | `/api/history/bottleneck` | `range` = 15m, 1h, 3h or 24h (anything else → 1h) | `link_id`, `points`, `point_count`, `capacity_mbps`, `threshold_pct`, `range`, `start_time`, `end_time` | – |
| GET | `/api/history/hosts` | `range`, `host` = all or h1–h4 | `hosts`, `vm_scope`, `range`, `start_time`, `end_time` | – |
| GET | `/api/history/flows/top` | `range`, `top` = 1–10 (invalid → 5) | `top_flows`, `flows_count`, … | – |
| GET | `/api/analytics/summary` | `range` | `metrics`, `storage`, … | – |
| GET | `/api/analytics/incidents` | `state`, `limit` = 1–100 | `incidents`, `count` | – |
| GET | `/api/analytics/anomalies` | `limit` = 1–50 | `anomalies`, `count` | – |
| GET | `/api/experiments/status` | – | `active` (record or null), `history` (newest first), **`source`** = live, file or unavailable, **`stale`** (bool) | – |
| POST | `/trigger` | JSON `{"problem": "flood"\|"congestion"\|"burst"\|"cpu"\|"mem"\|"kill", "host": "h1"–"h4", "rate": "5"\|"8"\|"10"}` (rate only for flood/burst) | `{"status": "triggered", "message"}` | 400 invalid or missing body/field; 409 another experiment active; 503 topology or control socket unavailable |

**Experiment record:** `exp_id`, `host`, `cmd`, `rate`, `duration`, `start_time`, `expiry`, `state` (RUNNING, RECOVERING, RECOVERED, RECOVERY_FAILED or RECOVERY_TIMEOUT), `child_pid`, `recovery_checks` {name: {passed, details, data_missing?}}, `failed_checks`, `error`, `completed_at`.

**Fixed durations:** burst 6 s (3 Mbps); flood, cpu, mem and kill 20 s.
