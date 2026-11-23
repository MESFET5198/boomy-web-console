#!/usr/bin/env python3
"""Probe PLAYTEACH indices 0..N one by one to identify recorded gestures."""
import argparse
import sys
from typing import List, Optional

from choreo_lib import ChoreoAbort, ChoreoClient


def probe_indices(
    client: ChoreoClient,
    max_index: int = 5,
    wait_enter: bool = True,
) -> List[tuple]:
    results = []
    client.preflight()
    print(f"Probing PLAYTEACH indices 0..{max_index}", flush=True)
    for idx in range(max_index + 1):
        print(f"[{client.elapsed()}] --- PLAYTEACH index={idx} ---", flush=True)
        trace = client.exec_action("PLAYTEACH", index=idx)
        if "no-start" in trace:
            print(f"[{client.elapsed()}] index={idx}: no recorded action ({trace})", flush=True)
        else:
            print(f"[{client.elapsed()}] index={idx}: completed ({trace})", flush=True)
        results.append((idx, trace))
        if wait_enter and idx < max_index:
            input(f"Press Enter to probe next index ({idx + 1}/{max_index})...")
    return results


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Probe Boomy PLAYTEACH indices")
    p.add_argument("--server", default="http://192.168.55.101:8080")
    p.add_argument("--max-index", type=int, default=5)
    p.add_argument("--no-pause", action="store_true", help="Do not wait for Enter between indices")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    client = ChoreoClient(args.server)
    try:
        probe_indices(client, max_index=args.max_index, wait_enter=not args.no_pause)
        print("Probe finished.", flush=True)
        return 0
    except KeyboardInterrupt:
        client.post_stop()
        print("已停止", flush=True)
        return 130
    except ChoreoAbort as exc:
        print(f"ABORT: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
