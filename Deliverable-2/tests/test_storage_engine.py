#!/usr/bin/env python3
"""Isolated Unit Tests for NETWATCH SQLite Storage Engine (Priority 4 - Step 1).

Uses temporary, isolated database files. Never touches production data.
Verifies WAL mode, busy timeout, deduplication, incident lifecycle,
hourly rollups, retention pruning, and the strict protection of ACTIVE incidents.
"""
import os
import shutil
import sys
import tempfile
import unittest

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from storage.netwatch_db import NetwatchDB


class TestStorageEngine(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary directory for test database
        self.test_dir = tempfile.mkdtemp(prefix="netwatch_db_test_")
        self.db_path = os.path.join(self.test_dir, "test_netwatch.db")
        self.db = NetwatchDB(self.db_path)

    def tearDown(self):
        # Clean up temporary test files
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_wal_initialization_and_pragmas(self):
        """Database initializes with WAL mode, busy_timeout=5000, and synchronous=NORMAL."""
        with self.db.connection() as conn:
            cur = conn.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0]
            self.assertEqual(journal_mode.lower(), "wal")

            cur = conn.execute("PRAGMA busy_timeout;")
            busy_timeout = cur.fetchone()[0]
            self.assertEqual(busy_timeout, 5000)

            cur = conn.execute("PRAGMA synchronous;")
            sync_mode = cur.fetchone()[0]
            # In SQLite: 0 = OFF, 1 = NORMAL, 2 = FULL, 3 = EXTRA
            self.assertEqual(sync_mode, 1)

    def test_link_sample_deduplication(self):
        """Inserting identical (link_id, timestamp) ignores duplicate without crashing or creating 2 rows."""
        ts = 1731234000.125
        # First insert succeeds
        res1 = self.db.insert_link_sample(
            timestamp=ts,
            link_id="s1-s2",
            throughput_mbps=4.25,
            utilization_pct=85.0,
            tx_bytes=5000000,
            measured=True,
            status="congested",
        )
        self.assertTrue(res1)

        # Duplicate insert on same link_id and timestamp is ignored
        res2 = self.db.insert_link_sample(
            timestamp=ts,
            link_id="s1-s2",
            throughput_mbps=4.25,
            utilization_pct=85.0,
            tx_bytes=5000000,
            measured=True,
            status="congested",
        )
        self.assertFalse(res2)

        # Different link at same timestamp succeeds
        res3 = self.db.insert_link_sample(
            timestamp=ts,
            link_id="h1-s1",
            throughput_mbps=1.10,
            utilization_pct=1.1,
            tx_bytes=1000000,
            measured=True,
            status="normal",
        )
        self.assertTrue(res3)

        # Confirm exactly 2 rows in table
        stats = self.db.get_storage_stats()
        self.assertEqual(stats['row_counts']['link_metrics_history'], 2)

    def test_host_sample_deduplication(self):
        """Inserting identical (host_id, timestamp) ignores duplicates."""
        ts = 1731234000.5
        res1 = self.db.insert_host_sample(
            timestamp=ts,
            host_id="h1",
            vm_cpu_util=24.5,
            vm_mem_util=48.2,
            telemetry_loss_rate=0.0,
            agent_status="OK",
        )
        self.assertTrue(res1)

        # Duplicate ignored
        res2 = self.db.insert_host_sample(
            timestamp=ts,
            host_id="h1",
            vm_cpu_util=24.5,
            vm_mem_util=48.2,
            telemetry_loss_rate=0.0,
            agent_status="OK",
        )
        self.assertFalse(res2)

        stats = self.db.get_storage_stats()
        self.assertEqual(stats['row_counts']['host_telemetry_history'], 1)

    def test_flow_sample_deduplication(self):
        """Inserting identical (dpid, flow_id, timestamp) ignores duplicate entries."""
        ts = 1731234000.75
        flow_id = "s1:0:1:0:in_port=1,ip_proto=17,ipv4_dst=10.0.0.5,ipv4_src=10.0.0.1"

        res1 = self.db.insert_flow_sample(
            timestamp=ts,
            dpid=1,
            flow_id=flow_id,
            switch="s1",
            source="10.0.0.1",
            destination="10.0.0.5",
            protocol="UDP",
            throughput_mbps=4.0,
            dropped_mbps=None,
            traverses_bottleneck=True,
            share_of_bottleneck_pct=95.0,
            is_mitigation=False,
            measured=True,
        )
        self.assertTrue(res1)

        # Duplicate ignored
        res2 = self.db.insert_flow_sample(
            timestamp=ts,
            dpid=1,
            flow_id=flow_id,
            switch="s1",
            source="10.0.0.1",
            destination="10.0.0.5",
            protocol="UDP",
            throughput_mbps=4.0,
            dropped_mbps=None,
            traverses_bottleneck=True,
            share_of_bottleneck_pct=95.0,
            is_mitigation=False,
            measured=True,
        )
        self.assertFalse(res2)

        stats = self.db.get_storage_stats()
        self.assertEqual(stats['row_counts']['flow_history'], 1)

    def test_incident_lifecycle_state_machine(self):
        """Incident transitions cleanly: ACTIVE creation -> peak update -> RESOLVED with accurate MTTR."""
        t0 = 1000.0
        # 1. Trigger congestion: creates single ACTIVE incident
        inc_id1 = self.db.record_or_update_incident(
            event_type="CONGESTION",
            target="s1-s2",
            severity="CRITICAL",
            timestamp=t0,
            current_value=82.0,
            trigger_threshold=80.0,
            description="Bottleneck utilization crossed 80%",
            mitigation_confirmed=False,
        )
        self.assertIn("INC-CONGESTION-s1-s2", inc_id1)

        # Verify state is ACTIVE
        active = self.db.get_active_incidents()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['state'], "ACTIVE")
        self.assertEqual(active[0]['peak_value'], 82.0)
        self.assertIsNone(active[0]['mitigation_time'])

        # 2. Next cycle at t0 + 3s: utilization increases to 95.5% and mitigation is confirmed
        inc_id2 = self.db.record_or_update_incident(
            event_type="CONGESTION",
            target="s1-s2",
            severity="CRITICAL",
            timestamp=t0 + 3.0,
            current_value=95.5,
            trigger_threshold=80.0,
            description="Bottleneck utilization ongoing",
            mitigation_confirmed=True,
        )
        # Must return the SAME incident_id without inserting a second row
        self.assertEqual(inc_id1, inc_id2)

        active = self.db.get_active_incidents()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]['peak_value'], 95.5)
        self.assertEqual(active[0]['mitigation_time'], t0 + 3.0)

        # 3. Third cycle at t0 + 15s: recovery confirmed (util < 75% and mitigation removed)
        resolved_info = self.db.resolve_incident(
            event_type="CONGESTION",
            target="s1-s2",
            end_timestamp=t0 + 15.0,
            description_suffix="Recovered after hard timeout expiry",
        )
        self.assertIsNotNone(resolved_info)
        self.assertEqual(resolved_info['state'], "RESOLVED")
        self.assertEqual(resolved_info['duration_sec'], 15.0)
        self.assertEqual(resolved_info['time_to_mitigation_sec'], 3.0)
        self.assertEqual(resolved_info['time_to_recovery_sec'], 12.0)  # 15s - 3s mitigation

        # No active incidents remain
        self.assertEqual(len(self.db.get_active_incidents()), 0)

    def test_hourly_rollups_idempotency(self):
        """Hourly rollups correctly aggregate closed hours and idempotent re-runs don't duplicate."""
        # Hour 1: 3600 to 7200
        # Insert 3 link samples in Hour 1
        self.db.insert_link_sample(3610.0, "s1-s2", 2.0, 40.0, 1000, True, "normal")
        self.db.insert_link_sample(3620.0, "s1-s2", 4.0, 80.0, 2000, True, "congested")
        self.db.insert_link_sample(3630.0, "s1-s2", 4.5, 90.0, 3000, True, "congested")

        # Run rollup for closed hour (e.g. at epoch 7500, which closes hour bucket 3600)
        counts = self.db.compute_hourly_rollups(closed_before_epoch=7500.0)
        self.assertEqual(counts['links'], 1)

        with self.db.connection() as conn:
            cur = conn.execute("SELECT * FROM hourly_link_rollups WHERE link_id = 's1-s2';")
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['hour_bucket'], 3600)
            self.assertEqual(row['sample_count'], 3)
            self.assertAlmostEqual(row['avg_throughput_mbps'], 3.5, places=2)
            self.assertAlmostEqual(row['max_throughput_mbps'], 4.5, places=2)
            self.assertAlmostEqual(row['avg_utilization_pct'], 70.0, places=1)
            self.assertAlmostEqual(row['max_utilization_pct'], 90.0, places=1)
            self.assertEqual(row['congested_sample_count'], 2)

        # Run rollup again: must overwrite cleanly without duplicate rows
        counts2 = self.db.compute_hourly_rollups(closed_before_epoch=7500.0)
        with self.db.connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM hourly_link_rollups;")
            total_rows = cur.fetchone()[0]
            self.assertEqual(total_rows, 1)

    def test_retention_pruning_protects_active_incidents(self):
        """Retention pruning deletes expired records and resolved incidents, but NEVER ACTIVE incidents."""
        t_now = 1000000.0
        # 1. Insert link samples: one very old (10h ago), one recent (1h ago)
        self.db.insert_link_sample(t_now - (10 * 3600), "s1-s2", 1.0, 20.0, 100, True, "normal")
        self.db.insert_link_sample(t_now - (1 * 3600), "s1-s2", 2.0, 40.0, 200, True, "normal")

        # 2. Insert incidents:
        # A. Very old RESOLVED incident (20 days ago) -> SHOULD be pruned
        inc_old_resolved = self.db.record_or_update_incident(
            event_type="CONGESTION",
            target="s1-s2",
            severity="CRITICAL",
            timestamp=t_now - (20 * 86400),
            current_value=90.0,
            trigger_threshold=80.0,
            description="Old resolved incident",
        )
        self.db.resolve_incident("CONGESTION", "s1-s2", t_now - (20 * 86400) + 20.0)

        # B. Very old ACTIVE incident (20 days ago) -> MUST BE PRESERVED!
        inc_old_active = self.db.record_or_update_incident(
            event_type="HOST_SILENT",
            target="h3",
            severity="CRITICAL",
            timestamp=t_now - (20 * 86400),
            current_value=1.0,
            trigger_threshold=0.5,
            description="Host h3 has been silent for 20 days and is still down",
        )

        # C. Recent ACTIVE incident (1h ago) -> MUST BE PRESERVED!
        inc_recent_active = self.db.record_or_update_incident(
            event_type="VM_CPU_SATURATION",
            target="mininet-vm",
            severity="WARNING",
            timestamp=t_now - 3600.0,
            current_value=88.0,
            trigger_threshold=85.0,
            description="VM CPU high",
        )

        # Execute pruning with raw_retention_hours=4 and incident_retention_days=14
        prune_results = self.db.prune_expired_records(
            raw_retention_hours=4,
            incident_retention_days=14,
            current_time=t_now
        )

        # Link samples: 1 old pruned, 1 recent kept
        self.assertEqual(prune_results['link_metrics_history'], 1)
        with self.db.connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM link_metrics_history;")
            self.assertEqual(cur.fetchone()[0], 1)

        # Incidents: only the old RESOLVED incident was pruned (1 pruned)
        self.assertEqual(prune_results['incident_events'], 1)

        # Check remaining incidents: exactly 2 incidents remain, and BOTH are ACTIVE!
        with self.db.connection() as conn:
            cur = conn.execute("SELECT incident_id, state FROM incident_events ORDER BY start_time ASC;")
            remaining = [dict(r) for r in cur.fetchall()]
            self.assertEqual(len(remaining), 2)
            self.assertEqual(remaining[0]['incident_id'], inc_old_active)
            self.assertEqual(remaining[0]['state'], "ACTIVE")
            self.assertEqual(remaining[1]['incident_id'], inc_recent_active)
            self.assertEqual(remaining[1]['state'], "ACTIVE")

    def test_wal_checkpoint_and_storage_stats(self):
        """Storage statistics return real file sizes and row counts, and WAL checkpoint truncates cleanly."""
        self.db.insert_link_sample(100.0, "s1-s2", 4.0, 80.0, 1000, True, "congested")
        stats = self.db.get_storage_stats()

        self.assertIn('db_size_bytes', stats)
        self.assertIn('wal_size_bytes', stats)
        self.assertIn('total_size_bytes', stats)
        self.assertGreater(stats['db_size_bytes'], 0)
        self.assertEqual(stats['row_counts']['link_metrics_history'], 1)

        # Checkpoint WAL
        self.db.checkpoint_wal()


if __name__ == '__main__':
    unittest.main()
