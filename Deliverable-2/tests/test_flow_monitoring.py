#!/usr/bin/env python3
"""Tests for OpenFlow Flow-Level Monitoring in Ryu Controller (Priority 3 validation)."""
import json
import os
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch, mock_open

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Cleanly mock Ryu modules before importing controller
class MockRyuApp:
    def __init__(self, *args, **kwargs):
        pass

def mock_decorator(*args, **kwargs):
    def wrap(fn):
        return fn
    return wrap

base_mock = MagicMock()
base_mock.app_manager.RyuApp = MockRyuApp

handler_mock = MagicMock()
handler_mock.MAIN_DISPATCHER = 1
handler_mock.CONFIG_DISPATCHER = 2
handler_mock.set_ev_cls = mock_decorator

ofproto_mock = MagicMock()
ofproto_mock.OFP_VERSION = 4
ofproto_mock.OFPP_CONTROLLER = 0xfffffffd
ofproto_mock.OFPMPF_REPLY_MORE = 1
ofproto_mock.OFPFF_SEND_FLOW_REM = 1

ofproto_pkg = MagicMock()
ofproto_pkg.ofproto_v1_3 = ofproto_mock

sys.modules['ryu'] = MagicMock()
sys.modules['ryu.base'] = base_mock
sys.modules['ryu.controller'] = MagicMock()
sys.modules['ryu.controller.ofp_event'] = MagicMock()
sys.modules['ryu.controller.handler'] = handler_mock
sys.modules['ryu.ofproto'] = ofproto_pkg
sys.modules['ryu.ofproto.ofproto_v1_3'] = ofproto_mock
sys.modules['ryu.ofproto.ofproto_v1_3_parser'] = MagicMock()
sys.modules['ryu.lib'] = MagicMock()
sys.modules['ryu.lib.packet'] = MagicMock()
sys.modules['ryu.lib.packet.packet'] = MagicMock()
sys.modules['ryu.lib.packet.ethernet'] = MagicMock()
sys.modules['ryu.lib.packet.ipv4'] = MagicMock()
sys.modules['ryu.lib.packet.udp'] = MagicMock()
sys.modules['ryu.lib.hub'] = MagicMock()

sys.modules.pop('controller.monitoring_controller', None)
import controller.monitoring_controller as mc


