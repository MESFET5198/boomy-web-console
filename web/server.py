#!/usr/bin/env python3
import argparse
import json
import os
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("CYCLONEDDS_URI", f"file://{os.path.join(ROOT_DIR, 'config', 'dds.xml')}")
sys.path.insert(0, os.path.join(ROOT_DIR, "build"))

from highcontrol_py import ControlCmd, HighController

WORKMODE_MAP = {
    0: "使能模式",
    1: "准备模式",
    2: "半身走路模式",
    3: "全身行走模式",
    4: "跑步模式",
    5: "舞蹈模式0",
    8: "预设动作·打招呼",
    9: "预设动作·握手",
    10: "预设动作·欢呼",
    11: "开始示教",
    12: "结束示教",
    13: "播放示教",
    14: "保存示教",
    26: "保护模式",
    27: "倒地起身",
    28: "起身倒地",
    29: "保存示教动作",
    30: "失能模式",
    31: "舞蹈模式1",
    32: "舞蹈模式2",
    33: "肘击动作",
}

STANDING_MODES = {2, 3, 5, 8, 9, 10, 13, 31, 32, 33}

JOINT_NAMES = [
    "arm_l1_joint (左肩pitch)",
    "arm_l2_joint (左肩roll)",
    "arm_l3_joint (左臂yaw)",
    "arm_l4_joint (左肘pitch)",
    "leg_l1_joint (左髋pitch)",
    "leg_l2_joint (左髋roll)",
    "leg_l3_joint (左腿yaw)",
    "leg_l4_joint (左膝pitch)",
    "leg_l5_joint (左踝pitch)",
    "leg_l6_joint (左踝roll)",
    "arm_r1_joint (右肩pitch)",
    "arm_r2_joint (右肩roll)",
    "arm_r3_joint (右臂yaw)",
    "arm_r4_joint (右肘pitch)",
    "leg_r1_joint (右髋pitch)",
    "leg_r2_joint (右髋roll)",
    "leg_r3_joint (右腿yaw)",
    "leg_r4_joint (右膝pitch)",
    "leg_r5_joint (右踝pitch)",
    "leg_r6_joint (右踝roll)",
    "waist_1_joint (腰yaw)",
]


