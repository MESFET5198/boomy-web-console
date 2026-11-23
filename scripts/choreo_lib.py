#!/usr/bin/env python3
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, List, Optional

WORKMODE_NAMES_ZH = {
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

WORKMODE_NAMES_EN = {
    0: "Enabled",
    1: "Ready",
    2: "Half Walk",
    3: "Full Walk",
    4: "Running",
    5: "Dance 0",
    8: "Preset: Wave",
    9: "Preset: Shake",
    10: "Preset: Cheer",
    11: "Start Teach",
    12: "End Teach",
    13: "Play Teach",
    14: "Save Teach",
    26: "Protected",
    27: "Fall to Stand",
    28: "Stand to Fall",
    29: "Saving Teach",
    30: "Disabled",
    31: "Dance 1",
    32: "Dance 2",
    33: "Elbow Strike",
}

WORKMODE_NAMES = WORKMODE_NAMES_EN


def mode_name(wm: int, lang: str = "en") -> str:
    table = WORKMODE_NAMES_ZH if lang == "zh" else WORKMODE_NAMES_EN
    return table.get(wm, "未知" if lang == "zh" else "Unknown")

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
    vel: Optional[tuple] = None


def mode(name: str) -> Step:
    name = name.upper()
    if name not in MODE_TARGET:
        raise ValueError(f"Unsupported mode: {name}")
    return Step("mode", f"MODE:{name}", 1.0, lambda c: c.exec_mode(name))


def move(x: float, y: float, z: float, seconds: float) -> Step:
    label = f"MOVE({x:+.1f},{y:+.1f},{z:+.1f},{seconds:.1f}s)"
    return Step("move", label, float(seconds), lambda c: c.exec_move(x, y, z, seconds), vel=(float(x), float(y), float(z)))


def action(name: str, index: int = 0, duration: Optional[float] = None) -> Step:
    name = name.upper()
    if name == "PLAYTEACH":
        dur = 5.0 if duration is None else float(duration)
        label = f"{name}#{index}({dur:.1f}s)"
        return Step("action", label, dur, lambda c: c.exec_action(name, index, duration=dur))
    return Step("action", name, 3.0, lambda c: c.exec_action(name, index))


def pause(seconds: float) -> Step:
    return Step("pause", f"PAUSE({seconds:.1f}s)", float(seconds), lambda c: c.exec_pause(seconds))


def speak(text: Optional[str] = None, wav: Optional[str] = None, blocking: bool = False) -> Step:
    if not text and not wav:
        raise ValueError("speak() requires text or wav")
    short = (text[:18] + "...") if (text and len(text) > 18) else (text or wav)
    label = f"SPEAK({short})"
    return Step("speak", label, 0.2, lambda c: c.exec_speak(text=text, wav=wav, blocking=blocking))


class ChoreoClient:
    def __init__(
        self,
        server: str,
        speed_scale: float = 0.5,
        poll_interval: float = 0.1,
        mode_timeout: float = 8.0,
        action_start_timeout: float = 3.0,
        action_done_timeout: float = 30.0,
        lang: str = "en",
    ):
        self.server = server.rstrip("/")
        self.speed_scale = max(0.0, min(1.0, float(speed_scale)))
        self.poll_interval = poll_interval
        self.mode_timeout = mode_timeout
        self.action_start_timeout = action_start_timeout
        self.action_done_timeout = action_done_timeout
        self.lang = lang
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

    def post_speak(self, text: Optional[str] = None, wav: Optional[str] = None, blocking: bool = False) -> dict:
        payload = {"blocking": bool(blocking)}
        if wav:
            payload["wav"] = wav
        if text:
            payload["text"] = text
        return self._request("POST", "/api/speak", payload, timeout=15.0)

    def post_stop(self) -> None:
        try:
            self._request("POST", "/api/stop", {}, timeout=2.0)
        except Exception as exc:
            print(f"[{self.elapsed()}] WARN: /api/stop failed: {exc}", flush=True)

    def _observe_status(self, snap: dict) -> int:
        if not snap.get("connected") or not snap.get("status"):
            raise ChoreoAbort("Robot disconnected (connected=false)")
        st = snap["status"]
        wm = int(st["workmode"])
        self.last_soc = int(st["bms"]["soc"])
        if self.last_workmode is not None and wm != self.last_workmode:
            wm_name = mode_name(wm, self.lang)
            print(f"[{self.elapsed()}]   workmode change: {self.last_workmode} -> {wm} ({wm_name})", flush=True)
        self.last_workmode = wm
        if wm in FAULT_MODES:
            self.post_stop()
            raise ChoreoAbort(f"Safety abort: workmode={wm} ({mode_name(wm, self.lang)})")
        return wm

    def preflight(self) -> None:
        try:
            snap = self.get_status()
        except Exception as exc:
            raise ChoreoAbort(f"Cannot reach {self.server}: {exc}")
        if not snap.get("connected") or not snap.get("status"):
            raise ChoreoAbort("Robot not connected (connected=false)")
        st = snap["status"]
        wm = int(st["workmode"])
        soc = int(st["bms"]["soc"])
        self.last_workmode = wm
        self.last_soc = soc
        if wm not in (2, 3):
            raise ChoreoAbort(f"workmode={wm} ({mode_name(wm, self.lang)}), must be 2 (WALK) or 3 (FULLWALK)")
        if soc < 20:
            raise ChoreoAbort(f"Battery too low: {soc}% (< 20%)")

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

    def exec_action(self, name: str, index: int = 0, duration: Optional[float] = None) -> str:
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
            msg = (
                f"[{self.elapsed()}] WARN: 动作 {name} 未开始 ({self.action_start_timeout:.1f}s 超时)，继续下一步"
                if self.lang == "zh"
                else f"[{self.elapsed()}] WARN: action {name} did not start ({self.action_start_timeout:.1f}s timeout), continuing"
            )
            print(msg, flush=True)
            return f"workmode {base_wm} (no-start)"

        # PLAYTEACH holds its final pose in workmode 13 and does NOT auto-return to WALK (doc p.26).
        if name == "PLAYTEACH":
            hold_s = 5.0 if duration is None else float(duration)
            end_t = time.monotonic() + hold_s
            while time.monotonic() < end_t:
                wm = self._observe_status(self.get_status())
                if wm == base_wm:
                    return f"workmode {base_wm} -> {started_wm} -> {wm}"
                time.sleep(min(self.poll_interval, max(0.0, end_t - time.monotonic())))
            # Manually switch back to WALK (2)
            self.post_cmd("WALK", 0)
            ret_deadline = time.monotonic() + self.mode_timeout
            while time.monotonic() < ret_deadline:
                wm = self._observe_status(self.get_status())
                if wm == 2:
                    return f"workmode {base_wm} -> {started_wm} -> 2"
                time.sleep(self.poll_interval)
            self.post_stop()
            raise ChoreoAbort(f"示教播放后切回 WALK(2) 超时 ({self.mode_timeout:.1f}s)")

        # Phase 2: wait for workmode to return to base_wm (default 30s)
        done_deadline = time.monotonic() + self.action_done_timeout
        while time.monotonic() < done_deadline:
            wm = self._observe_status(self.get_status())
            if wm == base_wm:
                return f"workmode {base_wm} -> {started_wm} -> {wm}"
            time.sleep(self.poll_interval)

        msg = (
            f"[{self.elapsed()}] WARN: 动作 {name} 未完成 ({self.action_done_timeout:.1f}s 超时)，停止并中止"
            if self.lang == "zh"
            else f"[{self.elapsed()}] WARN: action {name} did not finish ({self.action_done_timeout:.1f}s timeout), stopping and aborting"
        )
        print(msg, flush=True)
        self.post_stop()
        raise ChoreoAbort(
            f"动作 {name} 未完成 ({self.action_done_timeout:.1f}s 超时)"
            if self.lang == "zh"
            else f"Action {name} timeout ({self.action_done_timeout:.1f}s)"
        )

    def exec_pause(self, seconds: float) -> str:
        end_t = time.monotonic() + float(seconds)
        while time.monotonic() < end_t:
            self._observe_status(self.get_status())
            time.sleep(min(self.poll_interval, max(0.0, end_t - time.monotonic())))
        return f"workmode {self.last_workmode}"

    def exec_speak(self, text: Optional[str] = None, wav: Optional[str] = None, blocking: bool = False) -> str:
        self._observe_status(self.get_status())
        res = self.post_speak(text=text, wav=wav, blocking=blocking)
        dur = res.get("duration_s")
        return f"audio {dur:.1f}s" if isinstance(dur, (int, float)) else "audio started"


def check_min_speed(steps: List[Step], speed_scale: float, min_speed: float, lang: str = "en") -> List[str]:
    warnings = []
    for idx, s in enumerate(steps, 1):
        if s.kind != "move" or not s.vel:
            continue
        for axis, raw in zip(("x", "y", "z"), s.vel):
            if raw == 0.0:
                continue
            eff = abs(raw * speed_scale)
            if eff < min_speed:
                msg = (
                    f"WARN: step {idx} {axis}={eff:.2f} 低于最低有效速度 {min_speed:.2f}，机器人可能不动"
                    if lang == "zh"
                    else f"WARN: step {idx} {axis}={eff:.2f} is below min effective speed {min_speed:.2f}, robot may not move"
                )
                print(msg, flush=True)
                warnings.append(msg)
    return warnings


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Boomy Choreography Runner")
    p.add_argument("--server", default="http://192.168.55.101:8080")
    p.add_argument("--program", choices=["outro", "crab"], default="outro")
    p.add_argument("--teach-index", type=int, default=1)
    p.add_argument("--teach-duration", type=float, default=5.0)
    p.add_argument("--say", default="Hey everyone! Check out our Kickstarter link below!")
    p.add_argument("--say-wav", default=None)
    p.add_argument("--speed-scale", type=float, default=0.5)
    p.add_argument("--min-speed", type=float, default=0.5)
    p.add_argument("--turn", choices=["left", "right"], default="left")
    p.add_argument("--lang", choices=["en", "zh"], default="en")
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
    min_speed = getattr(args, "min_speed", 0.5)
    lang = getattr(args, "lang", "en")
    print(f"Program: {program_name} | Steps: {total} | SpeedScale: {args.speed_scale:.2f}", flush=True)
    check_min_speed(steps, args.speed_scale, min_speed, lang=lang)

    if args.dry_run:
        for idx, s in enumerate(steps, 1):
            print(f"[DRY-RUN] step {idx}/{total}  {s.label:<26s} est {s.est_seconds:.1f}s", flush=True)
        print(f"[DRY-RUN] Total estimated time: {est_total:.1f}s", flush=True)
        return 0

    client = ChoreoClient(args.server, speed_scale=args.speed_scale, poll_interval=poll_interval, lang=lang)
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
        print("已停止" if lang == "zh" else "Stopped", flush=True)
        return 130
    except ChoreoAbort as exc:
        print(f"ABORT: {exc}", file=sys.stderr, flush=True)
        return 1
