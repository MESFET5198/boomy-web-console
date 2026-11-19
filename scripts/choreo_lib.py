#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

WORKMODE_NAMES = {
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

MODE_TARGET = {"WALK": 2, "FULLWALK": 3}
FAULT_MODES = {26, 30}


class ChoreoAbort(RuntimeError):
    """Raised when the choreography must abort safely."""


@dataclass
class Step:
    kind: str
    label: str
    est_seconds: float
    fn: Callable[["ChoreoClient"], str]


def mode(name: str) -> Step:
    name = name.upper()
    if name not in MODE_TARGET:
        raise ValueError(f"Unsupported mode: {name}")
    return Step("mode", f"MODE:{name}", 1.0, lambda c: c.exec_mode(name))


def move(x: float, y: float, z: float, seconds: float) -> Step:
    label = f"MOVE({x:+.1f},{y:+.1f},{z:+.1f},{seconds:.1f}s)"
    return Step("move", label, float(seconds), lambda c: c.exec_move(x, y, z, seconds))


def action(name: str, index: int = 0) -> Step:
    name = name.upper()
    label = f"{name}#{index}" if name == "PLAYTEACH" else name
    return Step("action", label, 3.0, lambda c: c.exec_action(name, index))


def pause(seconds: float) -> Step:
    return Step("pause", f"PAUSE({seconds:.1f}s)", float(seconds), lambda c: c.exec_pause(seconds))


class ChoreoClient:
    def __init__(
        self,
        server: str,
        speed_scale: float = 0.5,
        poll_interval: float = 0.1,
        mode_timeout: float = 8.0,
        action_start_timeout: float = 3.0,
        action_done_timeout: float = 30.0,
    ):
        self.server = server.rstrip("/")
        self.speed_scale = max(0.0, min(1.0, float(speed_scale)))
        self.poll_interval = poll_interval
        self.mode_timeout = mode_timeout
        self.action_start_timeout = action_start_timeout
        self.action_done_timeout = action_done_timeout
        self.t0 = time.monotonic()
        self.last_workmode: Optional[int] = None
        self.last_soc: Optional[int] = None

    def elapsed(self) -> str:
        dt = time.monotonic() - self.t0
        mins = int(dt // 60)
        secs = dt - mins * 60
        return f"{mins:02d}:{secs:04.1f}"

    def _request(self, method: str, path: str, payload: Optional[dict] = None, timeout: float = 3.0) -> dict:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(f"{self.server}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def get_status(self) -> dict:
        return self._request("GET", "/api/status")

    def post_cmd(self, cmd: str, index: int = 0) -> dict:
        return self._request("POST", "/api/cmd", {"action": cmd, "index": index})

    def post_vel(self, x: float, y: float, z: float) -> dict:
        return self._request("POST", "/api/vel", {"x": x, "y": y, "z": z})

    def post_stop(self) -> None:
        try:
            self._request("POST", "/api/stop", {}, timeout=2.0)
        except Exception as exc:
            print(f"[{self.elapsed()}] WARN: /api/stop failed: {exc}", flush=True)

    def _observe_status(self, snap: dict) -> int:
        if not snap.get("connected") or not snap.get("status"):
            raise ChoreoAbort("机器人连接断开 (connected=false)")
        st = snap["status"]
        wm = int(st["workmode"])
        self.last_soc = int(st["bms"]["soc"])
        if self.last_workmode is not None and wm != self.last_workmode:
            wm_name = WORKMODE_NAMES.get(wm, "未知")
            print(f"[{self.elapsed()}]   workmode change: {self.last_workmode} -> {wm} ({wm_name})", flush=True)
        self.last_workmode = wm
        if wm in FAULT_MODES:
            self.post_stop()
            raise ChoreoAbort(f"触发安全中止: workmode={wm} ({WORKMODE_NAMES.get(wm, '')})")
        return wm

    def preflight(self) -> None:
        try:
            snap = self.get_status()
        except Exception as exc:
            raise ChoreoAbort(f"无法连接 {self.server}: {exc}")
        if not snap.get("connected") or not snap.get("status"):
            raise ChoreoAbort("机器人未连接 (connected=false)")
        st = snap["status"]
        wm = int(st["workmode"])
        soc = int(st["bms"]["soc"])
        self.last_workmode = wm
        self.last_soc = soc
        if wm not in (2, 3):
            raise ChoreoAbort(f"workmode={wm} ({WORKMODE_NAMES.get(wm, '')})，必须先切换到 2(WALK) 或 3(FULLWALK)")
        if soc < 20:
            raise ChoreoAbort(f"电池电量过低: {soc}% (< 20%)")

    def exec_mode(self, name: str) -> str:
        target = MODE_TARGET[name]
        cur = self._observe_status(self.get_status())
        if cur == target:
            return f"workmode {cur}"
        self.post_cmd(name, 0)
        deadline = time.monotonic() + self.mode_timeout
        while time.monotonic() < deadline:
            wm = self._observe_status(self.get_status())
            if wm == target:
                return f"workmode {cur} -> {wm}"
            time.sleep(self.poll_interval)
        self.post_stop()
        raise ChoreoAbort(f"切换模式 {name}({target}) 超时 ({self.mode_timeout:.1f}s)")

    def exec_move(self, x: float, y: float, z: float, seconds: float) -> str:
        sx, sy, sz = x * self.speed_scale, y * self.speed_scale, z * self.speed_scale
        wm_start = self._observe_status(self.get_status())
        end_t = time.monotonic() + float(seconds)
        try:
            while True:
                self.post_vel(sx, sy, sz)
                self._observe_status(self.get_status())
                rem = end_t - time.monotonic()
                if rem <= 0:
                    break
                time.sleep(min(0.2, rem))
        finally:
            self.post_stop()
        return f"workmode {wm_start}"

    def exec_action(self, name: str, index: int = 0) -> str:
        base_wm = self._observe_status(self.get_status())
        self.post_cmd(name, index)

        # Phase 1: wait for workmode to leave base_wm (default 3s)
        started_wm = None
        start_deadline = time.monotonic() + self.action_start_timeout
        while time.monotonic() < start_deadline:
            wm = self._observe_status(self.get_status())
            if wm != base_wm:
                started_wm = wm
                break
            time.sleep(self.poll_interval)

        if started_wm is None:
            print(f"[{self.elapsed()}] WARN: 动作 {name} 未开始 ({self.action_start_timeout:.1f}s 超时)，继续下一步", flush=True)
            return f"workmode {base_wm} (no-start)"

        # Phase 2: wait for workmode to return to base_wm (default 30s)
        done_deadline = time.monotonic() + self.action_done_timeout
        while time.monotonic() < done_deadline:
            wm = self._observe_status(self.get_status())
            if wm == base_wm:
                return f"workmode {base_wm} → {started_wm} → {wm}"
            time.sleep(self.poll_interval)

        print(f"[{self.elapsed()}] WARN: 动作 {name} 未完成 ({self.action_done_timeout:.1f}s 超时)，停止并中止", flush=True)
        self.post_stop()
        raise ChoreoAbort(f"动作 {name} 未完成 ({self.action_done_timeout:.1f}s 超时)")

    def exec_pause(self, seconds: float) -> str:
        end_t = time.monotonic() + float(seconds)
        while time.monotonic() < end_t:
            self._observe_status(self.get_status())
            time.sleep(min(self.poll_interval, max(0.0, end_t - time.monotonic())))
        return f"workmode {self.last_workmode}"


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Boomy Choreography Runner")
    p.add_argument("--server", default="http://192.168.55.101:8080")
    p.add_argument("--program", choices=["outro", "crab"], default="outro")
    p.add_argument("--teach-index", type=int, default=1)
    p.add_argument("--speed-scale", type=float, default=0.5)
    p.add_argument("--turn", choices=["left", "right"], default="left")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--step", action="store_true")
    return p.parse_args(argv)


def run_program(
    program_name: str,
    steps: List[Step],
    args: argparse.Namespace,
    poll_interval: float = 0.1,
) -> int:
    total = len(steps)
    est_total = sum(s.est_seconds for s in steps)
    print(f"Program: {program_name} | Steps: {total} | SpeedScale: {args.speed_scale:.2f}", flush=True)

    if args.dry_run:
        for idx, s in enumerate(steps, 1):
            print(f"[DRY-RUN] step {idx}/{total}  {s.label:<26s} est {s.est_seconds:.1f}s", flush=True)
        print(f"[DRY-RUN] Total estimated time: {est_total:.1f}s", flush=True)
        return 0

    client = ChoreoClient(args.server, speed_scale=args.speed_scale, poll_interval=poll_interval)
    try:
        client.preflight()
        for idx, s in enumerate(steps, 1):
            if args.step:
                input(f"[{client.elapsed()}] Press Enter for step {idx}/{total} ({s.label})...")
            wm_trace = s.fn(client)
            bat = f"battery {client.last_soc}%" if client.last_soc is not None else "battery --%"
            print(f"[{client.elapsed()}] step {idx}/{total}  {s.label:<26s} {wm_trace:<20s} {bat}", flush=True)
        print(f"Done in {time.monotonic() - client.t0:.1f}s.", flush=True)
        return 0
    except KeyboardInterrupt:
        client.post_stop()
        print("已停止", flush=True)
        return 130
    except ChoreoAbort as exc:
        print(f"ABORT: {exc}", file=sys.stderr, flush=True)
        return 1