class RobotBridge:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest_status = None
        self.last_rx_time = 0.0
        self._last_parse_mono = 0.0
        self._last_wm = None
        self.initialized = False
        self.vel = [0.0, 0.0, 0.0]
        self.vel_updated_at = 0.0
        self.vel_timeout_s = 0.6
        self.cmd_queue = queue.Queue()

        self.ctrl = None
        self._stop = False
        self._thread = threading.Thread(target=self._init_and_loop, daemon=True)
        self._thread.start()

    def _is_robot_reachable(self):
        import subprocess
        res = subprocess.run(
            ["ping", "-c", "1", "-W", "1", "192.168.55.102"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0

    def _init_and_loop(self):
        while not self._stop:
            if not self._is_robot_reachable():
                time.sleep(1.0)
                continue
            try:
                if self.ctrl is None:
                    self.ctrl = HighController.instance()
                ok = self.ctrl.init()
                print(f"[WebBridge] HighController.init() -> {ok}", flush=True)
                if ok:
                    self.ctrl.subscribe_robot_hardware_status(self._on_status)
                    self.initialized = True
                    break
            except Exception as exc:
                print(f"[WebBridge] init error: {exc}", flush=True)
            time.sleep(1.0)
        self._control_loop()

    def _on_status(self, status):
        # Throttle heavy Python dict construction to ~20Hz (unless workmode changes)
        # to avoid GIL contention with the 100Hz publish_cmd loop.
        now_mono = time.monotonic()
        wm = int(status.workmode)
        if wm == self._last_wm and (now_mono - self._last_parse_mono) < 0.05:
            self.last_rx_time = time.time()
            return
        self._last_parse_mono = now_mono
        self._last_wm = wm

        bms = status.bms_data
        imu = status.imu_data
        motors = []
        for idx, m in enumerate(status.motor_data):
            motors.append({
                "id": int(m.motor_id) if m.motor_id else idx,
                "name": JOINT_NAMES[idx] if idx < len(JOINT_NAMES) else f"joint_{idx}",
                "pos": round(float(m.pos), 3),
                "vel": round(float(m.vel), 3),
                "tau": round(float(m.tau), 3),
                "temp": int(m.temperature),
                "error": int(m.error),
            })

        snap = {
            "workmode": wm,
            "workmode_name": WORKMODE_MAP.get(wm, f"未知模式({wm})"),
            "bms": {
                "soc": int(bms.battery_soc),
                "soh": int(bms.battery_soh),
                "temp": int(bms.battery_temp),
                "alarm": int(bms.battery_alarm),
            },
            "imu": {
                "ori": [round(float(v), 4) for v in imu.ori],
                "angular_vel": [round(float(v), 3) for v in imu.angular_vel],
                "linear_acc": [round(float(v), 3) for v in imu.linear_acc],
            },
            "motors": motors,
        }
        with self.lock:
            self.latest_status = snap
            self.last_rx_time = time.time()

    def _control_loop(self):
        # 100Hz (10ms), matches official examples/py/test_high.py
        while not self._stop:
            now = time.time()
            with self.lock:
                if now - self.vel_updated_at > self.vel_timeout_s:
                    self.vel = [0.0, 0.0, 0.0]
                vx, vy, vz = self.vel

            action = ControlCmd.DEFAULT
            index = 0
            try:
                action, index = self.cmd_queue.get_nowait()
            except queue.Empty:
                pass

            try:
                self.ctrl.publish_cmd(vx, vy, vz, action, index)
            except Exception as exc:
                print(f"[WebBridge] publish_cmd error: {exc}", flush=True)

            time.sleep(0.01)

    def send_action(self, action_name: str, index: int = 0):
        if not hasattr(ControlCmd, action_name):
            raise ValueError(f"Unknown ControlCmd: {action_name}")
        cmd_enum = getattr(ControlCmd, action_name)
        if action_name != "DEFAULT":
            with self.lock:
                self.vel = [0.0, 0.0, 0.0]
                self.vel_updated_at = 0.0
        self.cmd_queue.put((cmd_enum, max(0, int(index))))

    def set_velocity(self, x: float, y: float, z: float):
        def clamp(v):
            return max(-1.0, min(1.0, float(v)))

        with self.lock:
            self.vel = [clamp(x), clamp(y), clamp(z)]
            self.vel_updated_at = time.time()

    def _clear_cmd_queue(self):
        # queue.Queue is internally thread-safe; never hold self.lock while draining it
        while True:
            try:
                self.cmd_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self):
        with self.lock:
            self.vel = [0.0, 0.0, 0.0]
            self.vel_updated_at = 0.0
            cur_mode = self.latest_status["workmode"] if self.latest_status else None
        self._clear_cmd_queue()
        return cur_mode

    def disable(self, confirm: bool = False, force: bool = False):
        if not confirm:
            return 400, {"ok": False, "error": "Confirmation required: {\"confirm\": true}"}
        with self.lock:
            cur_mode = self.latest_status["workmode"] if self.latest_status else None
        if cur_mode in STANDING_MODES and not force:
            return 409, {
                "ok": False,
                "workmode": cur_mode,
                "error": "站立状态下失能会摔倒，请先收纳",
            }
        self.stop()
        self.cmd_queue.put((ControlCmd.START, 0))
        return 200, {"ok": True, "prev_mode": cur_mode}

    def get_snapshot(self):
        now = time.time()
        with self.lock:
            age_ms = int((now - self.last_rx_time) * 1000) if self.last_rx_time else None
            connected = age_ms is not None and age_ms < 2000
            return {
                "connected": connected,
                "age_ms": age_ms,
                "cmd_vel": {"x": self.vel[0], "y": self.vel[1], "z": self.vel[2]},
                "status": self.latest_status,
            }


BRIDGE = None
INDEX_HTML_PATH = os.path.join(os.path.dirname(__file__), "index.html")


class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy polling logs
        pass

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            with open(INDEX_HTML_PATH, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path == "/api/status":
            self._send_json(200, BRIDGE.get_snapshot())
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            data = self._read_json_body()
        except Exception as exc:
            self._send_json(400, {"error": f"Invalid JSON: {exc}"})
            return

        if self.path == "/api/cmd":
            action = str(data.get("action", "")).upper()
            index = int(data.get("index", 0))
            try:
                BRIDGE.send_action(action, index)
                self._send_json(200, {"ok": True, "action": action, "index": index})
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": str(exc)})
            return

        if self.path == "/api/vel":
            BRIDGE.set_velocity(data.get("x", 0.0), data.get("y", 0.0), data.get("z", 0.0))
            self._send_json(200, {"ok": True})
            return

        if self.path == "/api/stop":
            prev_mode = BRIDGE.stop()
            self._send_json(200, {"ok": True, "prev_mode": prev_mode})
            return

        if self.path == "/api/disable":
            confirm = bool(data.get("confirm", False))
            force = bool(data.get("force", False))
            code, payload = BRIDGE.disable(confirm=confirm, force=force)
            self._send_json(code, payload)
            return

        if self.path == "/api/estop":
            print("[WebBridge] WARNING: POST /api/estop is deprecated; use /api/stop instead.", flush=True)
            prev_mode = BRIDGE.stop()
            self._send_json(200, {"ok": True, "prev_mode": prev_mode, "deprecated": True})
            return

        self._send_json(404, {"error": "Not found"})


def main():
    global BRIDGE
    parser = argparse.ArgumentParser(description="Boomy HighController Web Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind host (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="Bind port (default: 8080)")
    args = parser.parse_args()

    BRIDGE = RobotBridge()
    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    print(f"[WebBridge] Listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WebBridge] Shutting down...", flush=True)
        server.shutdown()


if __name__ == "__main__":
    main()
