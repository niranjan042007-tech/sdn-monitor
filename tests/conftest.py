"""Shared pytest setup.

Isolation: the experiment tracker exports to /tmp/sdn_experiments.json, the
same file the live dashboard serves. Without this fixture, running the test
suite on the demo VM injected fake experiment history (PID 9999,
completed_at=100.0) into the real dashboard. Every test now gets its own file.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_experiment_state_file(tmp_path, monkeypatch):
    try:
        import topo.monitoring_topo as mt
    except Exception:  # module not importable in this test -> nothing to isolate
        yield
        return
    monkeypatch.setattr(mt, 'EXPERIMENTS_STATE_FILE', os.path.join(str(tmp_path), 'sdn_experiments.json'))
    yield
