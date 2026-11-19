#!/usr/bin/env python3
"""Boomy choreography demo — edit STEPS to compose a new routine."""
import sys
from choreo_lib import action, mode, move, parse_args, run_program


def build_steps(program: str, teach_index: int, turn_dir: str):
    turn_z = 0.4 if turn_dir == "left" else -0.4
    if program == "crab":
        return [
            mode("FULLWALK"),
            move(0, 0.4, 0, 1.5),   # 左横移
            move(0, -0.4, 0, 1.5),  # 右横移回位
            action("CHEER"),        # 欢呼
        ]
    return [
        mode("WALK"),
        move(0.4, 0, 0, 2.0),               # 走进镜头
        move(0, 0, turn_z, 1.5),            # 转身面向镜头
        action("SWING"),                    # 挥手
        action("PLAYTEACH", index=teach_index),  # 播放示教：挥手 + 指向镜头
        action("CHEER"),                    # 欢呼
    ]


if __name__ == "__main__":
    args = parse_args()
    steps = build_steps(args.program, args.teach_index, args.turn)
    sys.exit(run_program(args.program, steps, args))
