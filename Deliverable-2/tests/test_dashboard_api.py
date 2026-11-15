#!/usr/bin/env python3
"""Tests for NETWATCH Dashboard API endpoints (Priority 1 validation)."""
import json
import os
import sys
import unittest
from unittest.mock import patch, mock_open

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from dashboard import (
    app,
    _PROB_TO_CMD,
    _prev_port_samples,
    _port_samples_lock,
    _prev_flow_samples,
    _flow_samples_lock,
)


def make_mock_open(file_dict):
    """Helper to mock builtins.open with a dictionary of path -> content or Exception."""
    def custom_open(path, mode='r', *args, **kwargs):
        path_str = str(path)
        for k, v in file_dict.items():
            if k in path_str:
                if isinstance(v, Exception):
                    raise v
                return mock_open(read_data=v)(path, mode, *args, **kwargs)
        raise FileNotFoundError(f"No mock configured for {path_str}")
    return custom_open


class TestDashboardAPI(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        self.client = app.test_client()
        with _port_samples_lock:
            _prev_port_samples.clear()
        with _flow_samples_lock:
            _prev_flow_samples.clear()

    def tearDown(self):
        with _port_samples_lock:
            _prev_port_samples.clear()
        with _flow_samples_lock:
            _prev_flow_samples.clear()

    def test_status_endpoint_returns_json(self):
        """GET /api/status returns expected structure even when temp files are missing."""
        resp = self.client.get('/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('utilization', data)
        self.assertIn('telemetry', data)
        self.assertIn('alerts', data)
        self.assertIn('countdowns', data)
        self.assertIn('mitigation_active', data)

    def test_status_marks_missing_sources_stale_instead_of_reporting_zero(self):
        with patch('builtins.open', side_effect=FileNotFoundError()):
            resp = self.client.get('/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsNone(data['utilization'])
        self.assertTrue(data['utilization_stale'])
        self.assertTrue(data['telemetry_stale'])
        self.assertTrue(data['data_stale'])

    def test_status_marks_old_controller_metrics_stale(self):
        now = 101.0
        file_dict = {
            '/tmp/sdn_metrics.json': json.dumps({
                'utilization': 0.85,
                'utilization_timestamp': 90.0,
                'snapshot_timestamp': 100.0,
            }),
            '/tmp/server_state.json': json.dumps({
                'h1': {'age': 0.5, 'last_seen': now - 0.5, 'status': 'OK'},
            }),
        }
        with patch('dashboard.time.time', return_value=now), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/status')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIsNone(data['utilization'])
        self.assertTrue(data['utilization_stale'])
        self.assertFalse(data['telemetry_stale'])
        self.assertTrue(data['data_stale'])

    def test_trigger_invalid_problem_rejected(self):
        """Invalid problem/experiment is rejected with 400."""
        resp = self.client.post('/trigger', json={'problem': 'invalid_attack', 'host': 'h1', 'rate': '8'})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'error')
        self.assertIn('Unknown problem/experiment', data.get('message', ''))

    def test_trigger_invalid_host_rejected(self):
        """Invalid host/target is rejected with 400."""
        resp = self.client.post('/trigger', json={'problem': 'flood', 'host': 'h99', 'rate': '8'})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'error')
        self.assertIn('Invalid host', data.get('message', ''))

    def test_trigger_invalid_rate_for_flood_rejected(self):
        """Invalid rate for flood is rejected with 400."""
        resp = self.client.post('/trigger', json={'problem': 'flood', 'host': 'h1', 'rate': '100'})
        self.assertEqual(resp.status_code, 400)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'error')
        self.assertIn('Invalid rate/intensity', data.get('message', ''))

    @patch('dashboard._send_control_command')
    def test_trigger_canonical_flood(self, mock_send):
        """Canonical backend contract triggers flood correctly."""
        mock_send.return_value = {'status': 'ok', 'message': 'flood started: 8 Mbps for 20s on h1'}
        resp = self.client.post('/trigger', json={'problem': 'flood', 'host': 'h1', 'rate': '8'})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with('flood', 'h1', rate='8', duration=20)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'triggered')

    @patch('dashboard._send_control_command')
    def test_trigger_frontend_congestion_mapping(self, mock_send):
        """Frontend contract mapping: 'congestion' maps to 'flood', 'target' maps to 'host', 'intensity' maps to 'rate'."""
        mock_send.return_value = {'status': 'ok', 'message': 'flood started: 10 Mbps for 20s on h2'}
        payload = {
            'experiment': 'congestion',
            'target': 'h2',
            'intensity': 10,
            'duration': 50  # Backend must enforce fixed duration (20s), not client value
        }
        resp = self.client.post('/trigger', json=payload)
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with('flood', 'h2', rate='10', duration=20)

    @patch('dashboard._send_control_command')
    def test_trigger_burst_duration(self, mock_send):
        """Burst experiment enforces fixed 6s duration."""
        mock_send.return_value = {'status': 'ok', 'message': 'burst started: 2 Mbps for 5s on h3'}
        resp = self.client.post('/trigger', json={'problem': 'burst', 'host': 'h3', 'rate': '8'})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with('burst', 'h3', rate='8', duration=6)

    @patch('dashboard._send_control_command')
    def test_trigger_cpu_stress(self, mock_send):
        """CPU stress triggers with 20s duration and normalized rate."""
        mock_send.return_value = {'status': 'ok', 'message': 'cpu stress started for 20s on h4'}
        resp = self.client.post('/trigger', json={'problem': 'cpu', 'host': 'h4'})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with('cpu', 'h4', rate='8', duration=20)

    @patch('dashboard._send_control_command')
    def test_trigger_mem_stress(self, mock_send):
        """Memory stress triggers with 20s duration."""
        mock_send.return_value = {'status': 'ok', 'message': 'mem stress started: 500 MB for 20s on h1'}
        resp = self.client.post('/trigger', json={'problem': 'mem', 'host': 'h1'})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with('mem', 'h1', rate='8', duration=20)

    @patch('dashboard._send_control_command')
    def test_trigger_kill_agent(self, mock_send):
        """Agent kill triggers with 20s restart delay."""
        mock_send.return_value = {'status': 'ok', 'message': 'agent killed on h2; restart scheduled in 20s'}
        resp = self.client.post('/trigger', json={'problem': 'kill', 'host': 'h2'})
        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with('kill', 'h2', rate='8', duration=20)

    @patch('dashboard._send_control_command')
    def test_trigger_socket_error_returns_503(self, mock_send):
        """When control socket fails or is not reachable, returns 503."""
        mock_send.return_value = {'status': 'error', 'message': 'Control socket not found — is the topology running?'}
        resp = self.client.post('/trigger', json={'problem': 'flood', 'host': 'h1', 'rate': '8'})
        self.assertEqual(resp.status_code, 503)
        data = resp.get_json()
        self.assertEqual(data.get('status'), 'error')
        self.assertIn('Control socket not found', data.get('message', ''))

    # -----------------------------------------------------------------------
    # Priority 2 Tests: Topology & Live Network Link Statistics
    # -----------------------------------------------------------------------

    def test_topology_endpoint_structure(self):
        """GET /api/topology returns configured Mininet topology."""
        resp = self.client.get('/api/topology')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get('type'), 'configured_mininet')
        self.assertIn('switches', data)
        self.assertIn('hosts', data)
        self.assertIn('links', data)

        # 2 switches: s1, s2
        switches = {s['id']: s for s in data['switches']}
        self.assertEqual(len(switches), 2)
        self.assertIn('s1', switches)
        self.assertIn('s2', switches)
        self.assertEqual(switches['s1']['dpid'], '0000000000000001')
        self.assertEqual(switches['s1']['ports'], [1, 2, 3, 4, 5])
        self.assertEqual(switches['s2']['dpid'], '0000000000000002')
        self.assertEqual(switches['s2']['ports'], [1, 2, 3])

        # 6 hosts: h1..h6
        hosts = {h['id']: h for h in data['hosts']}
        self.assertEqual(len(hosts), 6)
        for i in range(1, 7):
            hid = f'h{i}'
            self.assertIn(hid, hosts)
            self.assertEqual(hosts[hid]['ip'], f'10.0.0.{i}')
        self.assertEqual(hosts['h5']['role'], 'Monitoring Server')
        self.assertEqual(hosts['h5']['connected_to'], 's2')
        self.assertEqual(hosts['h5']['switch_port'], 2)

        # 7 links
        links = {l['id']: l for l in data['links']}
        self.assertEqual(len(links), 7)
        self.assertIn('s1-s2', links)
        bottleneck = links['s1-s2']
        self.assertTrue(bottleneck['is_bottleneck'])
        self.assertEqual(bottleneck['capacity_mbps'], 5)
        self.assertEqual(bottleneck['delay'], '5ms')
        self.assertEqual(bottleneck['source'], 's1')
        self.assertEqual(bottleneck['target'], 's2')
        self.assertEqual(bottleneck['source_port'], 5)
        self.assertEqual(bottleneck['target_port'], 1)

        # Verify edge links
        for edge_id in ['h1-s1', 'h2-s1', 'h3-s1', 'h4-s1', 's2-h5', 's2-h6']:
            self.assertIn(edge_id, links)
            self.assertFalse(links[edge_id]['is_bottleneck'])
            self.assertEqual(links[edge_id]['capacity_mbps'], 100)
            self.assertEqual(links[edge_id]['delay'], '1ms')

    def test_network_links_missing_stats_files(self):
        """When stats files are missing, all links return unmeasured with unknown status."""
        file_dict = {
            '/tmp/sdn_portstats.json': FileNotFoundError(),
            '/tmp/sdn_metrics.json': FileNotFoundError(),
        }
        with patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/links')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['stale'])
        links = data['links']
        self.assertEqual(len(links), 7)
        for lid, link in links.items():
            self.assertFalse(link['measured'])
            self.assertIsNone(link['throughput_mbps'])
            self.assertIsNone(link['utilization'])
            self.assertEqual(link['status'], 'unknown')

    def test_network_links_first_sample_no_rate(self):
        """First portstats sample does not calculate a rate (no delta available)."""
        portstats_content = json.dumps({'timestamp': 100.0, 'port_stats': {'1:1': 1000000}})
        file_dict = {
            '/tmp/sdn_portstats.json': portstats_content,
            '/tmp/sdn_metrics.json': '{}',
        }
        with patch('dashboard.time.time', return_value=101.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/links')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data['stale'])
        self.assertEqual(data['age_seconds'], 1.0)

        h1_link = data['links']['h1-s1']
        self.assertFalse(h1_link['measured'])
        self.assertIsNone(h1_link['throughput_mbps'])
        self.assertIsNone(h1_link['utilization'])
        self.assertEqual(h1_link['tx_bytes'], 1000000)
        self.assertEqual(h1_link['status'], 'up')

    def test_network_links_consecutive_sample_rate_calculation(self):
        """Two consecutive samples compute accurate delta throughput and utilization."""
        # First sample
        portstats_1 = json.dumps({'timestamp': 100.0, 'port_stats': {'1:1': 1000000}})
        file_dict_1 = {
            '/tmp/sdn_portstats.json': portstats_1,
            '/tmp/sdn_metrics.json': '{}',
        }
        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict_1)):
            self.client.get('/api/network/links')

        # Second sample: 3,000,000 bytes delta over 3.0s = 1,000,000 B/s = 8 Mbps
        portstats_2 = json.dumps({'timestamp': 103.0, 'port_stats': {'1:1': 4000000}})
        file_dict_2 = {
            '/tmp/sdn_portstats.json': portstats_2,
            '/tmp/sdn_metrics.json': '{}',
        }
        with patch('dashboard.time.time', return_value=103.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict_2)):
            resp = self.client.get('/api/network/links')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        h1_link = data['links']['h1-s1']
        self.assertTrue(h1_link['measured'])
        self.assertEqual(h1_link['throughput_mbps'], 8.0)
        self.assertEqual(h1_link['utilization'], 8.0)  # 8 Mbps / 100 Mbps = 8.0%
        self.assertEqual(h1_link['status'], 'up')

    def test_network_links_bottleneck_controller_utilization(self):
        """Bottleneck link uses verified controller metrics when available."""
        portstats = json.dumps({'timestamp': 100.0, 'port_stats': {'1:5': 2000000}})
        metrics = json.dumps({'timestamp': 100.0, 'utilization': 0.85})
        file_dict = {
            '/tmp/sdn_portstats.json': portstats,
            '/tmp/sdn_metrics.json': metrics,
        }
        with patch('dashboard.time.time', return_value=101.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/links')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        s1_s2 = data['links']['s1-s2']
        self.assertTrue(s1_s2['measured'])
        self.assertEqual(s1_s2['utilization'], 85.0)
        self.assertEqual(s1_s2['throughput_mbps'], 4.25)  # 85% of 5 Mbps = 4.25 Mbps
        self.assertEqual(s1_s2['status'], 'congested')  # >= 80% marks congested

    def test_network_links_counter_reset_handled_safely(self):
        """Counter reset (tx_bytes drops) does not yield negative rates or crash."""
        # Sample 1: high tx_bytes
        portstats_1 = json.dumps({'timestamp': 100.0, 'port_stats': {'1:1': 5000000}})
        file_dict_1 = {
            '/tmp/sdn_portstats.json': portstats_1,
            '/tmp/sdn_metrics.json': '{}',
        }
        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict_1)):
            self.client.get('/api/network/links')

        # Sample 2: interface reset, counter drops to 1000
        portstats_2 = json.dumps({'timestamp': 103.0, 'port_stats': {'1:1': 1000}})
        file_dict_2 = {
            '/tmp/sdn_portstats.json': portstats_2,
            '/tmp/sdn_metrics.json': '{}',
        }
        with patch('dashboard.time.time', return_value=103.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict_2)):
            resp = self.client.get('/api/network/links')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        h1_link = data['links']['h1-s1']
        self.assertFalse(h1_link['measured'])
        self.assertIsNone(h1_link['throughput_mbps'])
        self.assertIsNone(h1_link['utilization'])

    def test_network_links_stale_detection(self):
        """Sample older than 10s is flagged stale."""
        portstats = json.dumps({'timestamp': 100.0, 'port_stats': {'1:1': 1000000}})
        file_dict = {
            '/tmp/sdn_portstats.json': portstats,
            '/tmp/sdn_metrics.json': '{}',
        }
        with patch('dashboard.time.time', return_value=125.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/links')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['stale'])
        self.assertEqual(data['age_seconds'], 25.0)
        self.assertTrue(data['links']['h1-s1']['stale'])

    def test_network_links_reject_stale_bottleneck_metric(self):
        portstats = json.dumps({'timestamp': 100.0, 'port_stats': {'1:5': 2000000}})
        metrics = json.dumps({'timestamp': 80.0, 'utilization': 0.95})
        file_dict = {
            '/tmp/sdn_portstats.json': portstats,
            '/tmp/sdn_metrics.json': metrics,
        }
        with patch('dashboard.time.time', return_value=101.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/links')

        self.assertEqual(resp.status_code, 200)
        link = resp.get_json()['links']['s1-s2']
        self.assertFalse(link['measured'])
        self.assertIsNone(link['throughput_mbps'])
        self.assertIsNone(link['utilization'])
        self.assertEqual(link['status'], 'unknown')
        self.assertTrue(link['stale'])

    # =========================================================================
    # Priority 3: Flow-Level Monitoring (/api/network/flows)
    # =========================================================================

    def test_network_flows_missing_file_handled(self):
        """When /tmp/sdn_flows.json is missing, return 200 with empty flows and stale flag."""
        with patch('builtins.open', side_effect=FileNotFoundError("No flows file")):
            resp = self.client.get('/api/network/flows')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['flows'], [])
        self.assertTrue(data['stale'])
        self.assertEqual(data['top_talkers'], [])
        self.assertEqual(data['mitigation_rules'], [])

    def test_network_flows_first_sample_unmeasured(self):
        """First flow sample cannot compute delta; throughput_mbps is None and measured is False."""
        sample_flow = {
            'flow_id': '1:1:10.0.0.1:10.0.0.5:0:OUTPUT:5',
            'dpid': 1,
            'switch': 's1',
            'table_id': 0,
            'priority': 1,
            'match': {'in_port': 1, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5', 'ip_proto': 6},
            'actions': ['OUTPUT:5'],
            'packet_count': 100,
            'byte_count': 150000,
            'duration_sec': 5,
            'is_mitigation': False,
            'is_table_miss': False,
        }
        flows_json = json.dumps({'timestamp': 100.0, 'flows': [sample_flow]})
        file_dict = {'/tmp/sdn_flows.json': flows_json, '/tmp/sdn_metrics.json': '{}'}

        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/flows')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['flows']), 1)
        f = data['flows'][0]
        self.assertFalse(f['measured'])
        self.assertIsNone(f['throughput_mbps'])
        self.assertEqual(f['byte_count'], 150000)

    def test_network_flows_consecutive_samples_computes_throughput(self):
        """Consecutive samples compute accurate per-flow throughput."""
        flow_template = {
            'flow_id': '1:1:10.0.0.1:10.0.0.5:0:OUTPUT:5',
            'dpid': 1,
            'switch': 's1',
            'table_id': 0,
            'priority': 1,
            'match': {'in_port': 1, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5', 'ip_proto': 6},
            'actions': ['OUTPUT:5'],
            'packet_count': 100,
            'byte_count': 1000000,
            'duration_sec': 10,
            'is_mitigation': False,
            'is_table_miss': False,
        }

        # Sample 1 at t=100.0
        flows_1 = dict(flow_template)
        flows_json_1 = json.dumps({'timestamp': 100.0, 'flows': [flows_1]})
        file_dict_1 = {'/tmp/sdn_flows.json': flows_json_1, '/tmp/sdn_metrics.json': '{}'}

        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict_1)):
            self.client.get('/api/network/flows')

        # Sample 2 at t=103.0 (+375,000 bytes => (375000 * 8) / 3.0 / 1e6 = 1.0 Mbps)
        flows_2 = dict(flow_template)
        flows_2['byte_count'] = 1375000
        flows_2['packet_count'] = 350
        flows_json_2 = json.dumps({'timestamp': 103.0, 'flows': [flows_2]})
        # Mock metrics indicating bottleneck throughput is 2.0 Mbps
        metrics_json = json.dumps({'bottleneck_utilization': 40.0, 'bottleneck_throughput_mbps': 2.0})
        file_dict_2 = {'/tmp/sdn_flows.json': flows_json_2, '/tmp/sdn_metrics.json': metrics_json}

        with patch('dashboard.time.time', return_value=103.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict_2)):
            resp = self.client.get('/api/network/flows')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['flows']), 1)
        f = data['flows'][0]
        self.assertTrue(f['measured'])
        self.assertAlmostEqual(f['throughput_mbps'], 1.0, places=2)
        # Flow traverses bottleneck (s1:OUTPUT:5) and bottleneck rate is 2.0 Mbps => share = (1.0 / 2.0) * 100 = 50%
        self.assertAlmostEqual(f['share_of_bottleneck_pct'], 50.0, places=1)

    def test_network_flows_counter_reset_handled(self):
        """Counter drop (byte_count < prev) is handled safely without negative throughput."""
        flow_template = {
            'flow_id': '1:1:10.0.0.1:10.0.0.5:0:OUTPUT:5',
            'dpid': 1,
            'switch': 's1',
            'table_id': 0,
            'priority': 1,
            'match': {'in_port': 1, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5'},
            'actions': ['OUTPUT:5'],
            'packet_count': 500,
            'byte_count': 2000000,
            'duration_sec': 20,
            'is_mitigation': False,
            'is_table_miss': False,
        }

        # Sample 1: high bytes
        f1 = json.dumps({'timestamp': 100.0, 'flows': [flow_template]})
        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open({'/tmp/sdn_flows.json': f1, '/tmp/sdn_metrics.json': '{}'})):
            self.client.get('/api/network/flows')

        # Sample 2: rule re-installed, counter drops to 500
        flow_reset = dict(flow_template)
        flow_reset['byte_count'] = 500
        f2 = json.dumps({'timestamp': 103.0, 'flows': [flow_reset]})
        with patch('dashboard.time.time', return_value=103.0), \
             patch('builtins.open', side_effect=make_mock_open({'/tmp/sdn_flows.json': f2, '/tmp/sdn_metrics.json': '{}'})):
            resp = self.client.get('/api/network/flows')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        f = data['flows'][0]
        self.assertFalse(f['measured'])
        self.assertIsNone(f['throughput_mbps'])

    def test_network_flows_mitigation_drop_separation(self):
        """Priority 100 DROP rules are classified as mitigation and expose dropped_mbps."""
        drop_flow = {
            'flow_id': '1:1:10.0.0.1:10.0.0.5:0:DROP',
            'dpid': 1,
            'switch': 's1',
            'table_id': 0,
            'priority': 100,
            'match': {'in_port': 1, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5'},
            'actions': ['DROP'],
            'packet_count': 200,
            'byte_count': 1000000,
            'duration_sec': 5,
            'is_mitigation': True,
            'is_table_miss': False,
        }

        # Sample 1
        f1 = json.dumps({'timestamp': 100.0, 'flows': [drop_flow]})
        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open({'/tmp/sdn_flows.json': f1, '/tmp/sdn_metrics.json': '{}'})):
            self.client.get('/api/network/flows')

        # Sample 2 (+750,000 bytes over 3s => 2.0 Mbps dropped)
        drop_2 = dict(drop_flow)
        drop_2['byte_count'] = 1750000
        f2 = json.dumps({'timestamp': 103.0, 'flows': [drop_2]})
        with patch('dashboard.time.time', return_value=103.0), \
             patch('builtins.open', side_effect=make_mock_open({'/tmp/sdn_flows.json': f2, '/tmp/sdn_metrics.json': '{}'})):
            resp = self.client.get('/api/network/flows')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['mitigation_rules']), 1)
        m = data['mitigation_rules'][0]
        self.assertTrue(m['is_mitigation'])
        self.assertIsNone(m['throughput_mbps'])  # Drop flows do not forward
        self.assertAlmostEqual(m['dropped_mbps'], 2.0, places=2)
        self.assertIsNone(m['share_of_bottleneck_pct'])  # Never consumes bottleneck link

    def test_top_talkers_avoids_double_counting(self):
        """Top Talkers includes ingress edge flows only; transit hops are excluded to prevent double counting."""
        # Flow 1 on s1: ingress from h1 (in_port 1) forwarding out 5 (bottleneck)
        flow_s1 = {
            'flow_id': '1:1:10.0.0.1:10.0.0.5:0:OUTPUT:5',
            'dpid': 1,
            'switch': 's1',
            'table_id': 0,
            'priority': 1,
            'match': {'in_port': 1, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5', 'ip_proto': 6},
            'actions': ['OUTPUT:5'],
            'packet_count': 100,
            'byte_count': 1000000,
            'duration_sec': 5,
            'is_mitigation': False,
            'is_table_miss': False,
        }
        # Flow 2 on s2: downstream transit hop arriving on in_port 1 (bottleneck) forwarding to h5 (port 2)
        flow_s2 = {
            'flow_id': '2:1:10.0.0.1:10.0.0.5:0:OUTPUT:2',
            'dpid': 2,
            'switch': 's2',
            'table_id': 0,
            'priority': 1,
            'match': {'in_port': 1, 'ipv4_src': '10.0.0.1', 'ipv4_dst': '10.0.0.5', 'ip_proto': 6},
            'actions': ['OUTPUT:2'],
            'packet_count': 100,
            'byte_count': 1000000,
            'duration_sec': 5,
            'is_mitigation': False,
            'is_table_miss': False,
        }

        # Sample 1
        f1 = json.dumps({'timestamp': 100.0, 'flows': [flow_s1, flow_s2]})
        with patch('dashboard.time.time', return_value=100.0), \
             patch('builtins.open', side_effect=make_mock_open({'/tmp/sdn_flows.json': f1, '/tmp/sdn_metrics.json': '{}'})):
            self.client.get('/api/network/flows')

        # Sample 2
        f1_2 = dict(flow_s1, byte_count=1375000)
        f2_2 = dict(flow_s2, byte_count=1375000)
        f2 = json.dumps({'timestamp': 103.0, 'flows': [f1_2, f2_2]})
        with patch('dashboard.time.time', return_value=103.0), \
             patch('builtins.open', side_effect=make_mock_open({'/tmp/sdn_flows.json': f2, '/tmp/sdn_metrics.json': '{}'})):
            resp = self.client.get('/api/network/flows')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        # All flows list contains both flows (2 entries in switch flow tables)
        self.assertEqual(len(data['flows']), 2)
        # But top_talkers MUST only contain 1 entry (the ingress flow from s1, avoiding double counting s2 transit)
        self.assertEqual(len(data['top_talkers']), 1)
        tt = data['top_talkers'][0]
        self.assertEqual(tt['source'], '10.0.0.1')
        self.assertEqual(tt['destination'], '10.0.0.5')
        self.assertEqual(tt['ingress_switch'], 's1')
        self.assertAlmostEqual(tt['throughput_mbps'], 1.0, places=2)

    def test_network_flows_stale_detection(self):
        """Flow snapshot older than 10.0s is flagged stale."""
        flows_json = json.dumps({'timestamp': 100.0, 'flows': []})
        file_dict = {'/tmp/sdn_flows.json': flows_json, '/tmp/sdn_metrics.json': '{}'}
        with patch('dashboard.time.time', return_value=115.0), \
             patch('builtins.open', side_effect=make_mock_open(file_dict)):
            resp = self.client.get('/api/network/flows')

        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['stale'])
        self.assertEqual(data['age_seconds'], 15.0)


if __name__ == '__main__':
    unittest.main()
