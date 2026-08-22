"""Run deterministic complete-hand stress simulations outside normal pytest collection."""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from collections.abc import Sequence

from backend.tests.support.holdem_simulation import (
    ResolvedAction,
    deterministic_scenario,
    resolved_action_as_dict,
    run_simulation,
    scenario_as_dict,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run reproducible Phase 7 Hold'em domain simulations.",
    )
    parser.add_argument(
        "--hands",
        type=_positive_int,
        default=1_000,
        help="number of independently derived hands to run (default: 1000)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=20_260_822,
        help="deterministic master seed (default: 20260822)",
    )
    parser.add_argument(
        "--case-index",
        type=_nonnegative_int,
        help="run exactly this derived case for direct replay",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    indexes = (args.case_index,) if args.case_index is not None else range(args.hands)
    requested = 1 if args.case_index is not None else args.hands
    started = time.perf_counter()

    for completed, case_index in enumerate(indexes, start=1):
        case_seed, action_seed, scenario = deterministic_scenario(args.seed, case_index)
        trace: list[ResolvedAction] = []
        try:
            run_simulation(scenario, trace_sink=trace)
        except Exception as error:
            print("PHASE 7 STRESS FAILURE", file=sys.stderr)
            print(f"master_seed={args.seed}", file=sys.stderr)
            print(f"case_index={case_index}", file=sys.stderr)
            print(f"case_seed={case_seed}", file=sys.stderr)
            print(f"action_seed={action_seed}", file=sys.stderr)
            print(f"deck_seed={scenario.deck_seed}", file=sys.stderr)
            print(
                "scenario=" + json.dumps(scenario_as_dict(scenario), sort_keys=True),
                file=sys.stderr,
            )
            print(
                "resolved_actions="
                + json.dumps(
                    [resolved_action_as_dict(action) for action in trace],
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            print(f"error={type(error).__name__}: {error}", file=sys.stderr)
            print("traceback:", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print(
                "replay=.\\backend\\.venv\\Scripts\\python.exe -m "
                "backend.tests.stress.holdem_stress "
                f"--seed {args.seed} --case-index {case_index}",
                file=sys.stderr,
            )
            return 1

        if requested >= 1_000:
            interval = max(1_000, requested // 10)
            if completed % interval == 0 and completed < requested:
                elapsed = time.perf_counter() - started
                print(f"completed={completed}/{requested} elapsed_seconds={elapsed:.3f}")

    elapsed = time.perf_counter() - started
    rate = requested / elapsed if elapsed else float("inf")
    print(
        f"completed={requested} master_seed={args.seed} "
        f"elapsed_seconds={elapsed:.3f} hands_per_second={rate:.1f}"
    )
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a nonnegative integer")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
