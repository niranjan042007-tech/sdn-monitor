#!/usr/bin/env python3
"""Ryu SDN controller with automatic mitigation."""
import json
import time
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import (CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls)
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.lib import hub

SERVER_STATE_FILE = '/tmp/server_state.json'
LOG_FILE = '/tmp/sdn_alerts.log'
UTIL_THRESHOLD = 0.80
BOTTLENECK_PORTS = {(1, 5): 5e6, (2, 1): 5e6}

class AdvancedController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.port_stats_prev = {}
        self.mitigation_active = False
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions, idle=60)

    def add_flow(self, dp, prio, match, actions, idle=0, hard=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst, idle_timeout=idle, hard_timeout=hard))

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match['in_port']
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None: return
        dpid = dp.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        out_port = self.mac_to_port[dpid].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst, eth_src=eth.src)
            self.add_flow(dp, 1, match, actions, idle=60)
            
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data))

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                p = dp.ofproto_parser
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
            hub.sleep(3)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        dpid = ev.msg.datapath.id
        now = time.time()
        bottleneck_congested = False

        for s in ev.msg.body:
            if s.port_no > 100: continue
            key = (dpid, s.port_no)
            if key in self.port_stats_prev:
                prev_tx, prev_rx, prev_t = self.port_stats_prev[key]
                dt = max(now - prev_t, 1e-6)
                rate_bps = max(s.tx_bytes - prev_tx, s.rx_bytes - prev_rx) * 8 / dt
                capacity = BOTTLENECK_PORTS.get(key, 100e6)
                util = rate_bps / capacity

                if util > UTIL_THRESHOLD and key in BOTTLENECK_PORTS:
                    bottleneck_congested = True
                    self._alert(f'HIGH-UTIL on bottleneck (dpid={dpid} port={s.port_no}) util={util:.2f}')
            self.port_stats_prev[key] = (s.tx_bytes, s.rx_bytes, now)

        self._check_integration(bottleneck_congested)

    def _check_integration(self, bottleneck_congested):
        try:
            with open(SERVER_STATE_FILE, 'r') as f:
                server_state = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        high_loss = False
        for host, data in server_state.items():
            if data.get('loss', 0) > 0.05:
                high_loss = True
                break

        if bottleneck_congested and high_loss and not self.mitigation_active:
            self._alert('INTEGRATION ALERT: Bottleneck full + App Loss > 5%. APPLYING MITIGATION.')
            self._apply_mitigation()
        elif not bottleneck_congested and self.mitigation_active:
            self._alert('INTEGRATION: Network recovered. Removing mitigation flag.')
            self.mitigation_active = False

    def _apply_mitigation(self):
        self.mitigation_active = True
        dp = self.datapaths.get(1)
        if not dp: return
        parser = dp.ofproto_parser
        match = parser.OFPMatch(eth_type=0x0800, ipv4_src='10.0.0.1', ipv4_dst='10.0.0.5', ip_proto=17, udp_dst=5555)
        self.add_flow(dp, 100, match, [], hard=15)
        self._alert('MITIGATION ACTIVE: Installed DROP rule for h1->h5 (port 5555)')

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def flow_removed_handler(self, ev):
        if ev.msg.reason == ev.msg.datapath.ofproto.OFPRR_HARD_TIMEOUT:
            self._alert('MITIGATION EXPIRED: Drop rule removed after hard timeout.')
            self.mitigation_active = False

    def _alert(self, msg):
        line = f'[{time.strftime("%H:%M:%S")}] ALERT: {msg}'
        self.logger.warning(line)
        try:
            with open(LOG_FILE, 'a') as f: f.write(line + '\n')
        except OSError: pass

if __name__ == '__main__':
    app_manager.main()
