---
name: grasp
description: Structural template for pick-and-place programs. Covers the standard grasp-lift-place code skeleton, top-down and cylindrical side grasps, staged approach, and post-lift grasp verification. No task-specific strategies — those are discovered through experiment.
---

# Grasp — Code Template

> This skill provides the **structural skeleton** for pick-and-place programs.
> Task-specific strategies (object-specific offsets, SAM3 prompts, placement geometry)
> are discovered through experiment and added here as the skill library grows — each new
> strategy as its own subsection (trigger → code → evidence), modeled on *Cylindrical Side
> Grasp* below; tables are only for one-line thresholds.
> Programs cannot import modules — all helpers below are pure Python over public contracts.

---

## Standard Pick-and-Place Template

```python
def quantile(values, fraction):
    ordered = sorted(float(value) for value in values)
    return ordered[int((len(ordered) - 1) * fraction)]

def cloud_quantiles(points, x_fraction=0.5, y_fraction=0.5, z_fraction=0.5):
    return (
        quantile((point[0] for point in points), x_fraction),
        quantile((point[1] for point in points), y_fraction),
        quantile((point[2] for point in points), z_fraction),
    )

base_frame = get_robot_state().base_frame
report = {"task": get_task_context().language}

# --- Localize pick object ---
picked = localize_object("<object prompt>", camera_name="agentview", target_frame=base_frame)
if not picked.ok or len(picked.point_cloud.points) < 20:
    raise ValueError("localization failed for pick object")
object_center = cloud_quantiles(picked.point_cloud.points)

# --- Grasp via grasp backend (preferred for irregular objects) ---
grasps = generate_grasps("agentview", picked.segmentation, backend="contact-graspnet")
grasp = select_grasp(grasps, strategy="top_down") if grasps.ok else None
if grasp is not None:
    grasp_pose = grasp.pose
else:
    # --- OR: top-down grasp from observed geometry (simple flat objects) ---
    downward = get_robot_state().end_effector_poses["primary"].quaternion_wxyz
    grasp_pose = Pose(
        (object_center[0], object_center[1], object_center[2] + 0.01),
        downward,
        base_frame,
    )

# --- Staged approach: pre-grasp above, descend, close ---
open_gripper()
pre_grasp = Pose(
    (grasp_pose.position[0], grasp_pose.position[1], grasp_pose.position[2] + 0.12),
    grasp_pose.quaternion_wxyz,
    grasp_pose.frame,
)
if not move_to_pose(pre_grasp, tolerance=0.02, max_steps=250).ok:
    raise ValueError("pre-grasp approach failed")
if not move_to_pose(grasp_pose, tolerance=0.015, max_steps=250).ok:
    raise ValueError("grasp approach failed")
close_gripper()

# --- Lift, then VERIFY the object actually moved with the gripper ---
before_z = quantile((point[2] for point in picked.point_cloud.points), 0.5)
lift = Pose(
    (grasp_pose.position[0], grasp_pose.position[1], grasp_pose.position[2] + 0.15),
    grasp_pose.quaternion_wxyz,
    grasp_pose.frame,
)
move_to_pose(lift, tolerance=0.02, max_steps=250)

after = localize_object("<object prompt>", camera_name="agentview", target_frame=base_frame)
grasp_verified = (
    after.ok
    and len(after.point_cloud.points) >= 20
    and quantile((point[2] for point in after.point_cloud.points), 0.5) - before_z > 0.02
)
report["grasp_verified"] = grasp_verified

# --- Re-observe for placement target ---
target = localize_object("<target prompt>", camera_name="agentview", target_frame=base_frame)
if not target.ok:
    raise ValueError("localization failed for placement target")
target_center = cloud_quantiles(target.point_cloud.points)
surface_z = quantile((point[2] for point in target.point_cloud.points), 0.9)

# --- Transport ---
above_target = Pose(
    (target_center[0], target_center[1], lift.position[2]),
    grasp_pose.quaternion_wxyz,
    base_frame,
)
move_to_pose(above_target, tolerance=0.02, max_steps=300)

# --- Place ---
release = Pose(
    (target_center[0], target_center[1], surface_z + 0.03),
    grasp_pose.quaternion_wxyz,
    base_frame,
)
move_to_pose(release, tolerance=0.02, max_steps=250)
open_gripper()

result = report
```

