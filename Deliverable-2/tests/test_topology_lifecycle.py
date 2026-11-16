"""Offline lifecycle tests for the topology wrapper; never launches Mininet."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from topo import monitoring_topo


class FakeHost:
    def cmd(self, _command):
        return ''


class FakeNet:
    def __init__(self, **_kwargs):
        self.stopped = 0

    def start(self):
        pass

    def get(self, _name):
        return FakeHost()

    def stop(self):
        self.stopped += 1


class TopologyLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()
        self.patchers = [
            mock.patch.object(monitoring_topo, 'MonitoringTopo', return_value=object()),
            mock.patch.object(monitoring_topo, 'Mininet', return_value=self.net),
            mock.patch.object(monitoring_topo, 'RemoteController', object),
            mock.patch.object(monitoring_topo, 'OVSKernelSwitch', object),
            mock.patch.object(monitoring_topo, 'TCLink', object),
            mock.patch.object(monitoring_topo, 'info'),
            mock.patch.object(monitoring_topo, '_write_topology_pid_file'),
            mock.patch.object(monitoring_topo, '_remove_topology_pid_file'),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_cli_failure_stops_network_and_control_thread(self):
        server_exited = threading.Event()

        def fake_control(_net, _base, stop_event, ready_event):
            ready_event.set()
            stop_event.wait(2)
            server_exited.set()

        with mock.patch.object(monitoring_topo, '_control_server', side_effect=fake_control), \
             mock.patch.object(monitoring_topo, 'CLI', side_effect=RuntimeError('cli failed')):
            with self.assertRaisesRegex(RuntimeError, 'cli failed'):
                monitoring_topo.run()

        self.assertEqual(self.net.stopped, 1)
        self.assertTrue(server_exited.is_set())

    def test_control_startup_failure_stops_network_without_cli(self):
        with mock.patch.object(monitoring_topo, '_control_server', side_effect=RuntimeError('bind failed')), \
             mock.patch.object(monitoring_topo, 'CLI') as cli:
            with self.assertRaisesRegex(RuntimeError, 'Control socket startup failed'):
                monitoring_topo.run()

        self.assertEqual(self.net.stopped, 1)
        cli.assert_not_called()

    def test_repeated_run_stops_each_network(self):
        def fake_control(_net, _base, stop_event, ready_event):
            ready_event.set()
            stop_event.wait(2)

        for _ in range(2):
            with mock.patch.object(monitoring_topo, '_control_server',
                                   side_effect=fake_control), \
                 mock.patch.object(monitoring_topo, 'CLI'):
                monitoring_topo.run()
        self.assertEqual(self.net.stopped, 2)


if __name__ == '__main__':
    unittest.main()
