#!/usr/bin/env python3
"""Phase 2: Ryu SDN Controller with Dynamic Mitigation."""
import json, time, os, math
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ipv4, udp
from ryu.lib import hub

SERVER_STATE_FILE = '/tmp/server_state.json'
LOG_FILE = '/tmp/sdn_alerts.log'
METRICS_FILE = '/tmp/sdn_metrics.json'
PORT_STATS_FILE = '/tmp/sdn_portstats.json'
FLOW_STATS_FILE = '/tmp/sdn_flows.json'
UTIL_THRESHOLD = 0.80
BOTTLENECK_PORTS = {(1, 5): 5e6, (2, 1): 5e6}
# s1 host-facing ports (topology adds h1..h4 to s1 in order -> ports 1..4)
S1_HOST_PORTS = {1: 'h1', 2: 'h2', 3: 'h3', 4: 'h4'}
HOST_IPS = {'h1': '10.0.0.1', 'h2': '10.0.0.2', 'h3': '10.0.0.3', 'h4': '10.0.0.4'}
OFFENDER_MIN_BPS = 1e6      # a host must send >1 Mbps into s1 to be blamed
CONGESTION_CONFIRM_POLLS = 2  # consecutive congested polls before mitigating
PORT_STATS_STALE_SECONDS = 10.0  # Port stats are requested every 3 seconds

