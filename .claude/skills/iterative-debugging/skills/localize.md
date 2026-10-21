---
name: localize
description: Object localization via SAM3 — prompting strategies, multi-prompt fallback pattern, candidate disambiguation, and per-object prompt registry. Grows through experiment.
---

# Localize — SAM3 Prompting & Object Localization

> This skill tracks **what SAM3 prompts work for which objects** and the standard
> localization helper pattern. Prompt strings go in the registry table; any selection or
> filtering logic that needs code becomes its own subsection (trigger → code → evidence),
> modeled on *Disambiguation* below.
> Programs cannot import modules — all helpers below are pure Python over public contracts.

---

## Standard Localization Helper

`localize_object()` already composes text segmentation, RGB-D projection, and geometry
estimation. Wrap it with a prompt-fallback loop and a minimum-point guard:

```python
def locate(queries, camera_name="agentview"):
    """Try prompts in order, return the first LocalizationResult with >= 20 points."""
    base_frame = get_robot_state().base_frame
    for query in queries:
        found = localize_object(query, camera_name=camera_name, target_frame=base_frame)
        if found.ok and len(found.point_cloud.points) >= 20:
            return found
    return None

bottle = locate(("<specific prompt>", "<fallback prompt>"))
if bottle is None:
    raise ValueError("object not found")
center = bottle.geometry.pose.position   # framed center from estimate_geometry
```

**Do NOT aim with `geometry.pose.position` — use per-axis cloud quantiles.** `estimate_geometry`
returns the **world-AABB midpoint**, and a handful of outlier pixels on the surface behind the object
stretch that box. Measured on `libero_goal_swap/task_8` seed 51 by an independent verifier probe:
`geometry.pose.x` minus the cloud's x-median was **+20.8 mm on a 10.2 cm bowl** and **+5.95 mm on a
12.1 cm plate**; the pose reproduces the AABB midpoint to 0.25 mm, and its extents exceed the
quantile spans by 7.3–13.0 mm. On `task_9` the same bias reached **10.63 cm**. Aim with
`quantile(points, 0.50)` per axis and keep `geometry.extents` / `point_count` for filtering only —
see § *AABB Pose Is Not a Centre — Outlier-Sensitive `geometry`* below for the full mechanism.

```python
xs = [float(p[0]) for p in bottle.point_cloud.points]
ys = [float(p[1]) for p in bottle.point_cloud.points]
zs = [float(p[2]) for p in bottle.point_cloud.points]
aim = (sorted(xs)[len(xs) // 2], sorted(ys)[len(ys) // 2], sorted(zs)[len(zs) // 2])
```

**Why the helper still uses geometry at all:** `estimate_geometry` remains the cheap way to get
**extents and an orientation** for elongated or partially occluded objects; it is the *aim point*
that must not come from it.

---

## Prompt Registry

Discovered working prompts, indexed by object. Add entries as you find them.
List prompts in priority order — first hit wins. This table is for prompt strings and scores
only — if a finding needs procedure or code (candidate filtering, camera choice, occlusion
handling), write it as a subsection instead and keep at most a pointer row here.

| Object | Working Prompts | Benchmark/Suite/Task | Notes |
|---|---|---|---|
| drawer handle (stacked set) | `"drawer handle"` | libero-pro/libero_goal_swap/task_0 | Returns the whole stack (3 strong) + stragglers ≤0.2; select with Relative Score Thresholds + middle-of-stack. Mask bleeds 2–4 cm behind the bar — use Foremost-Depth Shell. |
| middle drawer handle | ~~`"middle drawer handle"`~~ | libero-pro/libero_goal_swap/task_0 | Dead end: all candidates ≤0.15. Spatial selection from `"drawer handle"` instead. |
| cabinet (drawer chest body) | `"cabinet"` | libero-pro/libero_goal_swap/task_0 | Weak but usable (~0.34); body center → outward/pull direction for drawer tasks. |
| wine bottle (task_9) | `"wine bottle"` (top-1 0.926-0.953, 1589-1593 pts) | libero-pro/libero_goal_swap/task_9 | `"dark green wine bottle"` (0.945) also works. Deterministic across seeds 51-65. |
| wine rack (task_9 receiver) | `"wooden wine rack"` (top-1 0.750, 30 634 pts) | libero-pro/libero_goal_swap/task_9 | Confusable with task_2's cabinet: `"cabinet"` on this scene returns the drawer chest (flat top z 0.2158) *and* the sloped rack. Pick by the 30k-point cloud and the inclined top, not by score. |

