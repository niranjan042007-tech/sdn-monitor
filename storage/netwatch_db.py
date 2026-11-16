#!/usr/bin/env python3
"""SQLite Storage Engine for NETWATCH Network Intelligence & Analytics.

Provides thread-safe persistence using SQLite in WAL mode with busy timeout handling,
deduplicating insert methods, stateful incident lifecycle tracking, hourly rollups,
and safe retention pruning that guarantees preservation of ACTIVE incidents.
"""
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data',
    'netwatch.db'
)

# Default retention horizons
DEFAULT_RAW_RETENTION_HOURS = int(os.environ.get('NETWATCH_RAW_RETENTION_HOURS', '4'))
DEFAULT_COMPLETED_FLOW_HOURS = int(os.environ.get('NETWATCH_COMPLETED_FLOW_HOURS', '24'))
DEFAULT_ROLLUP_RETENTION_DAYS = int(os.environ.get('NETWATCH_ROLLUP_RETENTION_DAYS', '7'))
DEFAULT_INCIDENT_RETENTION_DAYS = int(os.environ.get('NETWATCH_INCIDENT_RETENTION_DAYS', '14'))


class NetwatchDB:
    """Embedded SQLite Storage Engine for NETWATCH."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Create a new configured SQLite connection."""
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # Configure WAL mode, busy timeout, and normal synchronous durability
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def connection(self):
        """Context manager yielding a transactional connection."""
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database schema, indices, and constraints."""
        with self.connection() as conn:
            # 1. High-Resolution Raw Time-Series
            conn.execute("""
            CREATE TABLE IF NOT EXISTS link_metrics_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                link_id TEXT NOT NULL,
                throughput_mbps REAL,
                utilization_pct REAL,
                tx_bytes INTEGER,
                measured BOOLEAN NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                CONSTRAINT uq_link_sample UNIQUE (link_id, timestamp)
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_link_ts ON link_metrics_history(link_id, timestamp);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS host_telemetry_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                host_id TEXT NOT NULL,
                vm_cpu_util REAL,
                vm_mem_util REAL,
                telemetry_loss_rate REAL,
                agent_status TEXT NOT NULL,
                CONSTRAINT uq_host_sample UNIQUE (host_id, timestamp)
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_host_ts ON host_telemetry_history(host_id, timestamp);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                dpid INTEGER NOT NULL,
                flow_id TEXT NOT NULL,
                switch TEXT NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                protocol TEXT NOT NULL,
                throughput_mbps REAL,
                dropped_mbps REAL,
                traverses_bottleneck BOOLEAN NOT NULL DEFAULT 0,
                share_of_bottleneck_pct REAL,
                is_mitigation BOOLEAN NOT NULL DEFAULT 0,
                measured BOOLEAN NOT NULL DEFAULT 0,
                CONSTRAINT uq_flow_sample UNIQUE (dpid, flow_id, timestamp)
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_ts ON flow_history(dpid, flow_id, timestamp);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_time ON flow_history(timestamp);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS completed_flow_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                removal_timestamp REAL NOT NULL,
                dpid INTEGER NOT NULL,
                flow_id TEXT NOT NULL,
                duration_sec REAL NOT NULL,
                packet_count INTEGER NOT NULL,
                byte_count INTEGER NOT NULL,
                avg_throughput_mbps REAL,
                reason TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_completed_ts ON completed_flow_events(removal_timestamp);")

            # 2. Incident Lifecycle State-Machine Table
            conn.execute("""
            CREATE TABLE IF NOT EXISTS incident_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                incident_id TEXT UNIQUE NOT NULL,
                event_type TEXT NOT NULL,
                target TEXT NOT NULL,
                state TEXT NOT NULL,
                severity TEXT NOT NULL,
                start_time REAL NOT NULL,
                mitigation_time REAL,
                end_time REAL,
                duration_sec REAL,
                time_to_mitigation_sec REAL,
                time_to_recovery_sec REAL,
                peak_value REAL NOT NULL,
                trigger_threshold REAL NOT NULL,
                description TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incident_state ON incident_events(state, event_type);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_incident_start ON incident_events(start_time);")

            # 3. Hourly Rollup Tables
            conn.execute("""
            CREATE TABLE IF NOT EXISTS hourly_link_rollups (
                hour_bucket INTEGER NOT NULL,
                link_id TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                avg_throughput_mbps REAL NOT NULL,
                max_throughput_mbps REAL NOT NULL,
                avg_utilization_pct REAL NOT NULL,
                max_utilization_pct REAL NOT NULL,
                congested_sample_count INTEGER NOT NULL,
                PRIMARY KEY (hour_bucket, link_id)
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS hourly_host_rollups (
                hour_bucket INTEGER NOT NULL,
                host_id TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                avg_vm_cpu REAL NOT NULL,
                max_vm_cpu REAL NOT NULL,
                avg_vm_mem REAL NOT NULL,
                max_vm_mem REAL NOT NULL,
                avg_telemetry_loss REAL NOT NULL,
                max_telemetry_loss REAL NOT NULL,
                PRIMARY KEY (hour_bucket, host_id)
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS hourly_flow_rollups (
                hour_bucket INTEGER NOT NULL,
                flow_id TEXT NOT NULL,
                switch TEXT NOT NULL,
                source TEXT NOT NULL,
                destination TEXT NOT NULL,
                protocol TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                avg_throughput_mbps REAL NOT NULL,
                max_throughput_mbps REAL NOT NULL,
                total_bytes INTEGER NOT NULL,
                traverses_bottleneck BOOLEAN NOT NULL,
                avg_bottleneck_share_pct REAL,
                PRIMARY KEY (hour_bucket, flow_id)
            );
            """)

            conn.execute("""
            CREATE TABLE IF NOT EXISTS hourly_incident_rollups (
                hour_bucket INTEGER NOT NULL PRIMARY KEY,
                total_incidents INTEGER NOT NULL,
                congestion_incident_count INTEGER NOT NULL,
                mitigation_applied_count INTEGER NOT NULL,
                vm_saturation_count INTEGER NOT NULL,
                total_downtime_sec REAL NOT NULL
            );
            """)

    # -------------------------------------------------------------------------
    # Deduplicating Insert Methods
    # -------------------------------------------------------------------------

    def insert_link_sample(
        self,
        timestamp: float,
        link_id: str,
        throughput_mbps: Optional[float],
        utilization_pct: Optional[float],
        tx_bytes: Optional[int],
        measured: bool,
        status: str,
    ) -> bool:
        """Insert a link metric sample. Returns True if inserted, False if duplicate ignored."""
        query = """
        INSERT OR IGNORE INTO link_metrics_history (
            timestamp, link_id, throughput_mbps, utilization_pct, tx_bytes, measured, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?);
        """
        with self.connection() as conn:
            cur = conn.execute(query, (
                timestamp, link_id, throughput_mbps, utilization_pct, tx_bytes, 1 if measured else 0, status
            ))
            return cur.rowcount > 0

    def insert_host_sample(
        self,
        timestamp: float,
        host_id: str,
        vm_cpu_util: Optional[float],
        vm_mem_util: Optional[float],
        telemetry_loss_rate: Optional[float],
        agent_status: str,
    ) -> bool:
        """Insert a host telemetry sample. Returns True if inserted, False if duplicate ignored."""
        query = """
        INSERT OR IGNORE INTO host_telemetry_history (
            timestamp, host_id, vm_cpu_util, vm_mem_util, telemetry_loss_rate, agent_status
        ) VALUES (?, ?, ?, ?, ?, ?);
        """
        with self.connection() as conn:
            cur = conn.execute(query, (
                timestamp, host_id, vm_cpu_util, vm_mem_util, telemetry_loss_rate, agent_status
            ))
            return cur.rowcount > 0

    def insert_flow_sample(
        self,
        timestamp: float,
        dpid: int,
        flow_id: str,
        switch: str,
        source: str,
        destination: str,
        protocol: str,
        throughput_mbps: Optional[float],
        dropped_mbps: Optional[float],
        traverses_bottleneck: bool,
        share_of_bottleneck_pct: Optional[float],
        is_mitigation: bool,
        measured: bool,
    ) -> bool:
        """Insert a flow sample. Returns True if inserted, False if duplicate ignored."""
        query = """
        INSERT OR IGNORE INTO flow_history (
            timestamp, dpid, flow_id, switch, source, destination, protocol,
            throughput_mbps, dropped_mbps, traverses_bottleneck, share_of_bottleneck_pct,
            is_mitigation, measured
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.connection() as conn:
            cur = conn.execute(query, (
                timestamp, dpid, flow_id, switch, source, destination, protocol,
                throughput_mbps, dropped_mbps, 1 if traverses_bottleneck else 0,
                share_of_bottleneck_pct, 1 if is_mitigation else 0, 1 if measured else 0
            ))
            return cur.rowcount > 0

    def insert_completed_flow(
        self,
        removal_timestamp: float,
        dpid: int,
        flow_id: str,
        duration_sec: float,
        packet_count: int,
        byte_count: int,
        avg_throughput_mbps: Optional[float],
        reason: str,
    ) -> bool:
        """Insert a completed flow removal record (EventOFPFlowRemoved)."""
        query = """
        INSERT INTO completed_flow_events (
            removal_timestamp, dpid, flow_id, duration_sec, packet_count, byte_count, avg_throughput_mbps, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """
        with self.connection() as conn:
            cur = conn.execute(query, (
                removal_timestamp, dpid, flow_id, duration_sec, packet_count, byte_count, avg_throughput_mbps, reason
            ))
            return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Incident Lifecycle State-Machine Methods
    # -------------------------------------------------------------------------

    def record_or_update_incident(
        self,
        event_type: str,
        target: str,
        severity: str,
        timestamp: float,
        current_value: float,
        trigger_threshold: float,
        description: str,
        mitigation_confirmed: bool = False,
    ) -> str:
        """Create a new ACTIVE incident or update an existing ACTIVE incident with peak and mitigation times."""
        with self.connection() as conn:
            # Check for existing ACTIVE incident on this event_type + target
            cur = conn.execute(
                "SELECT incident_id, start_time, peak_value, mitigation_time FROM incident_events "
                "WHERE event_type = ? AND target = ? AND state = 'ACTIVE' LIMIT 1;",
                (event_type, target)
            )
            row = cur.fetchone()

            if row:
                incident_id = row['incident_id']
                existing_peak = row['peak_value']
                existing_mit = row['mitigation_time']

                new_peak = max(existing_peak, current_value)
                new_mit = existing_mit
                if mitigation_confirmed and existing_mit is None:
                    new_mit = timestamp

                conn.execute(
                    "UPDATE incident_events SET peak_value = ?, mitigation_time = ? WHERE incident_id = ?;",
                    (new_peak, new_mit, incident_id)
                )
                return incident_id
            else:
                # Generate unique incident ID
                incident_id = f"INC-{event_type}-{target}-{int(timestamp)}"
                mit_time = timestamp if mitigation_confirmed else None
                conn.execute(
                    "INSERT INTO incident_events ("
                    "incident_id, event_type, target, state, severity, start_time, mitigation_time, "
                    "peak_value, trigger_threshold, description"
                    ") VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?);",
                    (incident_id, event_type, target, severity, timestamp, mit_time, current_value, trigger_threshold, description)
                )
                return incident_id

    def resolve_incident(
        self,
        event_type: str,
        target: str,
        end_timestamp: float,
        description_suffix: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve an active incident, computing duration and MTTR."""
        with self.connection() as conn:
            cur = conn.execute(
                "SELECT * FROM incident_events WHERE event_type = ? AND target = ? AND state = 'ACTIVE' LIMIT 1;",
                (event_type, target)
            )
            row = cur.fetchone()
            if not row:
                return None

            incident_id = row['incident_id']
            start_time = row['start_time']
            mitigation_time = row['mitigation_time']

            duration_sec = max(0.0, round(end_timestamp - start_time, 2))
            time_to_mitigation_sec = None
            if mitigation_time is not None:
                time_to_mitigation_sec = max(0.0, round(mitigation_time - start_time, 2))
                time_to_recovery_sec = max(0.0, round(end_timestamp - mitigation_time, 2))
            else:
                time_to_recovery_sec = duration_sec

            updated_desc = row['description']
            if description_suffix:
                updated_desc += f" | {description_suffix}"

            conn.execute(
                "UPDATE incident_events SET "
                "state = 'RESOLVED', end_time = ?, duration_sec = ?, "
                "time_to_mitigation_sec = ?, time_to_recovery_sec = ?, description = ? "
                "WHERE incident_id = ?;",
                (end_timestamp, duration_sec, time_to_mitigation_sec, time_to_recovery_sec, updated_desc, incident_id)
            )

            return {
                'incident_id': incident_id,
                'state': 'RESOLVED',
                'start_time': start_time,
                'end_time': end_timestamp,
                'duration_sec': duration_sec,
                'time_to_mitigation_sec': time_to_mitigation_sec,
                'time_to_recovery_sec': time_to_recovery_sec,
            }

    def get_active_incidents(self, event_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return all current ACTIVE incidents."""
        with self.connection() as conn:
            if event_type:
                cur = conn.execute(
                    "SELECT * FROM incident_events WHERE state = 'ACTIVE' AND event_type = ? ORDER BY start_time ASC;",
                    (event_type,)
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM incident_events WHERE state = 'ACTIVE' ORDER BY start_time ASC;"
                )
            return [dict(r) for r in cur.fetchall()]

    # -------------------------------------------------------------------------
    # Hourly Rollup Aggregation Engine
    # -------------------------------------------------------------------------

    def compute_hourly_rollups(self, closed_before_epoch: float) -> Dict[str, int]:
        """Aggregate closed hours into hourly rollups.
        Only hours strictly earlier than closed_before_epoch are summarized.
        Uses INSERT OR REPLACE to guarantee idempotency.
        """
        # Determine the latest closed hour bucket
        max_hour_bucket = (int(closed_before_epoch) // 3600) * 3600

        counts = {'links': 0, 'hosts': 0, 'flows': 0, 'incidents': 0}

        with self.connection() as conn:
            # 1. Link rollups
            cur_link = conn.execute("""
            INSERT OR REPLACE INTO hourly_link_rollups (
                hour_bucket, link_id, sample_count,
                avg_throughput_mbps, max_throughput_mbps,
                avg_utilization_pct, max_utilization_pct,
                congested_sample_count
            )
            SELECT
                (CAST(timestamp / 3600 AS INTEGER) * 3600) AS hour_bucket,
                link_id,
                COUNT(*) AS sample_count,
                ROUND(AVG(throughput_mbps), 3),
                ROUND(MAX(throughput_mbps), 3),
                ROUND(AVG(utilization_pct), 2),
                ROUND(MAX(utilization_pct), 2),
                SUM(CASE WHEN utilization_pct >= 80.0 THEN 1 ELSE 0 END)
            FROM link_metrics_history
            WHERE timestamp < ? AND measured = 1
            GROUP BY hour_bucket, link_id;
            """, (max_hour_bucket,))
            counts['links'] = cur_link.rowcount

            # 2. Host rollups
            cur_host = conn.execute("""
            INSERT OR REPLACE INTO hourly_host_rollups (
                hour_bucket, host_id, sample_count,
                avg_vm_cpu, max_vm_cpu,
                avg_vm_mem, max_vm_mem,
                avg_telemetry_loss, max_telemetry_loss
            )
            SELECT
                (CAST(timestamp / 3600 AS INTEGER) * 3600) AS hour_bucket,
                host_id,
                COUNT(*) AS sample_count,
                ROUND(AVG(vm_cpu_util), 2),
                ROUND(MAX(vm_cpu_util), 2),
                ROUND(AVG(vm_mem_util), 2),
                ROUND(MAX(vm_mem_util), 2),
                ROUND(AVG(telemetry_loss_rate), 4),
                ROUND(MAX(telemetry_loss_rate), 4)
            FROM host_telemetry_history
            WHERE timestamp < ?
            GROUP BY hour_bucket, host_id;
            """, (max_hour_bucket,))
            counts['hosts'] = cur_host.rowcount

            # 3. Flow rollups
            cur_flow = conn.execute("""
            INSERT OR REPLACE INTO hourly_flow_rollups (
                hour_bucket, flow_id, switch, source, destination, protocol,
                sample_count, avg_throughput_mbps, max_throughput_mbps,
                total_bytes, traverses_bottleneck, avg_bottleneck_share_pct
            )
            SELECT
                (CAST(timestamp / 3600 AS INTEGER) * 3600) AS hour_bucket,
                flow_id,
                switch,
                source,
                destination,
                protocol,
                COUNT(*) AS sample_count,
                ROUND(AVG(COALESCE(throughput_mbps, 0.0)), 3),
                ROUND(MAX(COALESCE(throughput_mbps, 0.0)), 3),
                ROUND(SUM(COALESCE(throughput_mbps, 0.0) * 1e6 * 3.0 / 8.0)),
                MAX(traverses_bottleneck),
                ROUND(AVG(share_of_bottleneck_pct), 2)
            FROM flow_history
            WHERE timestamp < ? AND measured = 1
            GROUP BY hour_bucket, flow_id;
            """, (max_hour_bucket,))
            counts['flows'] = cur_flow.rowcount

            # 4. Incident rollups
            cur_inc = conn.execute("""
            INSERT OR REPLACE INTO hourly_incident_rollups (
                hour_bucket, total_incidents,
                congestion_incident_count, mitigation_applied_count,
                vm_saturation_count, total_downtime_sec
            )
            SELECT
                (CAST(start_time / 3600 AS INTEGER) * 3600) AS hour_bucket,
                COUNT(*) AS total_incidents,
                SUM(CASE WHEN event_type = 'CONGESTION' THEN 1 ELSE 0 END),
                SUM(CASE WHEN mitigation_time IS NOT NULL THEN 1 ELSE 0 END),
                SUM(CASE WHEN event_type = 'VM_CPU_SATURATION' THEN 1 ELSE 0 END),
                ROUND(SUM(COALESCE(duration_sec, 0.0)), 2)
            FROM incident_events
            WHERE start_time < ?
            GROUP BY hour_bucket;
            """, (max_hour_bucket,))
            counts['incidents'] = cur_inc.rowcount

        return counts

    # -------------------------------------------------------------------------
    # Retention Pruning (ACTIVE Incidents Strictly Protected)
    # -------------------------------------------------------------------------

    def prune_expired_records(
        self,
        raw_retention_hours: int = DEFAULT_RAW_RETENTION_HOURS,
        completed_flow_retention_hours: int = DEFAULT_COMPLETED_FLOW_HOURS,
        rollup_retention_days: int = DEFAULT_ROLLUP_RETENTION_DAYS,
        incident_retention_days: int = DEFAULT_INCIDENT_RETENTION_DAYS,
        current_time: Optional[float] = None,
    ) -> Dict[str, int]:
        """Prune records past their retention thresholds.
        ACTIVE incidents are NEVER pruned, regardless of age.
        """
        now = current_time if current_time is not None else time.time()
        raw_cutoff = now - (raw_retention_hours * 3600)
        completed_flow_cutoff = now - (completed_flow_retention_hours * 3600)
        rollup_cutoff = now - (rollup_retention_days * 86400)
        incident_cutoff = now - (incident_retention_days * 86400)

        pruned = {}
        with self.connection() as conn:
            c1 = conn.execute("DELETE FROM link_metrics_history WHERE timestamp < ?;", (raw_cutoff,))
            pruned['link_metrics_history'] = c1.rowcount

            c2 = conn.execute("DELETE FROM host_telemetry_history WHERE timestamp < ?;", (raw_cutoff,))
            pruned['host_telemetry_history'] = c2.rowcount

            c3 = conn.execute("DELETE FROM flow_history WHERE timestamp < ?;", (raw_cutoff,))
            pruned['flow_history'] = c3.rowcount

            c4 = conn.execute("DELETE FROM completed_flow_events WHERE removal_timestamp < ?;", (completed_flow_cutoff,))
            pruned['completed_flow_events'] = c4.rowcount

            c5 = conn.execute("DELETE FROM hourly_link_rollups WHERE hour_bucket < ?;", (rollup_cutoff,))
            pruned['hourly_link_rollups'] = c5.rowcount

            c6 = conn.execute("DELETE FROM hourly_host_rollups WHERE hour_bucket < ?;", (rollup_cutoff,))
            pruned['hourly_host_rollups'] = c6.rowcount

            c7 = conn.execute("DELETE FROM hourly_flow_rollups WHERE hour_bucket < ?;", (rollup_cutoff,))
            pruned['hourly_flow_rollups'] = c7.rowcount

            c8 = conn.execute("DELETE FROM hourly_incident_rollups WHERE hour_bucket < ?;", (rollup_cutoff,))
            pruned['hourly_incident_rollups'] = c8.rowcount

            # CRITICAL: Prune ONLY RESOLVED incidents older than incident_cutoff. ACTIVE are NEVER pruned.
            c9 = conn.execute(
                "DELETE FROM incident_events WHERE start_time < ? AND state = 'RESOLVED';",
                (incident_cutoff,)
            )
            pruned['incident_events'] = c9.rowcount

        return pruned

    def checkpoint_wal(self):
        """Execute WAL checkpoint with TRUNCATE to reclaim journal disk space."""
        with self.connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    def get_storage_stats(self) -> Dict[str, Any]:
        """Return runtime disk footprint and row counts."""
        db_size = os.path.getsize(self.db_path) if os.path.exists(self.db_path) else 0
        wal_path = self.db_path + '-wal'
        wal_size = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
        shm_path = self.db_path + '-shm'
        shm_size = os.path.getsize(shm_path) if os.path.exists(shm_path) else 0

        counts = {}
        with self.connection() as conn:
            for tbl in [
                'link_metrics_history', 'host_telemetry_history', 'flow_history',
                'completed_flow_events', 'incident_events',
                'hourly_link_rollups', 'hourly_host_rollups', 'hourly_flow_rollups', 'hourly_incident_rollups'
            ]:
                cur = conn.execute(f"SELECT COUNT(*) FROM {tbl};")
                counts[tbl] = cur.fetchone()[0]

        return {
            'db_size_bytes': db_size,
            'wal_size_bytes': wal_size,
            'shm_size_bytes': shm_size,
            'total_size_bytes': db_size + wal_size + shm_size,
            'row_counts': counts,
        }
