#!/usr/bin/env python3
"""Export agent rollouts into a behavior-cloning-ready index.

A finished CaP episode already contains everything BC needs:

  ``episode/steps.jsonl``   one record per control tick: the joint target and
                            gripper command that were *actually commanded*, plus
                            the resulting ``robot_state``
  ``media/videos/<cam>.mp4`` 20 fps, ``ticks + 1`` frames per camera, so frame
                            ``i`` is the observation *before* step ``i``
  ``run.json`` / ``outcome.json``  task identity, seed, and success

The pixels are already on disk, so this exporter deliberately does **not** copy
them. It writes one small ``.npz`` per episode (actions + proprioception), an
``index.jsonl`` describing every episode, and — importantly — *verifies* the
frame/tick alignment it relies on. A training loader then decodes frames lazily
from the mp4s, which keeps a 50-episode dataset at a few hundred MB instead of
~60 GB of decoded RGB.

Example::

    python scripts/export_agent_rollouts.py \
        --results outputs/runs/<run>/libero-pro/libero_goal_swap/task_9/validation/runs/<id>/results \
        --out     outputs/runs/<run>/distill/task_9_heldout
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

CAMERAS = ("agentview", "robot0_eye_in_hand")


def unwrap(value):
    """Turn the harness's serialized arrays into plain numpy arrays."""
    if isinstance(value, dict):
        if "$array_inline" in value:
            return np.asarray(value["$array_inline"], dtype=np.float64)
        if set(value) == {"primary"}:  # single-arm embodiment
            return unwrap(value["primary"])
        return {k: unwrap(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap(v) for v in value]
    return value


def video_frames(path: Path) -> int:
    import cv2  # local import: only needed for the alignment check

    cap = cv2.VideoCapture(str(path))
    try:
        return int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()


def load_episode(run_dir: Path) -> dict:
    run = json.loads((run_dir / "run.json").read_text())
    outcome = json.loads((run_dir / "outcome.json").read_text())

    actions, qpos, qvel, eef_pos, eef_quat, grip = [], [], [], [], [], []
    joint_names = None
    with (run_dir / "episode" / "steps.jsonl").open() as handle:
        for line in handle:
            if not line.strip():
                continue
            step = json.loads(line)
            arm = unwrap(step["action"])["arms"]
            actions.append(
                np.concatenate([np.asarray(arm["target"], dtype=np.float64),
                                [float(arm["gripper_position"])]])
            )
            state = unwrap(step["robot_state"])
            joint_names = state.get("joint_names") or joint_names
            qpos.append(np.asarray(state["joint_positions"], dtype=np.float64))
            qvel.append(np.asarray(state["joint_velocities"], dtype=np.float64))
            pose = state["end_effector_poses"]
            eef_pos.append(np.asarray(pose["position"], dtype=np.float64))
            eef_quat.append(np.asarray(pose["quaternion_wxyz"], dtype=np.float64))
            grip.append(float(state["gripper_positions"]))

    return {
        "run": run,
        "outcome": outcome,
        "joint_names": joint_names,
        "actions": np.asarray(actions),
        "qpos": np.asarray(qpos),
        "qvel": np.asarray(qvel),
        "eef_pos": np.asarray(eef_pos),
        "eef_quat": np.asarray(eef_quat),
        "gripper": np.asarray(grip),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", action="append", required=True,
                        help="a results/ directory (repeatable); each child is <NNNN>/<run-id>/")
    parser.add_argument("--out", required=True, help="output directory for the exported index")
    parser.add_argument("--skip-video-check", action="store_true",
                        help="skip the frame-count alignment check (faster, less safe)")
    parser.add_argument("--limit", type=int, default=0, help="export at most N episodes")
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    (out_dir / "episodes").mkdir(parents=True, exist_ok=True)

    index, frame_deltas, skipped = [], {}, []
    for results_dir in map(Path, args.results):
        for episode_dir in sorted(p for p in results_dir.iterdir() if p.is_dir()):
            run_dir = next(iter(sorted(episode_dir.iterdir())), None)
            if run_dir is None or not (run_dir / "run.json").is_file():
                skipped.append(str(episode_dir))
                continue
            data = load_episode(run_dir)
            ticks = len(data["actions"])

            cameras = {}
            for cam in CAMERAS:
                video = run_dir / "media" / "videos" / f"{cam}.mp4"
                if not video.is_file():
                    continue
                frames = None if args.skip_video_check else video_frames(video)
                if frames is not None:
                    frame_deltas.setdefault(frames - ticks, 0)
                    frame_deltas[frames - ticks] += 1
                cameras[cam] = {"video": str(video), "frames": frames}

            stem = f"{data['run']['suite']}-task{data['run']['task_id']}-seed{data['run']['seed']:03d}"
            npz_path = out_dir / "episodes" / f"{stem}.npz"
            np.savez_compressed(
                npz_path,
                actions=data["actions"], qpos=data["qpos"], qvel=data["qvel"],
                eef_pos=data["eef_pos"], eef_quat=data["eef_quat"], gripper=data["gripper"],
            )
            index.append({
                "episode": stem,
                "run_dir": str(run_dir),
                "benchmark": data["run"]["benchmark"],
                "suite": data["run"]["suite"],
                "task_id": data["run"]["task_id"],
                "task_name": data["run"].get("task_name"),
                "seed": data["run"]["seed"],
                "success": bool(data["outcome"].get("task_success")),
                "ticks": ticks,
                "joint_names": data["joint_names"],
                "cameras": cameras,
                "arrays": str(npz_path),
            })
            if args.limit and len(index) >= args.limit:
                break

    with (out_dir / "index.jsonl").open("w") as handle:
        for row in index:
            handle.write(json.dumps(row) + "\n")

    successes = [r for r in index if r["success"]]
    total_ticks = sum(r["ticks"] for r in index)
    print(f"episodes        {len(index)}  (success {len(successes)})")
    print(f"ticks           {total_ticks}  ({total_ticks} frames per camera)")
    print(f"frame-ticks     {frame_deltas or 'not checked'}")
    print(f"index           {out_dir / 'index.jsonl'}")
    if skipped:
        print(f"skipped         {len(skipped)} dirs without run.json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