---

## Cylindrical Side Grasp (bottles, cans)

Derive the grasp height from robust point-cloud quantiles instead of a fixed world height.
Handwritten poses from observed geometry (OBB, side-grasp, top-down) are often more reliable
than backend candidates for specific object classes — record which per object below.

```python
zs = sorted(float(point[2]) for point in picked.point_cloud.points)
z_low = zs[len(zs) // 10]
z_high = zs[(9 * len(zs)) // 10]
grasp_z = z_low + 0.5 * (z_high - z_low)
```

Form pre-grasp, grasp, and lift poses from the observed center and extent. Stage the approach
outside the object, preserve the grasp orientation and closed-gripper command through the lift,
and avoid abrupt transport.

**A `solve_ik` success is not an executable pose.** For a bottle with a neck, a composed
horizontal side grasp at mid-body solved on both IK backends — a verifier re-confirmed that on
its own seed — yet stalled 0.45–0.87 rad short after 70–600 servo steps at **every** height
(z 0.064 through 0.30, so not a table-clearance problem), while a straight top-down descent in
the home orientation covered the same reach in 5–71 steps. A 90° wrist roll while holding froze
at 151 of 250 steps on 5/5 verifier seeds. Prove an unusual orientation with a short
`move_to_joints` before you build versions on it; if it stalls, change the grasp family rather
than the stand-off.

---

## Confirm the Grasp from the Public Gripper Closure

**Trigger.** Any grasp whose graspable band is only a few centimetres tall (bottle neck, rim,
lip, handle edge), or any grasp height computed open-loop from a cloud quantile: the joint servo
settles ~1 cm above its target, so the jaws can close on air while every later stage still
returns `ok=True` — the lift, the flip and the place all "succeed" while carrying nothing.

**Code.** `close_gripper()` returns a result whose `diagnostics["final_position"]` is the jaw
position on a 0–1 scale (~8 cm of stroke). It is a **string** in the JSON payload — `float()` it.

```python
CLOSURE_MIN = 0.10      # air reads 0.035-0.05 here; anything you mean to hold reads far above it
STROKE_M = 0.081        # metres per unit of final_position — measured, do not assume


def close_and_read():
    result = close_gripper()
    if result.diagnostics and "final_position" in result.diagnostics:
        return float(result.diagnostics["final_position"])
    return None


closure = close_and_read()
width_seen = closure * STROKE_M if closure is not None else None
predicted = 0.030       # the object's width AT THE PINCH HEIGHT, from its own point cloud
gripped = (closure is not None and closure > CLOSURE_MIN
           and abs(width_seen - predicted) < 0.008)
```

**Measured calibration** (`libero_goal_swap/task_9`, reproduced by a verifier on seeds it had not
seen): air-close **0.0405–0.0494** (4–8 mm — pads nearly shut); a 1.2 cm bottle neck **0.1506**
(1.22 cm); a body pinch on a tapering bottle at 45 % height **0.4495–0.4673** (3.6–3.8 cm, which
is that height's width — the bottle's 4.3 cm base diameter would read 0.53). Stroke factor pinned
at 8.09 cm per unit from a same-run pair. The verifier read 5/5 with the plain threshold.

**Prefer the jaw width over any visual check.** A held object **merges with the gripper** in a
wide-camera mask, so re-segmenting the held object to prove you still have it measures perception,
not grasp. Measured on `libero_goal_swap/task_8` (15/15 seeds): after the lift, `segment_text("bowl")`
returned the bowl **plus pads and palm** — an OBB of 0.332 x 0.170 x 0.130 m against the bowl's own
0.109 m extent — so any shape or size filter rejected a **good** grasp and the program aborted with
`bowl_after: null`. A `null` measurement is not evidence of a failed grasp; it is evidence that the
check could not see. Read the robot's own state instead:

```python
close_gripper()
jaw = float(get_robot_state().gripper_positions["primary"])   # 0 = jaws met, nothing held
held = jaw > 0.02        # 4x below the weakest measured stall (0.083 on a ~7 mm rim)
```

