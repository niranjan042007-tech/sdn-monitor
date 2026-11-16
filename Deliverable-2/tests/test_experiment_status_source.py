"""Regression tests for the Experiment Center status pipeline (pending issue A).

Root cause: the dashboard served /tmp/sdn_experiments.json in preference to the
live topology, and the topology wrote that file through a fixed '<file>.tmp'
name with OSError swallowed. A leftover .tmp owned by another user (Ubuntu
fs.protected_regular) made every export fail, so a new burst never appeared.
"""
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import dashboard
import topo.monitoring_topo as mt


class TestExperimentStatusSource(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()

    @patch('dashboard._send_control_command')
    def test_live_ipc_preferred_over_state_file(self, mock_send):
        live = {'active': {'exp_id': 'abc', 'host': 'h4', 'cmd': 'burst', 'state': 'RUNNING'},
                'history': []}
        mock_send.return_value = {'status': 'ok', 'experiments': live}
        with patch('os.path.exists', return_value=True), \
             patch('builtins.open', side_effect=AssertionError('file must not be read')):
            data = self.client.get('/api/experiments/status').get_json()
        self.assertEqual(data['source'], 'live')
        self.assertFalse(data['stale'])
        self.assertEqual(data['active']['host'], 'h4')

    @patch('dashboard._send_control_command')
    def test_file_fallback_is_marked_stale(self, mock_send):
        mock_send.return_value = {'status': 'error', 'message': 'topology unavailable'}
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'sdn_experiments.json')
            with open(f, 'w') as fh:
                json.dump({'active': None, 'history': [{'exp_id': 'old'}]}, fh)
            real_join = os.path.join
            with patch('dashboard.os.path.join',
                       side_effect=lambda *a: f if a[-1] == 'sdn_experiments.json' else real_join(*a)):
                data = self.client.get('/api/experiments/status').get_json()
        self.assertEqual(data['source'], 'file')
        self.assertTrue(data['stale'])

    @patch('dashboard._send_control_command')
    def test_nothing_available(self, mock_send):
        mock_send.return_value = {'status': 'error', 'message': 'down'}
        with patch('dashboard.os.path.exists', return_value=False):
            data = self.client.get('/api/experiments/status').get_json()
        self.assertEqual(data, {'active': None, 'history': [], 'source': 'unavailable', 'stale': True})


class TestTriggerBodyValidation(unittest.TestCase):
    def setUp(self):
        self.client = dashboard.app.test_client()

    def test_bad_bodies_get_json_400(self):
        for kwargs in ({}, {'data': 'not json', 'content_type': 'application/json'},
                       {'json': ['cpu', 'h1']}):
            with self.subTest(kwargs=kwargs):
                r = self.client.post('/trigger', **kwargs)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(r.get_json()['status'], 'error')


class TestExportSurvivesLeftoverTmp(unittest.TestCase):
    def test_export_ignores_unwritable_fixed_tmp(self):
        with tempfile.TemporaryDirectory() as d:
            state = os.path.join(d, 'sdn_experiments.json')
            # Simulate a leftover fixed-name tmp file that cannot be opened.
            blocker = state + '.tmp'
            os.mkdir(blocker)  # a directory: open(..., 'w') on it always fails
            with patch.object(mt, 'EXPERIMENTS_STATE_FILE', state):
                tr = mt.ExperimentTracker()
                eid = tr.start('h4', 'burst', 6)
                with open(state) as fh:
                    self.assertEqual(json.load(fh)['active']['exp_id'], eid)
                tr.complete('h4', 'burst', eid, 'RECOVERED')
                with open(state) as fh:
                    hist = json.load(fh)['history']
            self.assertEqual(hist[0]['exp_id'], eid)
            self.assertEqual(hist[0]['state'], 'RECOVERED')
            leftovers = [n for n in os.listdir(d) if n.startswith('sdn_experiments.') and n.endswith('.tmp') and n != 'sdn_experiments.json.tmp']
            self.assertEqual(leftovers, [])


if __name__ == '__main__':
    unittest.main()