class AdvancedController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        self.port_stats_prev = {}
        self.bottleneck_utils = {}
        self.bottleneck_sample_times = {}
        self.current_utilization_timestamp = None
        self.raw_port_stats = {}
        self.flow_stats_cache = {}
        self.flow_stats_in_flight = {}
        self.mitigation_active = False
        self.mitigated_host = None
        self.host_ingress_bps = {}   # host -> rx rate measured on its s1 port
        self.congested_polls = 0
        self.last_util_log = 0
        self.monitor_thread = hub.spawn(self._monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        self.datapaths[dp.id] = dp
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self.add_flow(dp, 0, match, actions, idle=0)
        self.logger.info('Switch %016x connected', dp.id)

    def add_flow(self, dp, prio, match, actions, idle=0, hard=0, flags=0, cookie=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=prio, match=match,
            instructions=inst, idle_timeout=idle, hard_timeout=hard, flags=flags, cookie=cookie))

    @set_ev_cls(ofp_event.EventOFPErrorMsg, MAIN_DISPATCHER)
    def openflow_error_handler(self, ev):
        msg = ev.msg
        self.logger.error("OPENFLOW ERROR: dpid=%s type=%s code=%s data=%s", msg.datapath.id, msg.type, msg.code, msg.data.hex() if msg.data else None)

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
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=data))

    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                p = dp.ofproto_parser
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))
                dp.send_msg(p.OFPFlowStatsRequest(dp))
            hub.sleep(3)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        dpid = ev.msg.datapath.id
        now = time.time()

        for s in ev.msg.body:
            # Record raw stats for portstats JSON snapshot
            self.raw_port_stats[f"{dpid}:{s.port_no}"] = s.tx_bytes

            if s.port_no > 100: continue
            key = (dpid, s.port_no)
            if key in self.port_stats_prev:
                prev_tx, prev_rx, prev_t = self.port_stats_prev[key]
                dt = now - prev_t
                tx_delta = s.tx_bytes - prev_tx
                rx_delta = s.rx_bytes - prev_rx

                # A counter decrease means the switch/interface restarted. A
                # long sampling gap is also not a current rate measurement.
                if dt <= 0 or dt > PORT_STATS_STALE_SECONDS or tx_delta < 0 or rx_delta < 0:
                    self.bottleneck_utils.pop(key, None)
                    self.bottleneck_sample_times.pop(key, None)
                else:
                    rate_bps = max(tx_delta, rx_delta) * 8 / dt
                    capacity = BOTTLENECK_PORTS.get(key, 100e6)
                    util = rate_bps / capacity

                    if dpid == 1 and s.port_no in S1_HOST_PORTS:
                        # rx on the host's s1 port = traffic that host is sending
                        self.host_ingress_bps[S1_HOST_PORTS[s.port_no]] = rx_delta * 8 / dt
                    if key in BOTTLENECK_PORTS:
                        self.bottleneck_utils[key] = util
                        self.bottleneck_sample_times[key] = now
                        if util > UTIL_THRESHOLD:
                            if now - self.last_util_log > 3.0:
                                self._alert(f'HIGH-UTIL on bottleneck (dpid={dpid} port={s.port_no}) util={util:.2f}')
                                self.last_util_log = now
            self.port_stats_prev[key] = (s.tx_bytes, s.rx_bytes, now)

        # Export port stats JSON file (matches evidence/sdn_portstats.json format)
        try:
            snap = {'timestamp': now, 'port_stats': dict(self.raw_port_stats)}
            with open(PORT_STATS_FILE + '.tmp', 'w') as f:
                json.dump(snap, f, indent=2)
            os.replace(PORT_STATS_FILE + '.tmp', PORT_STATS_FILE)
        except OSError:
            pass

        # Only aggregate samples that were measured recently. A response from
        # the other switch must not make an old bottleneck value look fresh.
        fresh_samples = [
            (util, self.bottleneck_sample_times[key])
            for key, util in self.bottleneck_utils.items()
            if now - self.bottleneck_sample_times.get(key, 0.0) <= PORT_STATS_STALE_SECONDS
        ]
        if fresh_samples:
            max_util, self.current_utilization_timestamp = max(fresh_samples, key=lambda sample: sample[0])
        else:
            max_util = None
            self.current_utilization_timestamp = None
        self._check_integration(max_util)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        ofp = ev.msg.datapath.ofproto

        # Accumulate body parts across multipart replies per datapath
        self.flow_stats_in_flight.setdefault(dpid, []).extend(ev.msg.body)

        # Check if more parts are expected (OFPMPF_REPLY_MORE flag)
        if getattr(ev.msg, 'flags', 0) & ofp.OFPMPF_REPLY_MORE:
            return  # Wait for subsequent reply messages

        # Full snapshot received for this datapath
        flows_raw = self.flow_stats_in_flight.pop(dpid, [])
        normalized_flows = [self._normalize_flow(dpid, f) for f in flows_raw]
        self.flow_stats_cache[dpid] = normalized_flows
        self._export_flow_stats()

    def _normalize_flow(self, dpid, flow):
        switch_name = f"s{dpid}"
        priority = getattr(flow, 'priority', 0)
        table_id = getattr(flow, 'table_id', 0)
        cookie = getattr(flow, 'cookie', 0)
        packet_count = getattr(flow, 'packet_count', 0)
        byte_count = getattr(flow, 'byte_count', 0)
        duration_sec = getattr(flow, 'duration_sec', 0)
        idle_timeout = getattr(flow, 'idle_timeout', 0)
        hard_timeout = getattr(flow, 'hard_timeout', 0)

        match_dict = {}
        match_obj = getattr(flow, 'match', None)
        if match_obj is not None:
            if isinstance(match_obj, dict):
                items = match_obj.items()
            elif hasattr(match_obj, 'items'):
                items = match_obj.items()
            elif hasattr(match_obj, 'to_jsondict'):
                oxm = match_obj.to_jsondict().get('OFPMatch', {}).get('oxm_fields', [])
                items = [(f.get('OXMTlv', {}).get('field'), f.get('OXMTlv', {}).get('value'))
                         for f in oxm if isinstance(f, dict)]
            else:
                items = []

            for k, v in items:
                if k is None or v is None:
                    continue
                if k == 'eth_type' and isinstance(v, int):
                    match_dict[k] = f"0x{v:04x}"
                else:
                    match_dict[k] = str(v)

        actions_list = []
        instructions = getattr(flow, 'instructions', [])
        for inst in instructions:
            if hasattr(inst, 'actions'):
                for act in inst.actions:
                    if hasattr(act, 'port'):
                        if act.port == ofproto_v1_3.OFPP_CONTROLLER:
                            actions_list.append("CONTROLLER")
                        else:
                            actions_list.append(f"OUTPUT:{act.port}")
                    else:
                        actions_list.append(act.__class__.__name__)
        if not actions_list:
            actions_list = ["DROP"]

        is_table_miss = (priority == 0 and len(match_dict) == 0)
        is_mitigation = (priority == 100 and "DROP" in actions_list)

        match_sig = ",".join(f"{k}={match_dict[k]}" for k in sorted(match_dict.keys()))
        flow_id = f"{dpid}:{table_id}:{priority}:{cookie}:{match_sig}"

        return {
            'id': flow_id,
            'dpid': dpid,
            'switch': switch_name,
            'table_id': table_id,
            'priority': priority,
            'cookie': cookie,
            'match': match_dict,
            'actions': actions_list,
            'duration_sec': duration_sec,
            'idle_timeout': idle_timeout,
            'hard_timeout': hard_timeout,
            'packet_count': packet_count,
            'byte_count': byte_count,
            'is_mitigation': is_mitigation,
            'is_table_miss': is_table_miss,
        }

    def _export_flow_stats(self):
        now = time.time()
        switches_data = {}
        all_flows = []
        for dpid, flows in sorted(self.flow_stats_cache.items()):
            sw_name = f"s{dpid}"
            switches_data[str(dpid)] = {
                'dpid': dpid,
                'switch': sw_name,
                'flow_count': len(flows),
                'flows': flows,
            }
            all_flows.extend(flows)

        payload = {
            'timestamp': now,
            'snapshot_timestamp': now,
            'switches': switches_data,
            'flows': all_flows,
        }

        try:
            tmp_file = FLOW_STATS_FILE + '.tmp'
            with open(tmp_file, 'w') as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_file, FLOW_STATS_FILE)
        except OSError:
            pass

    def _check_integration(self, max_util):
        if max_util is None:
            # Missing or reset samples are unknown, not zero utilization. Do
            # not trigger or resolve mitigation based on an unknown reading.
            self.congested_polls = 0
            self._write_metrics(None)
            return

        try:
            with open(SERVER_STATE_FILE, 'r') as f:
                server_state = json.load(f)
        except (OSError, json.JSONDecodeError):
            return

        if not isinstance(server_state, dict):
            return

        # Current server format is a flat {host: record, snapshot_timestamp: ts}
        # object. Also accept the wrapped telemetry format used by older clients.
        if 'telemetry' in server_state:
            host_records = server_state['telemetry']
            if not isinstance(host_records, dict):
                return
        else:
            host_records = {
                host: record for host, record in server_state.items()
                if host != 'snapshot_timestamp'
            }
        if not host_records:
            return

        worst_host, worst_loss = None, 0.0
        now = time.time()

        for host, data in host_records.items():
            if host == 'snapshot_timestamp':
                continue

            if not isinstance(data, dict):
                return

            loss = data.get('loss')
            if (not isinstance(loss, (int, float)) or isinstance(loss, bool)
                    or not math.isfinite(loss) or loss < 0.0 or loss > 1.0):
                return

            last_seen = data.get('last_seen')
            if (not isinstance(last_seen, (int, float))
                    or isinstance(last_seen, bool)
                    or not math.isfinite(last_seen)
                    or now - last_seen > 5.0
                    or now < last_seen):
                continue

            if loss > worst_loss:
                worst_loss = loss
                worst_host = host

        high_loss = worst_host is not None and worst_loss > 0.05
        congested = max_util > UTIL_THRESHOLD
        self.congested_polls = self.congested_polls + 1 if congested else 0

        # Attribute congestion to the host actually sending the most traffic
        # (measured from OpenFlow port stats), not the host with the most loss.
        offender, offender_bps = None, 0.0
        for host, bps in self.host_ingress_bps.items():
            if bps > offender_bps:
                offender, offender_bps = host, bps
        if offender_bps < OFFENDER_MIN_BPS:
            offender = None

        if (self.congested_polls >= CONGESTION_CONFIRM_POLLS and offender
                and not self.mitigation_active):
            loss_txt = f' + telemetry loss {worst_loss*100:.1f}% ({worst_host})' if high_loss else ''
            self._alert(f'INTEGRATION ALERT: Util {max_util*100:.1f}%{loss_txt}; '
                        f'top sender {offender} at {offender_bps/1e6:.2f} Mbps. MITIGATING.')
            self._apply_mitigation(offender)
        elif self.mitigation_active and not congested:
            # The DROP rule sits on s1, so the offender's packets still count as
            # rx on its s1 port. Only declare recovery once the source stopped.
            src_bps = self.host_ingress_bps.get(self.mitigated_host, 0.0)
            if src_bps < OFFENDER_MIN_BPS:
                self._remove_mitigation()

        self._write_metrics(max_util)

    def _write_metrics(self, utilization):
        try:
            metrics = {'utilization': utilization, 'mitigation_active': self.mitigation_active,
                       'mitigated_host': self.mitigated_host,
                       'host_ingress_mbps': {h: round(b / 1e6, 3) for h, b in self.host_ingress_bps.items()},
                       'utilization_timestamp': self.current_utilization_timestamp,
                       'snapshot_timestamp': time.time()}
            with open(METRICS_FILE + '.tmp', 'w') as f: json.dump(metrics, f)
            os.replace(METRICS_FILE + '.tmp', METRICS_FILE)
        except OSError: pass

    def _remove_mitigation(self):
        dp = self.datapaths.get(1)
        if not dp:
            self.logger.warning("Cannot remove mitigation: datapath 1 unavailable")
            return

        ofp = dp.ofproto
        parser = dp.ofproto_parser

        mod = parser.OFPFlowMod(
            datapath=dp,
            cookie=0x4E45545741544348,
            cookie_mask=0xFFFFFFFFFFFFFFFF,
            table_id=ofp.OFPTT_ALL,
            command=ofp.OFPFC_DELETE,
            out_port=ofp.OFPP_ANY,
            out_group=ofp.OFPG_ANY,
            match=parser.OFPMatch()
        )
        dp.send_msg(mod)
        self.mitigation_active = False
        self.mitigated_host = None
        self._alert('MITIGATION REMOVED: Network recovered; DROP rule deleted.')

    def _apply_mitigation(self, host):
        dp = self.datapaths.get(1)
        if not dp or not host:
            self.logger.warning("Cannot mitigate: datapath 1 or host target missing")
            return
        parser = dp.ofproto_parser
        src_ip = HOST_IPS.get(host)
        if src_ip is None:
            self.logger.warning("Cannot mitigate: unknown host %r", host)
            return
        self.mitigation_active = True
        self.mitigated_host = host

        match = parser.OFPMatch(eth_type=0x0800, ipv4_src=src_ip, ipv4_dst='10.0.0.5', ip_proto=17, udp_dst=5555)
        self.add_flow(dp, 100, match, [], hard=15, flags=ofproto_v1_3.OFPFF_SEND_FLOW_REM, cookie=0x4E45545741544348)
        self._alert(f'MITIGATION ACTIVE: Installed DROP rule for {host}->h5')

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def flow_removed_handler(self, ev):
        if (ev.msg.cookie == 0x4E45545741544348
                and ev.msg.reason == ev.msg.datapath.ofproto.OFPRR_HARD_TIMEOUT):
            self._alert('MITIGATION EXPIRED: Drop rule removed after hard timeout.')
            self.mitigation_active = False
            self.mitigated_host = None
            self.congested_polls = 0  # re-mitigates if the flood is still running

    def _alert(self, msg):
        line = f'[{time.strftime("%H:%M:%S")}] ALERT: {msg}'
        self.logger.warning(line)
        try:
            with open(LOG_FILE, 'a') as f: f.write(line + '\n')
            os.chmod(LOG_FILE, 0o644)
        except OSError: pass

if __name__ == '__main__':
    app_manager.main()