Measured: a rim pinch stalls the jaw at 0.083-0.091 on 15/15 seeds, an empty close approaches 0, and
`close_gripper` costs 15 ticks on a rim versus ~3 on air. This one change took that task from 0/15 to
14/15 dev. Keep a visual check only as a *diagnostic*, never as the gate.

**Limits, all measured.** (a) `CLOSURE_MIN` alone separates only "met something" from "shut on
air"; the strong check is the predicted width, and a reading far *below* the prediction is an
edge grasp — a 0.272 read against a predicted 0.36–0.39 was an off-centre body grasp that the
following 90° roll could not rotate. (b) A retry ladder that reopens the jaws and lowers 1.5 cm
per attempt walks the **open** lower pad into the table: at a 0.056 m grasp height, retry 1 puts
it 1.2 mm above the table top and retry 2 **3.4 mm below** it. Retry with the jaws closed, or
floor the descent on the perceived support height. (c) `final_position` agrees exactly with
`gripper_positions["primary"]`, so either read works.

**Counter-example — a curved rim is not a caliper.** The predicted-width check assumes the two pads
close on two parallel faces of a known width. On a **curved rim or lip** they do not: three commanded
grip heights on the same shallow bowl gave closures **0.0605–0.0618, 0.0970–0.1092 and
0.1170–0.1255**, all three real contacts, and only the deepest one was reliable
(`libero_goal_swap/task_8`, 30 grasps). Applying the check literally to the valid ones — rim ~5 mm
predicted, closure 0.098–0.109 → 8.0–8.8 mm seen → 3.1 cm off — would have **rejected 15/15
successful grasps**. Worse, the close-return value is a **transient** under this velocity jaw: it
decayed to **0.0373–0.0415 (3.0–3.4 mm, the rim wall)** by the end of the lift on 5/5 seeds, so the
number is sampling-time-dependent as well as object-dependent.

Use the predicted width only where the pads face flat, parallel surfaces (a bottle neck, a body with
two opposed flats). On a rim/edge grasp the grasp test is the **post-lift re-localization**: the
object's own cloud centre must rise more than 2 cm against the pre-grasp centre. What survives from
this section in every case is its other half — the **~9 mm servo lag**, which was the actual root
cause of the slips this task saw.

---

## Shallow-Bowl Rim Pinch: Aim Below the Measured Rim

**Trigger.** A shallow, wide bowl (wider than the jaw stroke) whose only graspable band is its rim.
A rim pinch commanded at the measured rim height is marginal: the pads end up on the rounded lip and
the object slips during the lift.

**Code.** Measure the rim from the object's own cloud, then command the pinch **2 cm below** it —
the pose servo arrives ~1.1 cm short of the command, which is exactly the vertical wall.

```python
def quantile(values, fraction):
    ordered = sorted(float(v) for v in values)
    return ordered[int((len(ordered) - 1) * fraction)]


points = bowl.point_cloud.points
rim_z = quantile((p[2] for p in points), 0.98)     # rim top
y_hi = quantile((p[1] for p in points), 0.98)      # outermost +y rim point
# The +y rim because the reset hand's jaw axis is world y: one pad inside, one outside.
grip = (quantile((p[0] for p in points), 0.50), y_hi - 0.002, rim_z - 0.020)
open_gripper()
move_to_pose(Pose((grip[0], grip[1], grip[2] + 0.15), downward, base_frame),
             tolerance=0.025, max_steps=250)       # ~75 ticks
move_to_pose(Pose(grip, downward, base_frame), tolerance=0.015, max_steps=150)  # ~40 ticks
close_gripper()                                    # ~15 ticks, closure 0.098-0.109
# Verify by re-localization, never by the closure: the object's cloud centre must rise > 2 cm.
```

**Evidence.** `libero_goal_swap/task_8`: commanded `rim-0.008` **arrived** at `rim-0.0002..+0.0010`
(9 mm servo lag) and held 13/15 — seeds 54 and 64 slipped with bowl z rise 0.0003/0.0005 m and a
closure indistinguishable from the successes. Commanded `rim-0.020` arrived at **0.0109–0.0122 m
below the rim** and held **15/15 dev and 15/15 unseen**; a verifier that saw only the library and the
proposal reproduced it on 5/5 (arrived −0.0113..−0.0121, closure 0.1056–0.1092, rise
0.0911–0.1097). Whole pick-and-place costs 272–299 ticks of a 1000-tick budget.

