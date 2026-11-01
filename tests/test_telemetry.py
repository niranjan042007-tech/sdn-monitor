#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from agents.telemetry_agent import build_packet

def test_payload():
    pkt = build_packet('h1', 5)
    assert pkt['host'] == 'h1'
    assert pkt['seq'] == 5
    assert isinstance(pkt['cpu_load'], float)
    assert isinstance(pkt['mem_percent'], float)
    print("PASS: Telemetry payload validation")
    
if __name__ == '__main__':
    test_payload()
