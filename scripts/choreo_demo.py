#!/usr/bin/env python3
"""Boomy choreography demo -- edit steps below to compose a routine."""
import sys
from choreo_lib import action, mode, move, parse_args, run_program


def build_steps(program: str, teach_index: int, turn_dir: str):
    turn_z = 1.0 if turn_dir == "left" else -1.0
    if program == "crab":
        return [
            mode("FULLWALK"),
            move(0, 1.0, 0, 1.5),               # strafe left
            move(0, -1.0, 0, 1.5),              # strafe right
        ]
    return [
        mode("WALK"),
        move(1.0, 0, 0, 2.0),                   # walk into frame
        move(0, 0, turn_z, 1.5),                # turn to face camera
        action("SWING"),                        # wave
        action("PLAYTEACH", index=teach_index), # play taught gesture
        action("CHEER"),                        # cheer
    ]


if __name__ == "__main__":
    args = parse_args()
    steps = build_steps(args.program, args.teach_index, args.turn)
    sys.exit(run_program(args.program, steps, args))