**Limits, measured.** (a) The pinch **slips 1.3–2.1 cm through the jaws while still holding**, and the
rise test cannot see it (the bowl's z-median rose only 1.4–18.8 mm less than the eef, ~0 on one
seed). Any placement z derived from the grasp depth therefore carries that slip as error; either
measure the hold offset after the lift or descend to contact. (b) With the rim re-localized after the
lift, expect the cloud's **depth-axis median to shift 1.7–2.2 cm** while its 10.2 cm extent stays
unchanged — that is projection bias on the held object, not motion; use the pre-grasp measurement for
the carry offset instead (see `transport.md` §*Carry Offset From the Pre-Grasp Measurement*).

## Body Pinch at the Centre of Mass for a Necked Bottle

**Trigger.** An upright bottle or can whose neck is narrower than its body, to be **carried**
(lifted, rotated, transported), not merely lifted. A top-down jaw can only take the neck — the neck is
the only part the pads can reach from above without the palm fouling the body — and a neck pinch is a
weak carry, though **not** an impossible one: its friction-torque margin is roughly
grip·μ·half-neck-width ≈ 0.08 N·m against the ≈0.14 N·m a centre 7 cm below the grip needs,
so the object rotates at the jaws as soon as the hand translates fast or rotates. Measured: a 1.2 cm
neck pinch lifted a 15.8 cm bottle cleanly and lost it during an 11 cm lateral retract; a 2 cm-per-move
segmented carry did hold it, so the limit is acceleration, not distance.

**Bounded correction (2026-10-19).** A top-down pinch on the **cap** (jaw 0.149-0.168 normalised, a
12-13 mm contact) carried a bottle through a **22 cm lift** on 15/15 seeds, video-confirmed, in a run
with no library to consult. So "a neck pinch cannot carry" is **too strong as written**. What has
never been demonstrated is carrying through a **wrist reorientation**: that move was refused by
PyRoki (`solution exceeds the public pose residual limits`) before any motion, on 15/15 seeds. The
honest claim is: *a neck/cap pinch carries a lift-and-place; it has not been shown to survive a
reorientation, and the torque argument predicts it will not.* Do not state the untested half as
measured, and prefer the body pinch when a reorientation is coming.

**Pad offset: the pads are at the eef pose, not 45 mm below it.** This entry used to say the pads
sit ~0.045 m below the eef; two measured runs exclude that. On `libero_goal_swap/task_2`, a top-down
cap pinch closed at **0.1847–0.1860** with the achieved eef at z 0.1388–0.1392 (cap top 0.1459–0.1462),
and re-running the same program with the entry 12.31 mm higher closed at **0.1552–0.1554** with the
eef at 0.1513–0.1515 — a 0.0297 jaw-travel drop for a 12.31 mm rise, i.e. a taper of 0.196 mm of
contact width per mm of height, not a fixed offset. **The closure magnitude is the decisive test:** a
45 mm offset would have put the pads on the bottle's 39 mm body and read closure ≈ 0.48, not 0.185.
Bounded by those two runs: the offset is **≥ −7.0 mm and ≤ +5.3 mm** — "pads at the eef pose" to
within a pad half-height. Do not add a pad-offset constant to a grasp height; measure the achieved eef
and use the closure to confirm what the jaws actually met.

**Code.** Pinch the body at 45 % of its height, hand z-axis horizontal, jaws straddling the body.

```python
def quat_from_columns(xa, ya, za):          # hand axes in the world frame, right-handed
    m11, m12, m13 = xa[0], ya[0], za[0]
    m21, m22, m23 = xa[1], ya[1], za[1]
    m31, m32, m33 = xa[2], ya[2], za[2]
    trace = m11 + m22 + m33
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        return (0.25 * s, (m32 - m23) / s, (m13 - m31) / s, (m21 - m12) / s)
    s = math.sqrt(1.0 + m11 - m22 - m33) * 2.0
    return ((m32 - m23) / s, 0.25 * s, (m12 + m21) / s, (m13 + m31) / s)


q_side = quat_from_columns((0.0, 0.0, -1.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
z_body = z_low + 0.45 * (z_neck - z_low)        # quantiles of the object's own cloud
pre_grasp = Pose((cx, cy + 0.02, z_body + 0.02), q_side, base_frame)   # SHORT stand-off
grasp = Pose((cx, cy, z_body), q_side, base_frame)
```

