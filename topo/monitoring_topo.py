#!/usr/bin/env python3
"""Mininet topology for Project 6 (Network Monitoring & Alert System)."""
from mininet.topo import Topo
from mininet.net import Mininet
from mininet.node import RemoteController, OVSKernelSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info
from mininet.link import TCLink
import os


class MonitoringTopo(Topo):
    def build(self):
        s1 = self.addSwitch('s1')
        s2 = self.addSwitch('s2')

        h1 = self.addHost('h1', ip='10.0.0.1/24')
        h2 = self.addHost('h2', ip='10.0.0.2/24')
        h3 = self.addHost('h3', ip='10.0.0.3/24')
        h4 = self.addHost('h4', ip='10.0.0.4/24')
        h5 = self.addHost('h5', ip='10.0.0.5/24')
        h6 = self.addHost('h6', ip='10.0.0.6/24')

        for h in (h1, h2, h3, h4):
            self.addLink(h, s1, bw=100, delay='1ms')

        self.addLink(s1, s2, bw=5, delay='5ms', max_queue_size=100)
        self.addLink(h5, s2, bw=100, delay='1ms')
        self.addLink(h6, s2, bw=100, delay='1ms')


def run():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    topo = MonitoringTopo()
    net = Mininet(topo=topo, controller=RemoteController,
                  switch=OVSKernelSwitch, link=TCLink, autoSetMacs=True)
    net.start()

    info('*** Starting monitoring server on h5\n')
    h5 = net.get('h5')
    h5.cmd(f'cd {base} && python3 server/monitoring_server.py '
           f'> /tmp/server.log 2>&1 &')

    info('*** Starting telemetry agents on h1..h4\n')
    for hname in ('h1', 'h2', 'h3', 'h4'):
        h = net.get(hname)
        h.cmd(f'cd {base} && python3 agents/telemetry_agent.py {hname} 10.0.0.5 '
              f'> /tmp/{hname}_agent.log 2>&1 &')

    info('*** Topology ready. Start Ryu in another terminal.\n')
    CLI(net)
    net.stop()


if __name__ == '__main__':
    setLogLevel('info')
    run()