---

## Disambiguation: Two Similar Objects in Scene

When a scene contains two visually similar objects, the top-scored candidate is often the wrong
one. Inspect all candidates from `segment_text()` and discriminate by bbox pixel area, image
position, or projected 3D geometry:

```python
def smallest_box_candidate(camera_name, prompts, max_center_y=None):
    """Select the candidate with smallest bbox area (most compact matching shape).
    max_center_y: if set, only accept boxes whose center row is above it (upper image = farther).
    """
    for prompt in prompts:
        found = segment_text(camera_name, prompt)
        if not found.ok:
            continue
        best = None
        best_area = None
        for candidate in found.segmentations[:10]:
            if candidate.box_xyxy is None:
                continue
            x1, y1, x2, y2 = (float(value) for value in candidate.box_xyxy)
            area = (x2 - x1) * (y2 - y1)
            center_y = (y1 + y2) / 2.0
            if max_center_y is not None and center_y >= max_center_y:
                continue
            if best_area is None or area < best_area:
                best_area = area
                best = candidate
        if best is not None:
            return best
    return None
```

Project the selected candidate with `mask_to_point_cloud(candidate, camera_name)` and compare
observable geometry with `estimate_geometry()` — filter by 3D Z-height first when a taller or
larger object outranks the target, then pick the highest score among geometry-matching
candidates.

Never disambiguate with native simulator objects, hidden IDs, known seed coordinates, or fixed
pixel locations. Re-localize after contact or a failed grasp — the object may have moved; do not
reuse a stale center.

**Identity selectors disagree on this scene (measured column, 5/5 seeds).** The selector order above
(relation → sign of the support gap → candidate height → proximity) was scored head to head on
`libero_goal_swap/task_2`, where the prompt `"cabinet"` returns a drawer chest, a sloped wooden rack
and the table: **relation** and **flat fraction** both picked the drawer top, while **height**
(z_ref 0.293–0.296) and **proximity** both picked the **sloped rack**. Two of the four are wrong here.
"The taller one" and "the nearer one" are not axioms — compute the column and report it
(see the next section for the flatness test that separated them).

---

## Receiving Surface: Select by Flat Fraction, Not by Score

**Trigger.** The prompt names a **receiver** (a chest, a tabletop, a shelf) rather than a manipulated
object, and it returns several candidates of which only one is a horizontal top — a sloped rack, a
table edge, the robot arm.

```python
def quantile(values, fraction):
    ordered = sorted(float(value) for value in values)
    return ordered[int((len(ordered) - 1) * fraction)]

best = None
for cand in segment_text("agentview", "cabinet").segmentations[:3]:
    c = float(cand.score)
    if c < 0.15:
        continue
    cloud = mask_to_point_cloud(cand.mask, "agentview", target_frame=base_frame)
    pts = cloud.points
    if len(pts) < 400:
        continue
    z_ref = quantile((float(p[2]) for p in pts), 0.95)
    flat = [p for p in pts if z_ref - 0.013 <= float(p[2]) <= z_ref + 0.013]
    frac = len(flat) / float(len(pts))
    if frac < 0.30 or z_ref < bottle_base_z + 0.03:
        continue
    if best is None or frac > best[0]:
        best = (frac, flat, z_ref)

frac, flat, top_z = best
top_z = quantile((float(p[2]) for p in flat), 0.50)
place_x = 0.5 * (quantile((float(p[0]) for p in flat), 0.02) + quantile((float(p[0]) for p in flat), 0.98))
place_y = 0.5 * (quantile((float(p[1]) for p in flat), 0.02) + quantile((float(p[1]) for p in flat), 0.98))
```