class TestFlowMonitoring(unittest.TestCase):
    def setUp(self):
        # Instantiate controller with fresh state
        self.ctrl = mc.AdvancedController()
        self.ctrl.flow_stats_cache = {}
        self.ctrl.flow_stats_in_flight = {}
        self.ctrl.raw_port_stats = {}
        self.ctrl.bottleneck_utils = {}
        self.ctrl.bottleneck_sample_times = {}
        self.ctrl.mitigation_active = False

    def _port_stats_reply(self, timestamp, tx_bytes, rx_bytes):
        event = SimpleNamespace(msg=SimpleNamespace(
            datapath=SimpleNamespace(id=1),
            body=[SimpleNamespace(port_no=5, tx_bytes=tx_bytes, rx_bytes=rx_bytes)],
        ))
        with patch.object(mc.time, 'time', return_value=timestamp), \
             patch('builtins.open', mock_open()), \
             patch.object(mc.os, 'replace'), \
             patch.object(self.ctrl, '_alert'), \
             patch.object(self.ctrl, '_check_integration') as check:
            self.ctrl.port_stats_reply(event)
        return check

    def test_bottleneck_counter_reset_discards_invalid_rate_until_next_sample(self):
        self._port_stats_reply(100.0, 10_000_000, 10_000_000)
        check = self._port_stats_reply(103.0, 11_687_500, 11_687_500)
        self.assertAlmostEqual(self.ctrl.bottleneck_utils[(1, 5)], 0.9)
        check.assert_called_once_with(0.9)

        check = self._port_stats_reply(106.0, 500, 500)
        self.assertNotIn((1, 5), self.ctrl.bottleneck_utils)
        check.assert_called_once_with(None)

        check = self._port_stats_reply(109.0, 1_688_000, 1_688_000)
        self.assertAlmostEqual(self.ctrl.bottleneck_utils[(1, 5)], 0.9)
        check.assert_called_once_with(0.9)

    def test_stale_bottleneck_sample_is_not_refreshed_by_other_switch_reply(self):
        self.ctrl.bottleneck_utils[(1, 5)] = 0.9
        self.ctrl.bottleneck_sample_times[(1, 5)] = 100.0
        event = SimpleNamespace(msg=SimpleNamespace(
            datapath=SimpleNamespace(id=2), body=[],
        ))
        with patch.object(mc.time, 'time', return_value=111.0), \
             patch('builtins.open', mock_open()), \
             patch.object(mc.os, 'replace'), \
             patch.object(self.ctrl, '_check_integration') as check:
            self.ctrl.port_stats_reply(event)
        check.assert_called_once_with(None)

    def _check_integration_with_state(self, state_json, max_util=0.9, ingress=None):
        # Congestion already seen on the previous poll; h1 is the heavy sender
        # unless the test says otherwise (measured from s1 port rx counters).
        self.ctrl.congested_polls = mc.CONGESTION_CONFIRM_POLLS - 1
        self.ctrl.host_ingress_bps = ingress if ingress is not None else {'h1': 6e6, 'h2': 0.05e6}
        opener = mock_open(read_data=state_json)
        with patch('builtins.open', opener), patch.object(mc.os, 'replace') as replace:
            with patch.object(self.ctrl, '_alert') as alert, \
                 patch.object(self.ctrl, '_apply_mitigation') as mitigate:
                self.ctrl._check_integration(max_util)
        return opener, replace, alert, mitigate

    def test_integration_accepts_normal_host_record(self):
        _, replace, alert, mitigate = self._check_integration_with_state(
            json.dumps({'h1': {'loss': 0.02, 'last_seen': time.time()}}), max_util=0.5
        )
        replace.assert_called_once_with(mc.METRICS_FILE + '.tmp', mc.METRICS_FILE)
        alert.assert_not_called()
        mitigate.assert_not_called()

    def test_integration_ignores_numeric_snapshot_timestamp_metadata(self):
        _, replace, alert, mitigate = self._check_integration_with_state(json.dumps({
            'h1': {'loss': 0.10, 'last_seen': time.time()},
            'snapshot_timestamp': 1234.5,
        }))
        replace.assert_called_once_with(mc.METRICS_FILE + '.tmp', mc.METRICS_FILE)
        alert.assert_called_once()
        mitigate.assert_called_once_with('h1')

    def test_integration_blames_top_sender_not_worst_loss_host(self):
        # h2 has the worst telemetry loss but h3 is the one flooding the link.
        _, _, alert, mitigate = self._check_integration_with_state(json.dumps({
            'h1': {'loss': 0.06, 'last_seen': time.time()},
            'h2': {'loss': 0.20, 'last_seen': time.time()},
            'h3': {'loss': 0.01, 'last_seen': time.time()},
            'snapshot_timestamp': 1234.5,
        }), ingress={'h1': 0.1e6, 'h2': 0.05e6, 'h3': 7.5e6})
        alert.assert_called_once()
        mitigate.assert_called_once_with('h3')

    def test_integration_no_mitigation_without_heavy_sender(self):
        _, _, alert, mitigate = self._check_integration_with_state(json.dumps({
            'h1': {'loss': 0.20, 'last_seen': time.time()},
        }), ingress={'h1': 0.1e6, 'h2': 0.05e6})
        mitigate.assert_not_called()

    def test_integration_fails_closed_when_host_loss_is_missing(self):
        opener, replace, alert, mitigate = self._check_integration_with_state(
            json.dumps({'h1': {'cpu': 10.0, 'mem': 40.0}})
        )
        opener.assert_called_once_with(mc.SERVER_STATE_FILE, 'r')
        replace.assert_not_called()
        alert.assert_not_called()
        mitigate.assert_not_called()

    def test_integration_fails_closed_on_invalid_host_records_or_loss(self):
        for state in (
            {'h1': 0.0},
            {'h1': {'loss': '0.2'}},
            {'h1': {'loss': float('nan')}},
        ):
            with self.subTest(state=state):
                opener, replace, alert, mitigate = self._check_integration_with_state(
                    json.dumps(state)
                )
                opener.assert_called_once_with(mc.SERVER_STATE_FILE, 'r')
                replace.assert_not_called()
                alert.assert_not_called()
                mitigate.assert_not_called()

    def test_integration_accepts_wrapped_telemetry_state_format(self):
        _, replace, alert, mitigate = self._check_integration_with_state(json.dumps({
            'telemetry': {'h1': {'loss': 0.15, 'last_seen': time.time()}},
            'snapshot_timestamp': 1234.5,
        }))
        replace.assert_called_once_with(mc.METRICS_FILE + '.tmp', mc.METRICS_FILE)
        alert.assert_called_once()
        mitigate.assert_called_once_with('h1')

    def test_integration_fails_closed_for_missing_or_malformed_json(self):
        for open_error, content in ((FileNotFoundError(), None), (None, '{invalid')):
            with self.subTest(open_error=open_error, content=content):
                opener = mock_open(read_data=content or '')
                if open_error:
                    opener.side_effect = open_error
                with patch('builtins.open', opener), \
                     patch.object(mc.os, 'replace') as replace, \
                     patch.object(self.ctrl, '_alert') as alert, \
                     patch.object(self.ctrl, '_apply_mitigation') as mitigate:
                    self.ctrl._check_integration(0.9)
                replace.assert_not_called()
                alert.assert_not_called()
                mitigate.assert_not_called()

    def test_normalize_forwarding_flow(self):
        """Standard forwarding flow normalizes match fields and output actions."""
        mock_flow = MagicMock()
        mock_flow.priority = 1
        mock_flow.table_id = 0
        mock_flow.cookie = 0
        mock_flow.packet_count = 150
        mock_flow.byte_count = 225000
        mock_flow.duration_sec = 12
        mock_flow.idle_timeout = 0
        mock_flow.hard_timeout = 0
        mock_flow.match = {
            'in_port': 1,
            'eth_src': '00:00:00:00:00:01',
            'eth_dst': '00:00:00:00:00:05',
            'eth_type': 0x0800,
            'ipv4_src': '10.0.0.1',
            'ipv4_dst': '10.0.0.5',
            'ip_proto': 6,
        }

        # Mock action: output to port 5
        act = MagicMock()
        act.port = 5
        inst = MagicMock()
        inst.actions = [act]
        mock_flow.instructions = [inst]

        res = self.ctrl._normalize_flow(1, mock_flow)

        self.assertEqual(res['dpid'], 1)
        self.assertEqual(res['switch'], 's1')
        self.assertEqual(res['priority'], 1)
        self.assertEqual(res['packet_count'], 150)
        self.assertEqual(res['byte_count'], 225000)
        self.assertEqual(res['match']['in_port'], '1')
        self.assertEqual(res['match']['eth_type'], '0x0800')
        self.assertEqual(res['match']['ipv4_src'], '10.0.0.1')
        self.assertEqual(res['match']['ipv4_dst'], '10.0.0.5')
        self.assertEqual(res['actions'], ['OUTPUT:5'])
        self.assertFalse(res['is_mitigation'])
        self.assertFalse(res['is_table_miss'])

    def test_normalize_mitigation_drop_flow(self):
        """Priority 100 flow with no output actions normalizes to DROP with is_mitigation=True."""
        mock_flow = MagicMock()
        mock_flow.priority = 100
        mock_flow.table_id = 0
        mock_flow.cookie = 0
        mock_flow.packet_count = 500
        mock_flow.byte_count = 750000
        mock_flow.duration_sec = 5
        mock_flow.idle_timeout = 0
        mock_flow.hard_timeout = 15
        mock_flow.match = {
            'eth_type': 0x0800,
            'ipv4_src': '10.0.0.1',
            'ipv4_dst': '10.0.0.5',
            'ip_proto': 17,
        }
        # No instructions => drop rule
        mock_flow.instructions = []

        res = self.ctrl._normalize_flow(1, mock_flow)

        self.assertEqual(res['actions'], ['DROP'])
        self.assertTrue(res['is_mitigation'])
        self.assertFalse(res['is_table_miss'])

    def test_normalize_table_miss_flow(self):
        """Priority 0 flow with empty match normalizes to is_table_miss=True and CONTROLLER action."""
        mock_flow = MagicMock()
        mock_flow.priority = 0
        mock_flow.table_id = 0
        mock_flow.cookie = 0
        mock_flow.packet_count = 20
        mock_flow.byte_count = 1400
        mock_flow.duration_sec = 100
        mock_flow.idle_timeout = 0
        mock_flow.hard_timeout = 0
        mock_flow.match = {}

        act = MagicMock()
        act.port = ofproto_mock.OFPP_CONTROLLER
        inst = MagicMock()
        inst.actions = [act]
        mock_flow.instructions = [inst]

        res = self.ctrl._normalize_flow(1, mock_flow)

        self.assertEqual(res['actions'], ['CONTROLLER'])
        self.assertTrue(res['is_table_miss'])
        self.assertFalse(res['is_mitigation'])

    def test_multipart_accumulation(self):
        """Multipart FlowStatsReply accumulates across parts and exports only when MORE flag clears."""
        dp = MagicMock()
        dp.id = 1
        dp.ofproto = ofproto_mock

        flow1 = MagicMock(priority=1, table_id=0, cookie=0, packet_count=10, byte_count=1000,
                          duration_sec=1, idle_timeout=0, hard_timeout=0, match={}, instructions=[])
        flow2 = MagicMock(priority=2, table_id=0, cookie=0, packet_count=20, byte_count=2000,
                          duration_sec=2, idle_timeout=0, hard_timeout=0, match={}, instructions=[])

        # Part 1 with OFPMPF_REPLY_MORE flag
        ev1 = MagicMock()
        ev1.msg.datapath = dp
        ev1.msg.body = [flow1]
        ev1.msg.flags = ofproto_mock.OFPMPF_REPLY_MORE

        with patch.object(self.ctrl, '_export_flow_stats') as mock_export:
            self.ctrl.flow_stats_reply_handler(ev1)
            # Should not export yet
            mock_export.assert_not_called()
            self.assertIn(1, self.ctrl.flow_stats_in_flight)
            self.assertEqual(len(self.ctrl.flow_stats_in_flight[1]), 1)

        # Part 2 with MORE flag cleared
        ev2 = MagicMock()
        ev2.msg.datapath = dp
        ev2.msg.body = [flow2]
        ev2.msg.flags = 0

        with patch.object(self.ctrl, '_export_flow_stats') as mock_export:
            self.ctrl.flow_stats_reply_handler(ev2)
            # Should export now
            mock_export.assert_called_once()
            # In-flight cleared for dp 1
            self.assertNotIn(1, self.ctrl.flow_stats_in_flight)
            # Cache populated with both flows
            self.assertEqual(len(self.ctrl.flow_stats_cache[1]), 2)

    def test_export_flow_stats_atomic_write(self):
        """_export_flow_stats writes to .tmp file first, then atomically renames."""
        self.ctrl.flow_stats_cache = {
            1: [{'id': 'flow-1', 'switch': 's1'}],
            2: [{'id': 'flow-2', 'switch': 's2'}],
        }

        written_data = {}
        def mock_open_file(path, mode='w'):
            m = mock_open()
            def write(chunk):
                written_data[path] = written_data.get(path, '') + chunk
            m().write.side_effect = write
            return m()

        with patch('builtins.open', side_effect=mock_open_file), \
             patch('os.replace') as mock_replace:
            self.ctrl._export_flow_stats()

            # Confirm atomic replace was called with .tmp and target file
            mock_replace.assert_called_once_with(mc.FLOW_STATS_FILE + '.tmp', mc.FLOW_STATS_FILE)
            # Confirm JSON payload structure written
            content = written_data.get(mc.FLOW_STATS_FILE + '.tmp', '')
            parsed = json.loads(content)
            self.assertIn('timestamp', parsed)
            self.assertIn('switches', parsed)
            self.assertIn('flows', parsed)
            self.assertEqual(len(parsed['flows']), 2)
            self.assertIn('1', parsed['switches'])
            self.assertIn('2', parsed['switches'])


if __name__ == '__main__':
    unittest.main()
