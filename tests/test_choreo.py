#!/usr/bin/env python3
import io
import json
import os
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from http.server import ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "tests"))

import choreo_demo
import choreo_lib
import teach_probe
from fake_server import FakeRobotState, start_fake_server


class TestChoreoAndSafety(unittest.TestCase):
    def setUp(self):
        self.state = FakeRobotState(initial_workmode=2, soc=76)
        self.srv, self.url = start_fake_server(self.state)

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def test_1_move_vel_interval_and_stop(self):
        client = choreo_lib.ChoreoClient(self.url, speed_scale=0.5, poll_interval=0.02)
        client.preflight()
        client.exec_move(1.0, 0.0, 0.0, 0.7)

        ts = self.state.vel_timestamps
        self.assertGreaterEqual(len(ts), 3)
        for a, b in zip(ts, ts[1:]):
            self.assertLessEqual(b - a, 0.25, f"vel interval {b - a:.3f}s > 0.25s")
        vel_xs = [b["x"] for _, _, p, b in self.state.requests if p == "/api/vel"]
        self.assertTrue(all(abs(v - 0.5) < 1e-6 for v in vel_xs))
        self.assertEqual(self.state.stop_count, 1)

    def test_2_action_start_and_complete(self):
        client = choreo_lib.ChoreoClient(
            self.url, poll_interval=0.02, action_start_timeout=1.0, action_done_timeout=2.0
        )
        client.preflight()
        trace = client.exec_action("SWING")
        self.assertIn("2", trace)
        self.assertIn("8", trace)
        self.assertTrue(trace.endswith("2"))

    def test_3_action_no_start_timeout_warns_and_continues(self):
        self.state.action_behavior["SWING"] = (None, 0.0, None, None)
        client = choreo_lib.ChoreoClient(
            self.url, poll_interval=0.02, action_start_timeout=0.25, action_done_timeout=1.0
        )
        client.preflight()
        buf = io.StringIO()
        with redirect_stdout(buf):
            trace = client.exec_action("SWING")
        self.assertIn("未开始", buf.getvalue())
        self.assertIn("no-start", trace)
        self.assertEqual(self.state.stop_count, 0)

    def test_4_action_not_done_timeout_stops_and_aborts(self):
        self.state.action_behavior["SWING"] = (8, 0.03, None, None)
        client = choreo_lib.ChoreoClient(
            self.url, poll_interval=0.02, action_start_timeout=0.5, action_done_timeout=0.25
        )
        client.preflight()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(choreo_lib.ChoreoAbort):
                client.exec_action("SWING")
        self.assertIn("未完成", buf.getvalue())
        self.assertGreaterEqual(self.state.stop_count, 1)

    def test_5_fault_workmode_26_or_30_aborts_immediately(self):
        for fault_wm in (26, 30):
            with self.subTest(fault_wm=fault_wm):
                state = FakeRobotState(initial_workmode=2, soc=80)
                state.action_behavior["SWING"] = (fault_wm, 0.05, None, None)
                srv, url = start_fake_server(state)
                try:
                    client = choreo_lib.ChoreoClient(
                        url, poll_interval=0.02, action_start_timeout=0.5, action_done_timeout=1.0
                    )
                    client.preflight()
                    with self.assertRaises(choreo_lib.ChoreoAbort):
                        client.exec_action("SWING")
                    self.assertGreaterEqual(state.stop_count, 1)
                finally:
                    srv.shutdown()
                    srv.server_close()

    def test_6_ctrl_c_calls_stop_only_never_disable(self):
        def interrupt_fn(_client):
            raise KeyboardInterrupt()

        steps = [choreo_lib.Step("pause", "INTERRUPT", 1.0, interrupt_fn)]
        args = choreo_lib.parse_args(["--server", self.url])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = choreo_lib.run_program("outro", steps, args, poll_interval=0.02)
        self.assertEqual(rc, 130)
        self.assertIn("已停止", buf.getvalue())
        self.assertGreaterEqual(self.state.stop_count, 1)
        self.assertEqual(self.state.disable_count, 0)
        cmd_actions = [b.get("action") for _, _, p, b in self.state.requests if p == "/api/cmd"]
        self.assertNotIn("START", cmd_actions)

    def test_7_disable_returns_409_in_standing_mode_without_force(self):
        # Inject a mock highcontrol_py so we can test the real web/server.py handler
        class FakeCmd:
            WALK = 0
            SWING = 1
            SHAKE = 2
            CHEER = 3
            FULLWALK = 4
            START = 5
            SWITCH = 6
            STARTTEACH = 7
            SAVETEACH = 8
            ENDTEACH = 9
            PLAYTEACH = 10
            DANCE0 = 11
            FALLTOSTAND = 12
            STANDTOFALL = 13
            DANCE1 = 14
            DANCE2 = 15
            ZHOUJI = 16
            DEFAULT = 17

        class FakeCtrl:
            def init(self):
                return True

            def subscribe_robot_hardware_status(self, cb):
                pass

            def publish_cmd(self, x, y, z, action, index):
                pass

        class FakeHC:
            ControlCmd = FakeCmd

            class HighController:
                @staticmethod
                def instance():
                    return FakeCtrl()

        sys.modules["highcontrol_py"] = FakeHC
        sys.path.insert(0, os.path.join(ROOT, "web"))
        import server as real_server

        bridge = object.__new__(real_server.RobotBridge)
        bridge.lock = threading.Lock()
        bridge.latest_status = {"workmode": 2, "bms": {"soc": 80}}
        bridge.last_rx_time = time.time()
        bridge.vel = [0.0, 0.0, 0.0]
        bridge.vel_updated_at = 0.0
        bridge.vel_timeout_s = 0.6
        import queue
        bridge.cmd_queue = queue.Queue()
        real_server.BRIDGE = bridge

        srv = ThreadingHTTPServer(("127.0.0.1", 0), real_server.RequestHandler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/disable",
                data=json.dumps({"confirm": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(req, timeout=2.0)
            self.assertEqual(ctx.exception.code, 409)
            err_body = json.loads(ctx.exception.read().decode("utf-8"))
            self.assertIn("摔倒", err_body["error"])

            # With force=True, it succeeds with 200
            req2 = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/disable",
                data=json.dumps({"confirm": True, "force": True}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req2, timeout=2.0) as resp2:
                self.assertEqual(resp2.status, 200)
                self.assertTrue(json.loads(resp2.read().decode("utf-8"))["ok"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_8_dry_run_makes_zero_http_requests(self):
        args = choreo_lib.parse_args(["--server", self.url, "--program", "outro", "--dry-run"])
        steps = choreo_demo.build_steps(args.program, args.teach_index, args.turn)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = choreo_lib.run_program(args.program, steps, args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.state.requests), 0)
        self.assertIn("DRY-RUN", buf.getvalue())

    def test_9_min_speed_warning_threshold(self):
        low_steps = [choreo_lib.move(0.4, 0.0, 0.0, 1.0)]
        buf_low = io.StringIO()
        with redirect_stdout(buf_low):
            w_low = choreo_lib.check_min_speed(low_steps, speed_scale=0.5, min_speed=0.5)
        self.assertEqual(len(w_low), 1)
        self.assertIn("x=0.20", buf_low.getvalue())
        self.assertIn("低于最低有效速度 0.50", buf_low.getvalue())

        ok_steps = [choreo_lib.move(1.0, 0.0, 0.0, 1.0)]
        buf_ok = io.StringIO()
        with redirect_stdout(buf_ok):
            w_ok = choreo_lib.check_min_speed(ok_steps, speed_scale=0.5, min_speed=0.5)
        self.assertEqual(len(w_ok), 0)
        self.assertEqual(buf_ok.getvalue(), "")

    def test_10_dry_run_prints_low_speed_warning(self):
        steps = [choreo_lib.move(0.4, 0.0, 0.0, 1.0)]
        args = choreo_lib.parse_args(
            ["--server", self.url, "--speed-scale", "0.5", "--min-speed", "0.5", "--dry-run"]
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = choreo_lib.run_program("test", steps, args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.state.requests), 0)
        self.assertIn("x=0.20", buf.getvalue())
        self.assertIn("低于最低有效速度 0.50", buf.getvalue())

    def test_11_teach_probe_continues_after_no_start(self):
        # Index 0 & 2 have no recorded action; Index 1 stays in workmode 13 until WALK is sent
        self.state.action_behavior[("PLAYTEACH", 0)] = (None, 0.0, None, None)
        self.state.action_behavior[("PLAYTEACH", 1)] = (13, 0.04, None, None)
        self.state.action_behavior[("PLAYTEACH", 2)] = (None, 0.0, None, None)
        client = choreo_lib.ChoreoClient(
            self.url, poll_interval=0.02, action_start_timeout=0.2, action_done_timeout=1.0
        )
        buf = io.StringIO()
        with redirect_stdout(buf):
            results = teach_probe.probe_indices(client, max_index=2, wait_enter=False, duration=0.15)
        self.assertEqual(len(results), 3)
        self.assertIn("no-start", results[0][1])
        self.assertIn("2 -> 13 -> 2", results[1][1])
        self.assertIn("no-start", results[2][1])
        walk_cmds = [b for _, _, p, b in self.state.requests if p == "/api/cmd" and b.get("action") == "WALK"]
        self.assertGreaterEqual(len(walk_cmds), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
