# SPDX-License-Identifier: Apache-2.0
"""CPU-only, bounded idle-GPU gate for an explicitly requested local command.

Run this launcher through the site's process manager. This is an observation
gate, not an exclusive cluster reservation; other schedulers must coordinate.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path


def parse_gpus(text: str) -> list[dict]:
    rows = []
    for row in csv.reader(text.splitlines(), skipinitialspace=True):
        if not row:
            continue
        index, uuid, free, used, utilization = row
        rows.append(
            dict(index=int(index), uuid=uuid.strip(), free=int(free), used=int(used), utilization=int(utilization)))
    return rows


def idle_indices(rows: list[dict], occupied: set[str], min_free_mib: int) -> list[int]:
    return [
        r['index'] for r in rows
        if r['uuid'] not in occupied and r['free'] >= min_free_mib and r['used'] < 1024 and r['utilization'] <= 5
    ]


def probe_idle(min_free_mib: int) -> list[int]:

    def query(fields: str) -> str:
        return subprocess.check_output(['nvidia-smi', fields, '--format=csv,noheader,nounits'], text=True, timeout=15)

    rows = parse_gpus(query('--query-gpu=index,uuid,memory.free,memory.used,utilization.gpu'))
    occupied = {row.strip() for row in query('--query-compute-apps=gpu_uuid').splitlines() if row.strip()}
    return idle_indices(rows, occupied, min_free_mib)


def wait_for_idle(probe: Callable[[], list[int]],
                  *,
                  count: int,
                  stable_checks: int,
                  interval: float,
                  timeout: float,
                  sleep: Callable[[float], None] = time.sleep) -> list[int]:
    deadline = time.monotonic() + timeout
    previous: list[int] = []
    stable = 0
    while time.monotonic() < deadline:
        candidates = probe()[:count]
        if len(candidates) == count:
            stable = stable + 1 if candidates == previous else 1
            if stable >= stable_checks:
                return candidates
        else:
            stable = 0
        previous = candidates
        sleep(min(interval, max(0, deadline - time.monotonic())))
    raise TimeoutError('No suitable idle GPU allocation became available before the deadline')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--min-free-mib', type=int, default=45000)
    parser.add_argument('--stable-checks', type=int, default=3)
    parser.add_argument('--interval', type=float, default=30)
    parser.add_argument('--timeout', type=float, default=43200)
    parser.add_argument('--record', type=Path, required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command or args.count < 1 or args.stable_checks < 1 or args.interval <= 0 or args.timeout <= 0:
        parser.error('A command and positive count/checks/interval/timeout are required')
    args.record.parent.mkdir(parents=True, exist_ok=True)

    def record(state: str, **values: object) -> None:
        temporary = args.record.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(state=state, updated_at=time.time(), **values), indent=2))
        temporary.replace(args.record)

    def probe() -> list[int]:
        candidates = probe_idle(args.min_free_mib)
        record('waiting', idle_candidates=candidates, requested_count=args.count)
        return candidates

    try:
        chosen = wait_for_idle(probe,
                               count=args.count,
                               stable_checks=args.stable_checks,
                               interval=args.interval,
                               timeout=args.timeout)
        if not set(chosen).issubset(probe()):
            raise RuntimeError('GPU availability changed at launch; command was not started')
        record('running', gpu_indices=chosen)
        print(f'Launching on verified idle GPUs {chosen}', flush=True)
        environment = dict(os.environ, CUDA_VISIBLE_DEVICES=','.join(map(str, chosen)))
        result = subprocess.run(command, env=environment, check=False)
        record('completed' if result.returncode == 0 else 'failed', gpu_indices=chosen, exit_code=result.returncode)
        raise SystemExit(result.returncode)
    except (TimeoutError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
        record('not_started', reason=str(exc))
        raise SystemExit(str(exc)) from exc


if __name__ == '__main__':
    main()
