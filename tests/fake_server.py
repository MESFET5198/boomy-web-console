#!/usr/bin/env python3
"""Standard-library fake Boomy HTTP server for automated testing."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeRobotState:
    def __init__(self, initial_workmode: int = 2, soc: int = 76):
        self.lock = threading.Lock()
        self.connected = True
        self.workmode = initial_workmode
        self.soc = soc
        self.requests = []  # list of (timestamp, method, path, body)
        self.vel_timestamps = []
        self.stop_count = 0
        self.disable_count = 0
        # Scripted action transitions: action_name -> (enter_wm, enter_delay, exit_wm, exit_delay)
        self.action_behavior = {
            "SWING": (8, 0.05, 2, 0.15),
            "CHEER": (10, 0.05, 2, 0.15),
            "PLAYTEACH": (13, 0.05, 2, 0.15),
            "WALK": (2, 0.05, None, None),
            "FULLWALK": (3, 0.05, None, None),
        }

    def record(self, method: str, path: str, body: dict):
        now = time.monotonic()
        with self.lock:
            self.requests.append((now, method, path, body))
            if path == "/api/vel":
                self.vel_timestamps.append(now)
            elif path == "/api/stop":
                self.stop_count += 1
            elif path == "/api/disable":
                self.disable_count += 1

    def trigger_action(self, action_name: str):
        spec = self.action_behavior.get(action_name)
        if not spec:
            return
        enter_wm, enter_delay, exit_wm, exit_delay = spec

        def _run():
            if enter_wm is not None:
                time.sleep(enter_delay)
                with self.lock:
                    self.workmode = enter_wm
            if exit_wm is not None and exit_delay is not None:
                time.sleep(exit_delay)
                with self.lock:
                    self.workmode = exit_wm

        threading.Thread(target=_run, daemon=True).start()


def start_fake_server(state: FakeRobotState):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def _send(self, code: int, payload: dict):
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/api/status":
                with state.lock:
                    snap = {
                        "connected": state.connected,
                        "age_ms": 10,
                        "status": {
                            "workmode": state.workmode,
                            "workmode_name": str(state.workmode),
                            "bms": {"soc": state.soc, "soh": 100, "temp": 25, "alarm": 0},
                        },
                    }
                state.record("GET", self.path, {})
                self._send(200, snap)
                return
            self._send(404, {"error": "not found"})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            state.record("POST", self.path, body)
            if self.path == "/api/cmd":
                state.trigger_action(str(body.get("action", "")).upper())
                self._send(200, {"ok": True})
                return
            if self.path in ("/api/vel", "/api/stop"):
                self._send(200, {"ok": True})
                return
            if self.path == "/api/disable":
                self._send(200, {"ok": True})
                return
            self._send(404, {"error": "not found"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}"
