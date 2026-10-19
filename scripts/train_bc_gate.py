#!/usr/bin/env python3
"""Gate experiment for claim 3: can a policy be fit from agent rollouts at all?

This is the *necessary condition*, not the claim. It fits a behavior-cloning
model on the joint targets recorded in agent rollouts and measures teacher-forced
action error on **held-out seeds** — seeds whose episodes never appear in
training. It does **not** close the loop in the simulator, so it cannot report a
task success rate. Read a good number here as "the data is learnable", never as
"the agent's data improves the task".

Three arms, same split, same optimiser:

  ``image+state``  agentview frame at tick t + proprioception -> joint target
  ``state``        proprioception only                         -> joint target
  ``mean``         predict the training mean action            -> joint target

The ``state`` and ``mean`` arms exist to keep the image arm honest: if the image
arm cannot beat them, the pixels are not carrying usable signal at this scale.

Frames are decoded from the mp4s written by the harness (20 fps, ``ticks + 1``
frames, frame *i* = observation before step *i*), downscaled to grayscale
96x96 in RAM. Nothing is written except the run's ``summary.json``.

Example::

    python scripts/train_bc_gate.py \
        --index outputs/runs/<run>/distill/task_9_heldout/index.jsonl \
        --out   outputs/runs/<run>/distill/task_9_heldout/gate \
        --holdout-seeds 1-10 --epochs 8 --device cpu
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import numpy as np
import torch
from torch import nn


def parse_seeds(spec: str) -> set[int]:
    seeds: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            seeds.update(range(int(lo), int(hi) + 1))
        else:
            seeds.add(int(chunk))
    return seeds


def read_frames(video: Path, count: int, size: int) -> np.ndarray:
    """Sequentially decode the first ``count`` frames as grayscale ``size``x``size``."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    frames = np.zeros((count, size, size), dtype=np.uint8)
    idx = 0
    try:
        while idx < count:
            ok, frame = cap.read()
            if not ok:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frames[idx] = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
            idx += 1
    finally:
        cap.release()
    if idx < count:
        raise RuntimeError(f"{video}: decoded {idx} frames, expected {count}")
    return frames


def load_rows(index_path: Path) -> list[dict]:
    rows = [json.loads(line) for line in index_path.read_text().splitlines() if line.strip()]
    return [r for r in rows if r["success"]]


def build_split(rows, holdout_seeds: set[int]):
    train = [r for r in rows if r["seed"] not in holdout_seeds]
    holdout = [r for r in rows if r["seed"] in holdout_seeds]
    if not train or not holdout:
        raise SystemExit(f"split is empty: train={len(train)} holdout={len(holdout)} "
                         f"(holdout seeds {sorted(holdout_seeds)})")
    return train, holdout


def load_episodes(rows, size: int, camera: str) -> list[dict]:
    out = []
    for row in rows:
        cam = row["cameras"].get(camera)
        if cam is None:
            continue
        arrays = np.load(row["arrays"])
        ticks = row["ticks"]
        frames = read_frames(Path(cam["video"]), ticks, size)
        out.append({
            "frames": frames,                       # (T, S, S) uint8
            "actions": arrays["actions"][:, :7].astype(np.float32),
            "proprio": np.concatenate([
                arrays["qpos"], arrays["eef_pos"], arrays["gripper"][:, None],
            ], axis=1).astype(np.float32),
            "seed": row["seed"],
        })
    return out


class BCNet(nn.Module):
    def __init__(self, proprio_dim: int, action_dim: int, use_image: bool):
        super().__init__()
        self.use_image = use_image
        self.proprio = nn.Sequential(nn.Linear(proprio_dim, 64), nn.ReLU(),
                                     nn.Linear(64, 64), nn.ReLU())
        if use_image:
            self.vision = nn.Sequential(
                nn.Conv2d(1, 16, 5, stride=2), nn.ReLU(),
                nn.Conv2d(16, 32, 3, stride=2), nn.ReLU(),
                nn.Conv2d(32, 64, 3, stride=2), nn.ReLU(),
                nn.AdaptiveAvgPool2d(1), nn.Flatten(),
                nn.Linear(64, 64), nn.ReLU(),
            )
            head_in = 128
        else:
            head_in = 64
        self.head = nn.Sequential(nn.Linear(head_in, 128), nn.ReLU(), nn.Linear(128, action_dim))

    def forward(self, image, proprio):
        z = self.proprio(proprio)
        if self.use_image:
            z = torch.cat([z, self.vision(image)], dim=1)
        return self.head(z)


