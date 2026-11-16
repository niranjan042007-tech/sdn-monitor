#!/usr/bin/env python3
"""Native Desktop UI for SDN Network Monitoring (Control Panel Only)."""
import tkinter as tk
from tkinter import ttk
import json
import os
import socket
import threading
import time

CONTROL_SOCK = os.environ.get('NETWATCH_CONTROL_SOCKET', '/tmp/sdn_control.sock')

def send_control_command(cmd, host, rate='8', duration=20):
    """Send validated experiment command to the Mininet topology via Unix socket."""
    payload = json.dumps({
        'cmd': cmd,
        'host': host,
        'rate': str(rate),
        'duration': int(duration),
    }) + '\n'
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        sock.connect(CONTROL_SOCK)
        sock.sendall(payload.encode())
        raw = b''
        while not raw.endswith(b'\n'):
            chunk = sock.recv(4096)
            if not chunk: break
            raw += chunk
        sock.close()
        return json.loads(raw.decode().strip())
    except FileNotFoundError:
        return {'status': 'error', 'message': 'Control socket /tmp/sdn_control.sock not found. Start Mininet first.'}
    except Exception as exc:
        return {'status': 'error', 'message': f'Socket error: {exc}'}

BG_COLOR = "#1e1e2e"
FG_COLOR = "#cdd6f4"
BOX_COLOR = "#313244"
ACCENT = "#89b4fa"
CRITICAL = "#f38ba8"
OK = "#a6e3a1"
WARNING = "#f9e2af"

active_problems = {}
lock = threading.Lock()

