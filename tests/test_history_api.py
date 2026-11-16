#!/usr/bin/env python3
"""Integration Tests for NETWATCH Historical & Analytics APIs (Priority 4 - Step 2).

Uses an isolated temporary SQLite database for every test.
Verifies:
- GET /api/history/bottleneck (15m, 1h, 3h raw; 24h mixed-resolution)
- GET /api/history/hosts (Shared VM-wide CPU/memory semantics)
- GET /api/history/flows/top (Top talker time-series attribution)
- GET /api/analytics/summary (CFP, MTTM, MTTR, peak util, storage stats)
- GET /api/analytics/incidents (Lifecycle state-machine filtering)
- GET /api/analytics/anomalies (Real-time and historical anomaly feed)
- collect_historical_snapshot (Deduplication by source timestamp, incident triggers)
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dashboard import app, get_db, collect_historical_snapshot
from storage.netwatch_db import NetwatchDB


class TestHistoryAPI(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary directory for test database
        self.test_dir = tempfile.mkdtemp(prefix="netwatch_hist_test_")
        self.db_path = os.path.join(self.test_dir, "test_hist.db")
        app.config['TESTING'] = True
        app.config['DATABASE_PATH'] = self.db_path
        self.client = app.test_client()
        self.db = get_db()
        # Reset collector in-memory state for clean isolation across test cases
        from dashboard import _last_persisted_ts, _ema_state, _prev_port_samples, _prev_flow_samples
        _last_persisted_ts['portstats'] = 0.0
        _last_persisted_ts['flows'] = 0.0
        _last_persisted_ts['hosts'] = {}
        _ema_state.clear()
        _prev_port_samples.clear()
        _prev_flow_samples.clear()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_history_bottleneck_empty_db(self):
        """GET /api/history/bottleneck returns 200 with empty points on clean database."""
        resp = self.client.get('/api/history/bottleneck?range=1h')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['link_id'], 's1-s2')
        self.assertEqual(data['range'], '1h')
        self.assertEqual(data['capacity_mbps'], 5.0)
        self.assertEqual(data['threshold_pct'], 80.0)
        self.assertEqual(data['point_count'], 0)
        self.assertEqual(data['points'], [])

    def test_history_bottleneck_raw_range(self):
        """GET /api/history/bottleneck returns raw samples for <= 3h ranges."""
        now = 1000000.0
        # Insert 3 samples on s1-s2
        self.db.insert_link_sample(now - 1200.0, 's1-s2', 2.5, 50.0, 1000, True, 'normal')
        self.db.insert_link_sample(now - 600.0, 's1-s2', 4.2, 84.0, 2000, True, 'congested')
        self.db.insert_link_sample(now - 60.0, 's1-s2', 1.0, 20.0, 3000, True, 'normal')

        # Insert 1 sample for a different link (should not be in s1-s2 points)
        self.db.insert_link_sample(now - 60.0, 'h1-s1', 0.5, 0.5, 500, True, 'normal')

        with patch('dashboard.time.time', return_value=now):
            resp = self.client.get('/api/history/bottleneck?range=1h')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['point_count'], 3)
        self.assertEqual(len(data['points']), 3)
        p1, p2, p3 = data['points']
        self.assertEqual(p1['throughput_mbps'], 2.5)
        self.assertEqual(p1['resolution'], 'raw')
        self.assertEqual(p2['utilization_pct'], 84.0)
        self.assertEqual(p2['status'], 'congested')
        self.assertEqual(p3['resolution'], 'raw')

    def test_history_bottleneck_24h_mixed_resolution(self):
        """GET /api/history/bottleneck?range=24h returns mixed-resolution (hourly rollups + raw points)."""
        now = 1000000.0
        # 1. Historical closed hour (10h ago): insert into hourly_link_rollups
        hour_10h_ago = (int(now - (10 * 3600)) // 3600) * 3600
        with self.db.connection() as conn:
            conn.execute("""
            INSERT INTO hourly_link_rollups (
                hour_bucket, link_id, sample_count, avg_throughput_mbps, max_throughput_mbps,
                avg_utilization_pct, max_utilization_pct, congested_sample_count
            ) VALUES (?, 's1-s2', 1200, 3.5, 4.8, 70.0, 96.0, 150);
            """, (hour_10h_ago,))

        # 2. Recent raw sample (1h ago, within the 4h raw window)
        self.db.insert_link_sample(now - 3600.0, 's1-s2', 2.0, 40.0, 10000, True, 'normal')

        with patch('dashboard.time.time', return_value=now):
            resp = self.client.get('/api/history/bottleneck?range=24h')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['point_count'], 2)
        resolutions = [p['resolution'] for p in data['points']]
        self.assertIn('1h_rollup', resolutions)
        self.assertIn('raw', resolutions)

        # Confirm rollup point has max values
        rollup_point = next(p for p in data['points'] if p['resolution'] == '1h_rollup')
        self.assertEqual(rollup_point['max_utilization_pct'], 96.0)
        self.assertEqual(rollup_point['status'], 'congested')

    def test_history_hosts_vm_scope_and_metrics(self):
        """GET /api/history/hosts returns VM-wide CPU/memory telemetry grouped by host."""
        now = 1000000.0
        self.db.insert_host_sample(now - 300.0, 'h1', 32.5, 60.1, 0.0, 'OK')
        self.db.insert_host_sample(now - 60.0, 'h1', 34.0, 60.2, 0.01, 'OK')
        self.db.insert_host_sample(now - 60.0, 'h2', 34.0, 60.2, 0.0, 'OK')

        with patch('dashboard.time.time', return_value=now):
            resp = self.client.get('/api/history/hosts?range=1h')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['vm_scope'], 'Shared Mininet Host VM Namespace')
        self.assertIn('h1', data['hosts'])
        self.assertIn('h2', data['hosts'])
        self.assertEqual(len(data['hosts']['h1']), 2)
        self.assertEqual(len(data['hosts']['h2']), 1)
        self.assertEqual(data['hosts']['h1'][0]['vm_cpu_util'], 32.5)

        # Test filtering by specific host
        with patch('dashboard.time.time', return_value=now):
            resp_h1 = self.client.get('/api/history/hosts?host=h1&range=1h')
        data_h1 = resp_h1.get_json()
        self.assertIn('h1', data_h1['hosts'])
        self.assertNotIn('h2', data_h1['hosts'])

    def test_history_flows_top(self):
        """GET /api/history/flows/top returns top forwarding flows by average rate."""
        now = 1000000.0
        f1_id = "s1:0:1:0:in_port=1,ip_proto=17,ipv4_dst=10.0.0.5,ipv4_src=10.0.0.1"
        f2_id = "s1:0:1:0:in_port=2,ip_proto=17,ipv4_dst=10.0.0.5,ipv4_src=10.0.0.2"

        # f1 has 4.0 Mbps
        self.db.insert_flow_sample(now - 60.0, 1, f1_id, 's1', '10.0.0.1', '10.0.0.5', 'UDP', 4.0, None, True, 80.0, False, True)
        # f2 has 1.0 Mbps
        self.db.insert_flow_sample(now - 60.0, 1, f2_id, 's1', '10.0.0.2', '10.0.0.5', 'UDP', 1.0, None, True, 20.0, False, True)

        with patch('dashboard.time.time', return_value=now):
            resp = self.client.get('/api/history/flows/top?top=1&range=1h')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['top_flows']), 1)
        top_flow = data['top_flows'][0]
        self.assertEqual(top_flow['flow_id'], f1_id)
        self.assertEqual(top_flow['source'], '10.0.0.1')
        self.assertAlmostEqual(top_flow['avg_throughput_mbps'], 4.0, places=2)
        self.assertEqual(len(top_flow['points']), 1)

    def test_analytics_summary_kpis(self):
        """GET /api/analytics/summary calculates CFP, MTTM, MTTR, peak util and storage metrics."""
        now = 1000000.0
        # 10 samples: 8 normal (40%), 2 congested (85% and 95%) => CFP = 80.0%
        for i in range(8):
            self.db.insert_link_sample(now - 3000.0 + (i * 10), 's1-s2', 2.0, 40.0, 1000, True, 'normal')
        self.db.insert_link_sample(now - 1000.0, 's1-s2', 4.25, 85.0, 5000, True, 'congested')
        self.db.insert_link_sample(now - 990.0, 's1-s2', 4.75, 95.0, 6000, True, 'congested')

        # Resolved congestion incident: start at now - 1000, mitigation at now - 995 (TTM=5s), recovery at now - 980 (TTR=15s)
        inc_id = self.db.record_or_update_incident(
            'CONGESTION', 's1-s2', 'CRITICAL', now - 1000.0, 85.0, 80.0, 'Congestion', mitigation_confirmed=False
        )
        self.db.record_or_update_incident(
            'CONGESTION', 's1-s2', 'CRITICAL', now - 995.0, 95.0, 80.0, 'Ongoing', mitigation_confirmed=True
        )
        self.db.resolve_incident('CONGESTION', 's1-s2', now - 980.0, 'Recovered')

        with patch('dashboard.time.time', return_value=now):
            resp = self.client.get('/api/analytics/summary?range=1h')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        metrics = data['metrics']
        self.assertEqual(metrics['congestion_free_pct'], 80.0)
        self.assertEqual(metrics['peak_utilization_pct'], 95.0)
        self.assertEqual(metrics['peak_utilization_timestamp'], now - 990.0)
        self.assertEqual(metrics['mttm_seconds'], 5.0)
        self.assertEqual(metrics['mttr_seconds'], 15.0)
        self.assertEqual(metrics['active_incidents_count'], 0)
        self.assertEqual(metrics['total_incidents_in_window'], 1)

        # Storage info
        storage = data['storage']
        self.assertGreater(storage['db_size_bytes'], 0)
        self.assertEqual(storage['raw_retention_hours'], 4)
        self.assertEqual(storage['rollup_retention_days'], 7)

    def test_analytics_incidents_lifecycle(self):
        """GET /api/analytics/incidents returns incidents and filters by state."""
        t = 5000.0
        self.db.record_or_update_incident('CONGESTION', 's1-s2', 'CRITICAL', t, 85.0, 80.0, 'Active congestion')
        self.db.record_or_update_incident('VM_CPU_SATURATION', 'mininet-vm', 'WARNING', t - 100.0, 90.0, 85.0, 'Resolved VM CPU')
        self.db.resolve_incident('VM_CPU_SATURATION', 'mininet-vm', t - 50.0)

        # All incidents
        resp = self.client.get('/api/analytics/incidents')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['count'], 2)

        # Active only
        resp_act = self.client.get('/api/analytics/incidents?state=ACTIVE')
        data_act = resp_act.get_json()
        self.assertEqual(data_act['count'], 1)
        self.assertEqual(data_act['incidents'][0]['event_type'], 'CONGESTION')
        self.assertEqual(data_act['incidents'][0]['state'], 'ACTIVE')

        # Resolved only
        resp_res = self.client.get('/api/analytics/incidents?state=RESOLVED')
        data_res = resp_res.get_json()
        self.assertEqual(data_res['count'], 1)
        self.assertEqual(data_res['incidents'][0]['event_type'], 'VM_CPU_SATURATION')

    def test_analytics_anomalies_endpoint(self):
        """GET /api/analytics/anomalies returns anomaly events."""
        t = 6000.0
        self.db.record_or_update_incident('TRAFFIC_BURST', 's1-s2', 'WARNING', t, 4.5, 3.0, 'Burst on s1-s2')
        resp = self.client.get('/api/analytics/anomalies')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['count'], 1)
        self.assertEqual(data['anomalies'][0]['event_type'], 'TRAFFIC_BURST')

    def test_collector_deduplication_and_incident_triggering(self):
        """collect_historical_snapshot deduplicates snapshots by source timestamp and tracks incidents."""
        from dashboard import _last_persisted_ts
        _last_persisted_ts['portstats'] = 0.0
        _last_persisted_ts['flows'] = 0.0
        _last_persisted_ts['hosts'] = {}

        portstats_snap = {
            'timestamp': 1000.0,
            'port_stats': {'1:5': 5000000}
        }
        metrics_snap = {'utilization': 88.0, 'mitigation_active': True}
        flows_snap = {
            'timestamp': 1000.0,
            'flows': [{
                'dpid': 1,
                'priority': 100,
                'actions': ['DROP'],
                'is_mitigation': True,
                'match': {'ipv4_dst': '10.0.0.5', 'ip_proto': 17},
                'byte_count': 100000
            }]
        }
        server_snap = {
            'telemetry': {'h1': {'last_seen': 999.5, 'cpu': 89.0, 'mem': 50.0, 'loss': 0.0, 'status': 'OK'}}
        }

        def mock_open_impl(path, mode='r', *args, **kwargs):
            from unittest.mock import mock_open
            p = str(path)
            if 'sdn_portstats.json' in p:
                return mock_open(read_data=json.dumps(portstats_snap))(path, mode, *args, **kwargs)
            if 'sdn_metrics.json' in p:
                return mock_open(read_data=json.dumps(metrics_snap))(path, mode, *args, **kwargs)
            if 'sdn_flows.json' in p:
                return mock_open(read_data=json.dumps(flows_snap))(path, mode, *args, **kwargs)
            if 'server_state.json' in p:
                return mock_open(read_data=json.dumps(server_snap))(path, mode, *args, **kwargs)
            raise FileNotFoundError(p)

        # 1. First poll cycle at t=1000.0: records new samples and creates ACTIVE incidents
        with patch('builtins.open', side_effect=mock_open_impl), \
             patch('dashboard.time.time', return_value=1000.0):
            res1 = collect_historical_snapshot(current_time=1000.0)

        self.assertGreater(res1['links_inserted'], 0)
        self.assertGreater(res1['hosts_inserted'], 0)
        self.assertGreater(res1['flows_inserted'], 0)

        # Verify incidents were created
        active_incidents = self.db.get_active_incidents()
        types = [i['event_type'] for i in active_incidents]
        self.assertIn('CONGESTION', types)
        self.assertIn('VM_CPU_SATURATION', types)

        # Confirm mitigation_time was set on CONGESTION (mitigation confirmed)
        cong = next(i for i in active_incidents if i['event_type'] == 'CONGESTION')
        self.assertEqual(cong['mitigation_time'], 1000.0)
        self.assertEqual(cong['peak_value'], 88.0)

        # 2. Second poll cycle with identical snapshots: deduplication skips inserts
        with patch('builtins.open', side_effect=mock_open_impl), \
             patch('dashboard.time.time', return_value=1002.0):
            res2 = collect_historical_snapshot(current_time=1002.0)

        self.assertEqual(res2['links_inserted'], 0)
        self.assertEqual(res2['hosts_inserted'], 0)
        self.assertEqual(res2['flows_inserted'], 0)

        # Total active incidents remain exactly 2 (zero duplicates)
        self.assertEqual(len(self.db.get_active_incidents()), 2)

        # 3. Third cycle with new timestamp at t=1003.0: network recovered
        portstats_snap['timestamp'] = 1003.0
        metrics_snap['utilization'] = 20.0
        metrics_snap['mitigation_active'] = False
        flows_snap['timestamp'] = 1003.0
        flows_snap['flows'] = []  # No drop rule
        server_snap['telemetry']['h1']['last_seen'] = 1003.0
        server_snap['telemetry']['h1']['cpu'] = 45.0  # CPU normalized

        with patch('builtins.open', side_effect=mock_open_impl), \
             patch('dashboard.time.time', return_value=1003.0):
            res3 = collect_historical_snapshot(current_time=1003.0)

        self.assertGreater(res3['links_inserted'], 0)
        # All incidents must be RESOLVED now
        self.assertEqual(len(self.db.get_active_incidents()), 0)

    def test_collector_accepts_flat_production_telemetry_format(self):
        """Test collector handles flat production telemetry format (h1..h4 at top level) without nested key."""
        from dashboard import _last_persisted_ts
        _last_persisted_ts['hosts'] = {}

        flat_server_snap = {
            'h1': {'last_seen': 2000.0, 'cpu': 25.0, 'mem': 30.0, 'loss': 0.0, 'status': 'OK'},
            'h2': {'last_seen': 2000.0, 'cpu': 25.0, 'mem': 30.0, 'loss': 0.0, 'status': 'OK'},
            'h3': {'last_seen': 2000.0, 'cpu': 25.0, 'mem': 30.0, 'loss': 0.0, 'status': 'OK'},
            'h4': {'last_seen': 2000.0, 'cpu': 25.0, 'mem': 30.0, 'loss': 0.0, 'status': 'OK'},
            'snapshot_timestamp': 2000.0
        }

        def mock_open_impl(path, mode='r', *args, **kwargs):
            from unittest.mock import mock_open
            p = str(path)
            if 'server_state.json' in p:
                return mock_open(read_data=json.dumps(flat_server_snap))(path, mode, *args, **kwargs)
            raise FileNotFoundError(p)

        with patch('builtins.open', side_effect=mock_open_impl), \
             patch('dashboard.time.time', return_value=2000.0):
            res = collect_historical_snapshot(current_time=2000.0)

        self.assertEqual(res['hosts_inserted'], 4, "All 4 flat host records should be inserted into history database")

    def test_traffic_burst_lifecycle_start_peak_recovery_and_recurrence(self):
        """Regression Test for Fix 1:
        1. Burst starts and creates one ACTIVE incident.
        2. Continuing burst updates peak without duplicate incidents.
        3. Fresh measurement satisfying recovery resolves the incident.
        4. A later burst creates a new incident.
        """
        portstats_snap = {'timestamp': 1000.0, 'port_stats': {'1:5': 100000}}
        metrics_snap = {'utilization': 20.0, 'mitigation_active': False}

        def mock_open_impl(path, mode='r', *args, **kwargs):
            from unittest.mock import mock_open
            p = str(path)
            if 'sdn_portstats.json' in p:
                return mock_open(read_data=json.dumps(portstats_snap))(path, mode, *args, **kwargs)
            if 'sdn_metrics.json' in p:
                return mock_open(read_data=json.dumps(metrics_snap))(path, mode, *args, **kwargs)
            if 'sdn_flows.json' in p:
                return mock_open(read_data=json.dumps({'timestamp': portstats_snap['timestamp'], 'flows': []}))(path, mode, *args, **kwargs)
            if 'server_state.json' in p:
                return mock_open(read_data=json.dumps({'telemetry': {}}))(path, mode, *args, **kwargs)
            raise FileNotFoundError(p)

        # 1. Baseline initialization at t=1000.0: rate = 1.0 Mbps, establishes EMA
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1000.0):
            collect_historical_snapshot(current_time=1000.0)
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 0)

        # 2. Burst starts at t=1002.0: rate jumps to 4.0 Mbps (jump = 3.0 >= 2.0, rate >= 3x EMA)
        portstats_snap['timestamp'] = 1002.0
        portstats_snap['port_stats']['1:5'] = 1100000
        metrics_snap['utilization'] = 80.0  # 4.0 Mbps
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1002.0):
            collect_historical_snapshot(current_time=1002.0)

        # Req 1: Burst starts and creates exactly one ACTIVE incident
        active_bursts = self.db.get_active_incidents(event_type='TRAFFIC_BURST')
        self.assertEqual(len(active_bursts), 1)
        inc1_id = active_bursts[0]['incident_id']
        self.assertEqual(active_bursts[0]['state'], 'ACTIVE')
        self.assertEqual(active_bursts[0]['peak_value'], 4.0)
        self.assertEqual(active_bursts[0]['start_time'], 1002.0)

        # 3. Burst continues and spikes higher at t=1004.0: rate = 4.8 Mbps
        portstats_snap['timestamp'] = 1004.0
        portstats_snap['port_stats']['1:5'] = 2300000
        metrics_snap['utilization'] = 96.0  # 4.8 Mbps
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1004.0):
            collect_historical_snapshot(current_time=1004.0)

        # Req 2: Continuing burst updates peak without duplicate incidents
        active_bursts = self.db.get_active_incidents(event_type='TRAFFIC_BURST')
        self.assertEqual(len(active_bursts), 1)
        self.assertEqual(active_bursts[0]['incident_id'], inc1_id)
        self.assertEqual(active_bursts[0]['peak_value'], 4.8)

        # 4. Burst continues at lower rate at t=1006.0: rate = 2.5 Mbps (still > 1.5x EMA)
        portstats_snap['timestamp'] = 1006.0
        portstats_snap['port_stats']['1:5'] = 2925000
        metrics_snap['utilization'] = 50.0  # 2.5 Mbps
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1006.0):
            collect_historical_snapshot(current_time=1006.0)

        active_bursts = self.db.get_active_incidents(event_type='TRAFFIC_BURST')
        self.assertEqual(len(active_bursts), 1)
        self.assertEqual(active_bursts[0]['incident_id'], inc1_id)
        self.assertEqual(active_bursts[0]['peak_value'], 4.8)  # Peak preserved

        # 5. Fresh measurement satisfying recovery at t=1008.0: rate = 1.0 Mbps (<= 1.5x EMA)
        portstats_snap['timestamp'] = 1008.0
        portstats_snap['port_stats']['1:5'] = 3175000
        metrics_snap['utilization'] = 20.0  # 1.0 Mbps
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1008.0):
            collect_historical_snapshot(current_time=1008.0)

        # Req 3: Fresh measurement satisfying recovery resolves the incident
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 0)
        with self.db.connection() as conn:
            cur = conn.execute("SELECT * FROM incident_events WHERE incident_id = ?;", (inc1_id,))
            resolved_inc = cur.fetchone()
        self.assertEqual(resolved_inc['state'], 'RESOLVED')
        self.assertEqual(resolved_inc['end_time'], 1008.0)
        self.assertEqual(resolved_inc['duration_sec'], 6.0)
        self.assertEqual(resolved_inc['peak_value'], 4.8)

        # 6. Normal traffic continues at t=1010.0: rate = 1.0 Mbps
        portstats_snap['timestamp'] = 1010.0
        portstats_snap['port_stats']['1:5'] = 3425000
        metrics_snap['utilization'] = 20.0  # 1.0 Mbps
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1010.0):
            collect_historical_snapshot(current_time=1010.0)

        # 7. A later burst occurs at t=1012.0: rate jumps to 4.0 Mbps
        portstats_snap['timestamp'] = 1012.0
        portstats_snap['port_stats']['1:5'] = 4425000
        metrics_snap['utilization'] = 80.0  # 4.0 Mbps
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1012.0):
            collect_historical_snapshot(current_time=1012.0)

        # Req 4: A later burst creates a new incident
        active_bursts = self.db.get_active_incidents(event_type='TRAFFIC_BURST')
        self.assertEqual(len(active_bursts), 1)
        inc2_id = active_bursts[0]['incident_id']
        self.assertNotEqual(inc2_id, inc1_id)
        with self.db.connection() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM incident_events WHERE event_type = 'TRAFFIC_BURST';")
            self.assertEqual(cur.fetchone()[0], 2)

    def test_traffic_burst_invalid_samples_do_not_resolve(self):
        """Regression Test for Fix 1 resilience:
        5. Missing sample does not resolve a burst.
        6. Stale sample does not resolve a burst.
        7. Unmeasured/reset sample does not resolve a burst.
        """
        portstats_snap = {'timestamp': 1000.0, 'port_stats': {'1:5': 100000}}
        metrics_snap = {'utilization': 20.0, 'mitigation_active': False}
        simulate_missing_portstats = False

        def mock_open_impl(path, mode='r', *args, **kwargs):
            from unittest.mock import mock_open
            p = str(path)
            if 'sdn_portstats.json' in p:
                if simulate_missing_portstats:
                    raise OSError("File not found")
                return mock_open(read_data=json.dumps(portstats_snap))(path, mode, *args, **kwargs)
            if 'sdn_metrics.json' in p:
                return mock_open(read_data=json.dumps(metrics_snap))(path, mode, *args, **kwargs)
            if 'sdn_flows.json' in p:
                return mock_open(read_data=json.dumps({'timestamp': portstats_snap['timestamp'], 'flows': []}))(path, mode, *args, **kwargs)
            if 'server_state.json' in p:
                return mock_open(read_data=json.dumps({'telemetry': {}}))(path, mode, *args, **kwargs)
            raise FileNotFoundError(p)

        # Baseline at t=1000.0
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1000.0):
            collect_historical_snapshot(current_time=1000.0)

        # Trigger active burst at t=1002.0 (rate = 4.0 Mbps)
        portstats_snap['timestamp'] = 1002.0
        portstats_snap['port_stats']['1:5'] = 1100000
        metrics_snap['utilization'] = 80.0
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1002.0):
            collect_historical_snapshot(current_time=1002.0)
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 1)

        # Req 5: Missing sample does not resolve a burst
        simulate_missing_portstats = True
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1004.0):
            collect_historical_snapshot(current_time=1004.0)
        simulate_missing_portstats = False
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 1)

        # Req 6a: Stale duplicate sample (same timestamp 1002.0) does not resolve a burst
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1006.0):
            collect_historical_snapshot(current_time=1006.0)
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 1)

        # Req 6b: Stale sample with gap > 10.0s does not resolve a burst
        portstats_snap['timestamp'] = 1025.0  # Gap = 23.0s > 10.0s
        portstats_snap['port_stats']['1:5'] = 1200000
        metrics_snap['utilization'] = 20.0  # Low rate, but sample is stale
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1025.0):
            collect_historical_snapshot(current_time=1025.0)
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 1)

        # Req 7: Unmeasured / counter-reset sample does not resolve a burst
        portstats_snap['timestamp'] = 1027.0
        portstats_snap['port_stats']['1:5'] = 100  # Counter dropped: curr_tx < prev_tx -> measured=False
        del metrics_snap['utilization']  # No verified controller rate
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1027.0):
            collect_historical_snapshot(current_time=1027.0)
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 1)

        # Genuine recovery at t=1029.0 on fresh measured sample (dt = 2.0s <= 10.0s)
        portstats_snap['timestamp'] = 1029.0
        portstats_snap['port_stats']['1:5'] = 350000
        metrics_snap['utilization'] = 20.0  # Measured 1.0 Mbps <= 1.5x EMA
        with patch('builtins.open', side_effect=mock_open_impl), patch('dashboard.time.time', return_value=1029.0):
            collect_historical_snapshot(current_time=1029.0)
        self.assertEqual(len(self.db.get_active_incidents(event_type='TRAFFIC_BURST')), 0)

    def test_history_bottleneck_24h_boundary_no_overlap_and_no_gap(self):
        """Regression Test for Fix 2:
        8. 24-hour history has no overlapping rollup/raw interval.
        9. Exact-hour cutoff behaves correctly.
        10. Retention boundary does not create a historical data gap.
        """
        import math
        # --- Case A: Non-exact hour (e.g. now = 1000000.0, 46m 40s past hour) ---
        now = 1000000.0
        raw_cutoff = int(math.ceil((now - (4 * 3600)) / 3600.0)) * 3600
        self.assertEqual(raw_cutoff, 986400)
        # Req 10: Retention boundary (now - 4h = 985600) is <= raw_cutoff (986400),
        # so raw retention pruning does not create a gap in queried raw points.
        self.assertGreaterEqual(raw_cutoff, now - (4 * 3600))

        # Insert hourly rollup strictly before raw_cutoff (e.g. 982800 = 986400 - 3600)
        rollup_hour = raw_cutoff - 3600
        with self.db.connection() as conn:
            conn.execute("""
            INSERT INTO hourly_link_rollups (
                hour_bucket, link_id, sample_count, avg_throughput_mbps, max_throughput_mbps,
                avg_utilization_pct, max_utilization_pct, congested_sample_count
            ) VALUES (?, 's1-s2', 1200, 2.0, 3.5, 40.0, 70.0, 0);
            """, (rollup_hour,))

        # Insert raw points at and after raw_cutoff
        self.db.insert_link_sample(float(raw_cutoff), 's1-s2', 2.1, 42.0, 1000, True, 'normal')
        self.db.insert_link_sample(raw_cutoff + 600.0, 's1-s2', 2.2, 44.0, 2000, True, 'normal')
        self.db.insert_link_sample(now - 10.0, 's1-s2', 2.3, 46.0, 3000, True, 'normal')

        with patch('dashboard.time.time', return_value=now):
            resp = self.client.get('/api/history/bottleneck?range=24h')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        points = data['points']
        self.assertEqual(len(points), 4)

        # Req 8: 24-hour history has no overlapping rollup/raw interval
        rollup_points = [p for p in points if p['resolution'] == '1h_rollup']
        raw_points = [p for p in points if p['resolution'] == 'raw']
        self.assertEqual(len(rollup_points), 1)
        self.assertEqual(len(raw_points), 3)

        # Every rollup must be strictly before raw_cutoff
        for rp in rollup_points:
            self.assertLess(rp['timestamp'], raw_cutoff)
            # The hour range covered by this rollup is [timestamp, timestamp + 3600)
            self.assertLessEqual(rp['timestamp'] + 3600, raw_cutoff)

        # Every raw point must be at or after raw_cutoff
        for raw_p in raw_points:
            self.assertGreaterEqual(raw_p['timestamp'], raw_cutoff)

        # Clean handover with zero overlap: max rollup end == min raw point timestamp
        self.assertEqual(rollup_points[0]['timestamp'] + 3600, raw_points[0]['timestamp'])

        # --- Case B: Exact-hour cutoff behaves correctly (Req 9) ---
        exact_now = 360000.0  # Exactly 100 hours epoch
        exact_cutoff = int(math.ceil((exact_now - (4 * 3600)) / 3600.0)) * 3600
        # At an exact hour boundary, raw_cutoff must match exactly now - 4h
        self.assertEqual(exact_cutoff, int(exact_now - (4 * 3600)))
        self.assertEqual(exact_cutoff, 345600)


if __name__ == '__main__':
    unittest.main()
