"""Give a T0 arm only the feedback its condition allows.

Claim 1 (`docs/ablation-design.md` §2) compares an agent that can read a run's per-primitive
trace against one that cannot. That comparison is only meaningful if the second arm is handed a
directory that *cannot* contain the trace, the keyframes or the free diagnostics — pointing it at
the real run directory and asking it not to look is the mistake the 2026-10 ablation design warned
about (a leak that depends on the agent's self-restraint is not a control).

So the coordinator runs the episode, then copies exactly two files per seed into a separate tree:

    <out>/<seed>/outcome.json          {"program_ok", "task_success", "termination_reason"}
    <out>/<seed>/program-result.json   the program's own report + stdout, verbatim

Everything else stays behind: `trace/`, `media/`, `steps_executed`, `timing`, `cumulative_reward`.
A1-1 keeps the program's own output on purpose — the program is written by the agent, so its
report is not a harness trace channel; hiding it would change the manipulation from "measurements
are not free" to "you may not see your own prints", which is a different experiment (L-1).

    python sanitize_run.py --runs outputs/ablation/claim1/T0/runroot/libero-pro/.../debug/v01 \
                           --out    outputs/ablation/claim1/T0/feedback/v01 --label "v01 dev 15"

Prints the destination and a per-seed pass/fail line; the coordinator relays only that.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys

KEEP_OUTCOME = ("program_ok", "task_success", "termination_reason")


def find_runs(root: Path) -> list[Path]:
    """Every run directory under `root`, in seed order. Accepts one run dir or a batch tree."""
    if (root / "outcome.json").is_file():
        return [root]
    runs = sorted({path.parent for path in root.glob("**/outcome.json")})
    return runs


def seed_of(run_dir: Path) -> str:
    """`.../0051/run-...` -> `0051`; a bare run dir keeps its own name."""
    for parent in (run_dir.parent, run_dir):
        name = parent.name
        if name.isdigit():
            return name.zfill(4)
    return run_dir.name


def sanitize(run_dir: Path, out_dir: Path) -> dict:
    outcome_path = run_dir / "outcome.json"
    program_path = run_dir / "source" / "program-result.json"
    if not outcome_path.is_file():
        raise SystemExit(f"{run_dir} has no outcome.json — is this a run directory?")

    raw = json.loads(outcome_path.read_text())
    kept = {key: raw.get(key) for key in KEEP_OUTCOME}
    if not program_path.is_file():
        kept["program_result_missing"] = True

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "outcome.json").write_text(json.dumps(kept, indent=1, sort_keys=True) + "\n")
    if program_path.is_file():
        shutil.copyfile(program_path, out_dir / "program-result.json")
    return kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, required=True, help="a run directory or a batch tree")
    parser.add_argument("--out", type=Path, required=True, help="where the T0 arm will read")
    parser.add_argument("--label", default="", help="free text for the one-line summary")
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing --out tree (refused by default: a stale mix of two versions "
        "is the one way this directory can lie to the agent)",
    )
    args = parser.parse_args()

    runs = find_runs(args.runs)
    if not runs:
        raise SystemExit(f"no outcome.json under {args.runs}")

    if args.out.exists():
        if not args.force:
            raise SystemExit(
                f"{args.out} already exists — pass --force to replace it, or pick a new --out. "
                "Retaining the previous version's feedback is how a T0 agent ends up reading two "
                "versions at once."
            )
        shutil.rmtree(args.out)

    passes = 0
    lines = []
    for run_dir in runs:
        seed = seed_of(run_dir)
        kept = sanitize(run_dir, args.out / seed)
        ok = bool(kept.get("task_success"))
        passes += ok
        lines.append(
            f"seed {seed}: program_ok={kept.get('program_ok')} "
            f"task_success={kept.get('task_success')} "
            f"termination_reason={kept.get('termination_reason')}"
        )

    # The one invariant that makes this directory trustworthy.
    allowed = {"outcome.json", "program-result.json"}
    stray = [
        str(path.relative_to(args.out))
        for path in args.out.rglob("*")
        if path.is_file() and path.name not in allowed
    ]
    if stray:  # pragma: no cover - only reachable if someone edits this file badly
        raise SystemExit(f"T0 package contains files it must not: {stray}")

    print(f"label: {args.label or '-'}")
    print(f"out:   {args.out}")
    print(f"runs:  {len(runs)}  passes: {passes}/{len(runs)}")
    for line in lines:
        print(" ", line)
    print(f"files written: {2 * len(runs)} (outcome.json + program-result.json per seed)")
    return 0 if not stray else 1


if __name__ == "__main__":
    sys.exit(main())