class SDNDashboard:
    def __init__(self, root):
        self.root = root
        self.root.title("SDN Network Control Center")
        self.root.geometry("1000x700")
        self.root.configure(bg=BG_COLOR)

        # Go straight to the dashboard
        self.build_dashboard()

    def build_dashboard(self):
        for widget in self.root.winfo_children():
            widget.destroy()

        top_frame = tk.Frame(self.root, bg=BG_COLOR)
        top_frame.pack(fill="x", pady=10)
        tk.Label(top_frame, text="🌐 SDN Control Center", font=("Arial", 20, "bold"), bg=BG_COLOR, fg=ACCENT).pack()

        main_frame = tk.Frame(self.root, bg=BG_COLOR)
        main_frame.pack(fill="both", expand=True, padx=20)

        # Left: Buttons
        left_frame = tk.Frame(main_frame, bg=BOX_COLOR, padx=15, pady=15)
        left_frame.pack(side="left", fill="both", expand=True, padx=10)

        tk.Label(left_frame, text="🎮 Problem Injection", font=("Arial", 14, "bold"), bg=BOX_COLOR, fg=WARNING).pack(pady=5)

        sel_frame = tk.Frame(left_frame, bg=BOX_COLOR)
        sel_frame.pack(fill="x", pady=10)
        self.target_host = ttk.Combobox(sel_frame, values=["h1", "h2", "h3", "h4", "h5", "h6"])
        self.target_host.set("h1")
        self.target_host.pack(side="left", fill="x", expand=True, padx=5)
        self.target_rate = ttk.Combobox(sel_frame, values=["8 Mbps", "5 Mbps", "10 Mbps"])
        self.target_rate.set("8 Mbps")
        self.target_rate.pack(side="right", fill="x", expand=True, padx=5)

        btn_frame = tk.Frame(left_frame, bg=BOX_COLOR)
        btn_frame.pack(fill="both", expand=True)

        self.buttons = {}
        probs = [
            ("flood", "🌊 Flood Attack", "Send UDP flood"),
            ("cpu", "🔥 CPU Stress", "Max out CPU"),
            ("mem", "💾 MEM Stress", "Allocate 500MB RAM"),
            ("kill", "💀 Kill Agent", "Silent Host"),
            ("burst", "⚡ Micro-Burst", "2 Mbps for 5s")
        ]

        for i, (pid, name, desc) in enumerate(probs):
            btn = tk.Button(btn_frame, text=f"{name}\n{desc}", bg="#45475a", fg=FG_COLOR, font=("Arial", 10, "bold"),
                            command=lambda p=pid: self.trigger_problem(p), justify="left", anchor="w")
            btn.grid(row=i//2, column=i%2, padx=5, pady=5, sticky="nsew")
            self.buttons[pid] = btn

        btn_frame.columnconfigure(0, weight=1)
        btn_frame.columnconfigure(1, weight=1)

        # Right: Telemetry and Alerts
        right_frame = tk.Frame(main_frame, bg=BOX_COLOR, padx=15, pady=15)
        right_frame.pack(side="right", fill="both", expand=True, padx=10)

        tk.Label(right_frame, text="📈 Live Telemetry (CPU/MEM are VM-wide)", font=("Arial", 14, "bold"), bg=BOX_COLOR, fg=WARNING).pack(pady=5)

        self.tree = ttk.Treeview(right_frame, columns=("Host", "VM CPU (shared)", "VM MEM (shared)", "Host Loss", "Status"), show="headings", height=6)
        for col in self.tree["columns"]:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=80)
        self.tree.pack(fill="x", pady=10)

        self.util_label = tk.Label(right_frame, text="✅ Healthy: 0.0%", bg=OK, fg=BG_COLOR, font=("Arial", 12, "bold"))
        self.util_label.pack(fill="x", pady=5)

        tk.Label(right_frame, text="🔔 Live Alerts", font=("Arial", 14, "bold"), bg=BOX_COLOR, fg=WARNING).pack(pady=5)

        self.alert_box = tk.Text(right_frame, height=8, bg=BG_COLOR, fg=WARNING, font=("Courier", 9), state="disabled")
        self.alert_box.pack(fill="both", expand=True)

        self.update_ui()

    def trigger_problem(self, prob_id):
        host = self.target_host.get()
        rate = self.target_rate.get().split()[0]
        duration = 5 if prob_id == "burst" else 20

        with lock:
            active_problems[prob_id] = time.time() + duration

        btn = self.buttons[prob_id]
        btn.config(bg=CRITICAL, fg=BG_COLOR)

        resp = send_control_command(prob_id, host, rate=rate, duration=duration)
        if resp.get('status') == 'error':
            with lock:
                active_problems.pop(prob_id, None)
            btn.config(bg="#45475a", fg=FG_COLOR)
            try:
                self.alert_box.config(state="normal")
                self.alert_box.insert(tk.END, f"\n[IPC ERROR] {resp.get('message')}\n")
                self.alert_box.see(tk.END)
                self.alert_box.config(state="disabled")
            except Exception:
                pass

    def update_ui(self):
        if not hasattr(self, 'tree'):
            return

        with lock:
            now = time.time()
            for prob_id in list(active_problems.keys()):
                remaining = active_problems[prob_id] - now
                btn = self.buttons[prob_id]
                if remaining > 0:
                    base_text = btn.cget("text").split("\n")[0]
                    btn.config(text=f"{base_text}\n({int(remaining)}s)")
                else:
                    del active_problems[prob_id]
                    btn.config(bg="#45475a", fg=FG_COLOR)
                    for p_id, _, desc in [("flood", "", "Send UDP flood"), ("cpu", "", "Max out CPU"), ("mem", "", "Allocate 500MB RAM"), ("kill", "", "Silent Host"), ("burst", "", "2 Mbps for 5s")]:
                        if p_id == prob_id:
                            btn.config(text=f"{btn.cget('text').split(chr(10))[0].split('(')[0]}\n{desc}")

        try:
            with open('/tmp/server_state.json', 'r') as f:
                telemetry = json.load(f)
        except Exception:
            telemetry = {}

        utilization = 0.0
        try:
            # Read from sdn_metrics.json (same source as the Flask dashboard).
            # The 'utilization' field is already a 0.0–1.0+ ratio; multiply
            # by 100 once to get a percentage.
            # Previously this code re-parsed sdn_alerts.log and multiplied
            # the already-ratio value by 100 again (Bug B5), producing values
            # up to 10,000 %. Fixed here by reading the authoritative source.
            with open('/tmp/sdn_metrics.json', 'r') as f:
                metrics = json.load(f)
                utilization = metrics.get('utilization', 0.0) * 100
        except Exception:
            pass

        for item in self.tree.get_children():
            self.tree.delete(item)

        for host, info in telemetry.items():
            cpu = info.get('cpu', 0)
            mem = info.get('mem', 0)
            loss = info.get('loss', 0) * 100
            host_status = info.get('status', 'OK')
            if host_status == "DOWN":
                status = "🔴 DOWN"
            elif loss > 5:
                status = "🔴 LOSS"
            else:
                # CPU/MEM are shared VM-wide readings, not host-specific health.
                status = "🟢 HOST OK"

            self.tree.insert("", "end", values=(host, f"{cpu:.1f}", f"{mem:.1f}", f"{loss:.1f}", status))

        if utilization > 80:
            self.util_label.config(text=f"⚠️ CONGESTION: {utilization:.1f}%", bg=CRITICAL)
        else:
            self.util_label.config(text=f"✅ Healthy: {utilization:.1f}%", bg=OK)

        try:
            with open('/tmp/sdn_alerts.log', 'r') as f:
                lines = f.readlines()[-10:]
            self.alert_box.config(state="normal")
            self.alert_box.delete(1.0, tk.END)
            self.alert_box.insert(tk.END, "".join(lines))
            self.alert_box.config(state="disabled")
        except Exception:
            pass

        self.root.after(1000, self.update_ui)

if __name__ == '__main__':
    root = tk.Tk()
    app = SDNDashboard(root)
    root.mainloop()