**Evidence.** `libero_goal_swap/task_2`, "cabinet": the drawer chest is **top-1** with flat fraction
**0.729–0.796** (verifier, 5 seeds; 0.699–0.831 over the proposer's 30) against the sloped rack's
**0.112–0.143**, and `top_z` was **0.2158 on every seed**. Use `q50` of the flat band, not `q95` of
the whole cloud, for the support height.

**What the flat test is actually for.** Not for beating the field on score — the drawer was already
top-1 on all 35 measured seeds, so the rule **never had to fire**. Its job is rejecting the rack, and
the 0.15 score floor does most of the filtering. The third candidate here is the **table/floor**
(score 0.106, flat 0.529, z_ref 0.0049), not the robot arm as the proposing task believed; it is
dropped by the score floor before the flat test runs. Keep this rule labelled a **tie-breaker**
(the drawer/rack score margin did fall to 0.090–0.169 on the worst seed), not a substitute for a
good prompt.

---

## Relative Score Thresholds (never gate on a fixed cutoff)

**Trigger.** The same objects are re-detected every seed but their absolute SAM3 scores vary
2–3× across seeds (measured 0.24–0.61 for one scene's three drawer handles), while false
positives stay low (≤ ~0.2). No fixed cutoff separates them: 0.5 rejects real parts on
low-score seeds, anything lower admits stragglers on high-score seeds.

**Code.** Scale the cutoff to the scene's own top score, then cluster spatially. (`quantile`
is the cross-file helper from `grasp.md` § Standard Pick-and-Place Template — use that
definition; `math` is importable.)

```python
seg = segment_text("agentview", "<part prompt>")
top_score = max(float(c.score) for c in seg.segmentations[:5])
min_score = max(0.15, 0.4 * top_score)          # relative, never absolute

accepted = []
for cand in seg.segmentations[:5]:
    if float(cand.score) < min_score:
        continue
    cloud = mask_to_point_cloud(cand.mask, "agentview", target_frame=base_frame)
    if len(cloud.points) < 20:
        continue
    accepted.append((cand, float(cand.score),
                     quantile((p[0] for p in cloud.points), 0.5),
                     quantile((p[1] for p in cloud.points), 0.5),
                     quantile((p[2] for p in cloud.points), 0.5)))

# For a stacked set (drawer handles, shelves): keep candidates within 0.12 m XY
# of the top-scored one, sort by Z, take the MIDDLE of the stack.
accepted.sort(key=lambda e: -e[1])
ref = accepted[0]
stack = [e for e in accepted
         if math.sqrt((e[2] - ref[2]) ** 2 + (e[3] - ref[3]) ** 2) <= 0.12]
stack.sort(key=lambda e: e[4])
middle = stack[len(stack) // 2]          # middle of an odd stack; seed it with None
```

**Why it works + evidence.** The scene's own score distribution is the only reliable scale.
On libero_goal_swap/task_0 a fixed 0.5 cutoff lost 5/15 dev seeds while false positives never
exceeded 0.19; the relative rule accepted exactly the 3 stacked handles on all 15. A verifier
reproduced it from this text alone: middle-of-stack selection was correct 5/5, including a
seed whose top score was 0.312 (every fixed cutoff fails there). If the stack can be empty or
a single candidate, decide explicitly what that means — one candidate may be the WRONG stack
member (a lone detection of the top drawer reads as "the middle" if you do not check).

---

## Foremost-Depth Shell (mask bleed on thin protruding parts)

**Trigger.** A SAM3 mask on a small part that protrudes toward the camera (handle bar, knob,
bottle neck, lip) bleeds onto the surface behind it, so `mask_to_point_cloud` /
`localize_object` medians sit 2–4 cm behind the true part. Symptom: grasp descents
consistently land beside/behind the part and contact-stall; overlays show the mask extending
past the part edges.

**Code.** Deproject the mask yourself with the public camera model and keep only the foremost
~2 cm shell — the protruding part is the closest surface inside its own mask:

```python
def quat_rot(q):
    w, x, y, z = q
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )

def deproject(depth_m, intrinsics, camera_pose, pixels):
    # (depth, wx, wy, wz) per pixel, in the camera_pose frame (== robot base on LIBERO)
    cam_pos = camera_pose.position
    rot = quat_rot(tuple(float(v) for v in camera_pose.quaternion_wxyz))
    fx = float(intrinsics[0][0]); fy = float(intrinsics[1][1])
    cx = float(intrinsics[0][2]); cy = float(intrinsics[1][2])
    points = []
    for pixel in pixels:
        v = int(pixel[0]); u = int(pixel[1])
        d = float(depth_m[v, u])
        if d <= 0.05:
            continue
        x_c = (u - cx) / fx * d
        y_c = (v - cy) / fy * d
        points.append((d,
            float(cam_pos[0]) + rot[0][0] * x_c + rot[0][1] * y_c + rot[0][2] * d,
            float(cam_pos[1]) + rot[1][0] * x_c + rot[1][1] * y_c + rot[1][2] * d,
            float(cam_pos[2]) + rot[2][0] * x_c + rot[2][1] * y_c + rot[2][2] * d))
    return points

obs = get_observation()
cam = obs.cameras["agentview"]          # depth_m, intrinsics, camera_pose are all public
pixels = candidate.mask.nonzero()       # candidate chosen first (see Relative Score Thresholds)
points = deproject(cam.depth_m, cam.intrinsics, cam.camera_pose, zip(pixels[0], pixels[1]))
min_d = min(p[0] for p in points)
shell = [p for p in points if p[0] <= min_d + 0.02]   # foremost 2 cm = the part
part = (quantile((p[1] for p in shell), 0.5),
        quantile((p[2] for p in shell), 0.5),
        quantile((p[3] for p in shell), 0.5))
```

**Why it works + evidence.** Depth, not color, separates a protruding part from the surface
its mask bleeds onto. On libero_goal_swap/task_0 a host-side keyframe-depth measurement put
the mask median 2–4 cm behind the true handle bar; the shell placed the grasp target on the
bar (within 0.026 m). A verifier reproduced the helpers from this text alone and got
consistent, correct part positions in 10/10 episodes. **Limits.** The 2–4 cm advantage over
the raw median was not independently A/B tested by the verifier — what is verified is that
the shell yields correct, seed-consistent part geometry, and that the median bias exists.
Keep the shell thin (0.02 m); thicker shells re-admit the background surface.

---

## AABB Pose Is Not a Centre — Outlier-Sensitive `geometry`

**Trigger.** `localize_object(...).geometry.pose` reports a point that is clearly not where the
object is: grasp descents land consistently short of or beside the part, while the mask itself
looks tight on the object and the cloud's own per-axis median is correct. `extents` look
inflated along one horizontal axis too. Symptom in the numbers: a 4.2 cm bottle whose
`extents` report a 14–22 cm horizontal span.

**Why.** `geometry.pose.position` is the midpoint of the masked cloud's **world-axis-aligned
bounding box**, and `extents` are that box's spans. It is not a centroid and not an oriented
box. A handful of stray mask pixels that pass through a thin part and hit the surface behind it
stretch the box and pull the midpoint along the camera's depth axis, which on LIBERO's
`agentview` is the robot's ±x — so the error reads like a reach problem, not a perception one.
Measured on `libero_goal_swap/task_9`'s bottle: a 1 593-pixel mask whose body sits at x ≈ 0.580
reported `pose.x` 0.474 — **6.5–10.6 cm short on 15/15 seeds** — because 2 stray pixels hit the
table 18 cm behind the bottle (AABB low corner x 0.3673 against a cloud median of 0.5799). The
vertical extent was a clean 0.159 m on every seed; only the horizontal plane was contaminated.
A verifier reproduced it from this entry on a seed it had not seen: `pose.x` 0.4736 against a
cloud spanning 0.5658–0.5879, i.e. **10.63 cm**, with `extents` 7× too long in x.

**Code.** Take per-axis quantiles of the cloud, never the pose, for anything you aim at.
0 ticks.

```python
def quantile(values, fraction):
    ordered = sorted(float(v) for v in values)
    return ordered[int((len(ordered) - 1) * fraction)]


found = localize_object("<object prompt>", camera_name="agentview", target_frame=base_frame)
points = found.point_cloud.points
cx = quantile((float(p[0]) for p in points), 0.5)
cy = quantile((float(p[1]) for p in points), 0.5)
x_lo = quantile((float(p[0]) for p in points), 0.02)
x_hi = quantile((float(p[0]) for p in points), 0.98)
y_lo = quantile((float(p[1]) for p in points), 0.02)
y_hi = quantile((float(p[1]) for p in points), 0.98)
radius = 0.5 * (y_hi - y_lo)          # outlier-trimmed, for a cylinder seen from one side
```

**How to tell this apart from the Foremost-Depth Shell bias.** Both present as "the reported
centre is not where the object is", and they need different fixes. Compare the geometry against
the cloud's own per-axis quantiles: if `pose`/`extents` are off but the quantiles agree with the
object, it is this AABB effect — use the quantiles. If the *quantiles themselves* sit behind the
part, it is mask bleed — use the Foremost-Depth Shell. On task_9 the median was right to ~1 cm
on all 15 seeds and only the AABB was wrong; the 6.5–10.6 cm error there is what a reader who
knew only the shell entry would have mis-diagnosed.

**Second independent solve, behavioural not just geometric (2026-10-21).** On `task_9` a one-variable
switch of the grasp aim from the cloud quantiles to `geometry.pose` scored **0/15** dev seeds, with the
aim **6.5-10.7 cm** behind the cloud median and the jaws closing on air (0.0491-0.0495); the quantile aim
scored 15/15 dev and 15/15 unseen-check in the same solve. So this is not a cosmetic offset: on a
13-16 cm bottle it is the difference between a grasp and no grasp.

**Limits.** Verified for the flat, dark, upright-bottle case on one task and one verifier seed;
the mechanism (AABB midpoint ± depth-axis outliers) is general, the exact bias is a property of
the mask. Per-axis quantiles also remove real extrema, so do not use them where the extreme
point *is* the target (an object's tip, a slot's mouth).

---

## Key Signals

- `segment_text(...).ok` false or zero `segmentations` → prompt not recognized — try a more
  specific or different description (the trace span records every candidate's score and box)
- `score < 0.5` → low confidence — try an alternative prompt before accepting; **never gate on
  a fixed score cutoff** — absolute scores vary 2–3× across seeds, use the relative rule in
  *Relative Score Thresholds* above
- `len(point_cloud.points) < 20` → mask too small or object occluded — try a different prompt
- `media/overlays/` in the recorded run renders the retained top-1 mask — verify it covers the
  intended object

---

## Prompting Strategy

1. **Be specific first** — include color + shape + material: `"blue rectangular box"`
2. **Fall back to generic** — shorter, simpler descriptions; prefer category or shape prompts
   when a product-specific prompt is unreliable
3. **For targets (bowls, plates, racks)** — a material descriptor helps: `"silver bowl"` > `"bowl"`
4. **Always confirm with `get_task_context().language`** — the authoritative instruction
   regardless of suite or task name
5. **Detect target BEFORE grasping** — post-lift re-observation is corrupted by the robot arm
   blocking the camera; detect both object and target while the arm is at home position and the
   view is clean
6. **ARM OCCLUSION PATTERN** — if the target object is in the CENTER of the table, it may be
   hidden behind the robot arm at home position. Move the arm to an observation pose with
   `move_to_pose` before observing. Symptom: SAM3 finds other objects but not the target, and
   scores are low (<0.2) for the intended object.

---

## Placement Z Notes

A mask's projected Z reflects the **camera-facing surface**, not the true top.
For targets with vertical extent (bowls, raised platforms), adjust placement Z from observed
run evidence.

| Target | Z formula | Notes |
|---|---|---|
| `libero_goal_swap` drawer top, upright bottle | `place_eef_z = top_z + carry`, where `carry = eef_z_at_close − object_base_z` | Measured carry **0.1436–0.1452** (proposer quoted 0.1454–0.1483, sd 0.73 mm); landed base **3.5–4.6 mm** above the measured plane on 5/5 seeds. Both terms are measured **before the lift** — from the object's pre-grasp cloud and the achieved eef z at close, never from the held object. The descent then self-limits ~24–26 mm above the commanded z; that shortfall is *not* by itself a contact certificate (`transport.md` § *A Descent That Self-limits…*). |