**Evidence.** `libero_goal_swap/task_9`: 14/15 development, 13/15 unseen-check, **48/50
held-out**; a verifier reaching for the same task from this entry alone scored **5/5 on its
first attempt** with closures 0.4495–0.4673, carrying the bottle 0.24 m across and 0.16 m up.
Grasp → lift → transit → release cost 496–638 ticks across 85 episodes (15 dev, 15 unseen, 50 held-out, 5 verifier) against LIBERO's 1000-tick ceiling.

**Limits.** (a) Keep the stand-off **short**: a 9 cm +y stand-off did not converge from home on
5/5 verifier seeds (145–153 of 250 steps) while the following 2 cm move converged in 33–54 — do
not raise on a long stand-off's TIMEOUT, and prefer ~2 cm. (b) The carry this entry supports is
**upright**. A 90° wrist roll of the held body froze at 151/250 steps on 5/5 seeds and the bottle
stayed vertical; the task still passed because a bottle released standing on the receiving plane
is accepted. Do not read this entry as a way to lay a bottle down.

---

## Key Conventions

**Grasp with `close_gripper()`, never `set_gripper(0.0)`** — they are not equivalent. `close_gripper()` accepts a *contact stall* as success: the jaw stops on the object, the call returns `ok=True`, and the harness records the commanded gripper as closed.

```python
close_gripper()                      # correct: contact stall counts as closed
# set_gripper(0.0)                   # WRONG when grasping: times out, leaves stale open command
lift = move_to_pose(above_pose)      # keeps commanding closed, object is retained
```

**Never infer grasp success from a completed call** — `close_gripper()` returning does not mean
the object is held. Verify with the post-lift re-localization pattern above.

**Motion result guard** — every `move_to_pose` / `move_to_joints` / `plan_motion` returns a typed
result; check `.ok` before depending on the motion. Requested unavailable backends return typed
failures; the harness never falls back implicitly.

**Backend choice** — `MotionStrategy()` defaults to PyRoki IK plus straight-line joint
interpolation. That default is **not collision-aware**, and it does not say so: the arm sweeps
whatever the interpolated path crosses, the call returns `ok=True`, and the damage shows up later
as a missing object or an unexplained failure. Select `trajectory_planner="curobo"` (planned
transit, PyRoki IK) or `pose_planner="curobo-integrated"` (cuRobo solves pose and path together)
for free-space transit, and keep interpolation for segments where contact is intended — the
observed cloud contains the target and any held object, so a collision-aware planner refuses the
approach into a grasp by construction.

`ik_solver="curobo"` with `trajectory_planner="interpolation"` is checked *nowhere*: standalone
IK answers against an empty world, and interpolation checks nothing. The returned `Trajectory`
reports `collision_aware=False` — read it rather than assuming that naming cuRobo anywhere in the
strategy bought a scene check.

For `solve_ik`, PyRoki or cuRobo are usually needed.

**Quaternions are wxyz** in every `Pose`.

**Wrist vs fingertips**: a public `Pose` targets the **end-effector frame** (LIBERO's
`robot0_eef`) — the grasp point between the fingertips, not the wrist. The −0.1 m conversion to
`panda_hand` happens inside the IK provider (`providers/pyroki/client.py`, `PANDA_EEF_TO_HAND_OFFSET_M`),
so a program never applies it. Treating the target as the wrist and adding your own offset puts
the grip 10 cm off.

**Grasp verification thresholds** — add validated per-object entries here as you discover them.
This table is for one-line signals and numeric thresholds only; a grasp *strategy* that needs
procedure or code (e.g. constructing a manual grasp pose when a backend fails) gets its own
subsection like *Cylindrical Side Grasp* above, with the working snippet:

| Object | Verification signal | Threshold | Notes |
|---|---|---|---|

---

## Object Geometry Utilities

```python
geometry = estimate_geometry(picked.point_cloud)
# geometry.pose (framed center + orientation), geometry.extents, geometry.point_count

# Z range (grasp height tuning), pure Python:
zs = [float(point[2]) for point in picked.point_cloud.points]
height = max(zs) - min(zs)
```