def evaluate(model, episodes, action_mean, batch: int = 256) -> dict:
    model.eval()
    abs_err = []
    with torch.no_grad():
        for ep in episodes:
            n = len(ep["actions"])
            for start in range(0, n, batch):
                sl = slice(start, start + batch)
                frames = torch.from_numpy(ep["frames"][sl]).float().unsqueeze(1) / 255.0
                proprio = torch.from_numpy(ep["proprio"][sl])
                pred = model(frames, proprio)
                abs_err.append((pred - torch.from_numpy(ep["actions"][sl])).abs().numpy())
    err = np.concatenate(abs_err, axis=0)
    result = {"mae_overall_rad": float(err.mean()), "mae_per_joint_rad": err.mean(axis=0).tolist()}
    if action_mean is not None:
        base = []
        for ep in episodes:
            base.append(np.abs(action_mean - ep["actions"]).mean())
        result["mae_baseline_mean_action_rad"] = float(np.mean(base))
    return result


def evaluate_lag(episodes, action_mean) -> dict:
    """Copy the previous action — the strongest trivial baseline for smooth joint targets."""
    err = []
    for ep in episodes:
        actions = ep["actions"]
        pred = np.vstack([action_mean[None, :], actions[:-1]])
        err.append(np.abs(pred - actions))
    err = np.concatenate(err, axis=0)
    return {"mae_overall_rad": float(err.mean()), "mae_per_joint_rad": err.mean(axis=0).tolist()}


def train_arm(kind: str, train_eps, holdout_eps, epochs: int, seed: int, lr: float) -> dict:
    torch.manual_seed(seed)
    random.seed(seed)
    proprio_dim = train_eps[0]["proprio"].shape[1]
    action_dim = 7
    action_mean = np.concatenate([ep["actions"] for ep in train_eps]).mean(axis=0)

    if kind == "mean":
        return {"arm": kind, **evaluate(_ConstantMean(action_mean), holdout_eps, None)}
    if kind == "lag":
        return {"arm": kind, **evaluate_lag(holdout_eps, action_mean)}

    model = BCNet(proprio_dim, action_dim, use_image=(kind == "image+state"))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    steps_per_epoch = 64
    for epoch in range(epochs):
        model.train()
        for _ in range(steps_per_epoch):
            ep = random.choice(train_eps)
            n = len(ep["actions"])
            idx = np.random.randint(0, n, size=64)
            frames = torch.from_numpy(ep["frames"][idx]).float().unsqueeze(1) / 255.0
            proprio = torch.from_numpy(ep["proprio"][idx])
            target = torch.from_numpy(ep["actions"][idx])
            loss = loss_fn(model(frames, proprio), target)
            opt.zero_grad()
            loss.backward()
            opt.step()
    return {"arm": kind, "loss_last": float(loss.item()), **evaluate(model, holdout_eps, action_mean)}


class _ConstantMean(nn.Module):
    def __init__(self, mean: np.ndarray):
        super().__init__()
        self.register_buffer("mean", torch.from_numpy(mean.astype(np.float32)))

    def forward(self, image, proprio):
        return self.mean.expand(proprio.shape[0], -1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--holdout-seeds", default="1-10")
    parser.add_argument("--camera", default="agentview")
    parser.add_argument("--size", type=int, default=96)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--arms", default="mean,state,image+state")
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    args = parser.parse_args(argv)

    torch.set_num_threads(max(1, torch.get_num_threads()))
    rows = load_rows(Path(args.index))
    train_rows, holdout_rows = build_split(rows, parse_seeds(args.holdout_seeds))
    print(f"train episodes {len(train_rows)}  held-out episodes {len(holdout_rows)}")
    train_eps = load_episodes(train_rows, args.size, args.camera)
    holdout_eps = load_episodes(holdout_rows, args.size, args.camera)
    print(f"frames cached: train {sum(len(e['actions']) for e in train_eps)}, "
          f"held-out {sum(len(e['actions']) for e in holdout_eps)}")

    results = []
    for kind in [a.strip() for a in args.arms.split(",") if a.strip()]:
        results.append(train_arm(kind, train_eps, holdout_eps, args.epochs, args.seed, args.lr))
        print(f"  {kind:12s} mae={results[-1]['mae_overall_rad']:.4f} rad")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    summary = {
        "index": args.index, "camera": args.camera, "size": args.size,
        "holdout_seeds": sorted(parse_seeds(args.holdout_seeds)),
        "train_episodes": len(train_rows), "holdout_episodes": len(holdout_rows),
        "train_ticks": int(sum(r["ticks"] for r in train_rows)),
        "holdout_ticks": int(sum(r["ticks"] for r in holdout_rows)),
        "epochs": args.epochs, "device": args.device, "results": results,
        "scope": "teacher-forced action error on held-out seeds; NOT a task-success measurement",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(f"summary {out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
