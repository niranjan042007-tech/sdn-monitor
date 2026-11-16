#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from server.monitoring_server import calculate_loss

def test_loss_calc():
    # Simulate sequence gap: expected 5 packets, got 3
    loss = calculate_loss(expected=5, received=3)
    assert loss == 0.4, f"Expected 0.4, got {loss}"
    # No loss
    assert calculate_loss(5, 5) == 0.0
    print("PASS: Server loss calculation")

if __name__ == '__main__':
    test_loss_calc()
