---
name: transport
description: Motion patterns for moving objects between locations — multi-step waypoints, safe transit sequences, staged Cartesian moves, collision avoidance during transport. Grows through experiment.
---

# Transport — Motion Patterns

> This skill covers **how to move** once an object is grasped: waypointing, safe transit,
> intermediate stops, and collision avoidance. Discovered through experiment — add each
> validated pattern as its own subsection (trigger → code → evidence), modeled on
> *Pre-Probe IK Conditioning* below; tables are only for one-line lookups.

---

## Waypoint Sequences

Add a subsection per multi-step motion pattern that prevents collisions, slip, or IK failures
during transport (trigger condition → waypoint code → why it works + evidence). Use the table
only for one-line pointers to those subsections.

| Pattern | Trigger | Section |
|---|---|---|
| IK pre-probe | Low-Z placement target | *Pre-Probe IK Conditioning* below |

---

## Pre-Probe IK Conditioning

Before grasping, pre-visit the placement target at multiple approach heights. This seeds the
IK solver so it stays on the correct branch when placing after the grasp.

```python
# BEFORE grasping, probe the target at decreasing heights:
for probe_z in (target_z + 0.08, target_z + 0.06, target_z + 0.04, target_z + 0.02):
    move_to_pose(Pose((target_x, target_y, probe_z), downward, base_frame),
                 tolerance=0.03, max_steps=200)
go_home()
# NOW grasp — IK is conditioned for placement
```

**Why it works**: IK solvers maintain local branch continuity from their last configuration.
Visiting the placement target before grasping seeds the solver on the branch that can reach
that XY at low Z — so after grasp + home reset, the arm finds the same branch.

**When to use**: Any task where the placement target is at low Z with an observed XY. When
branch conditioning is not enough, request collision-aware planning explicitly:
`move_to_pose(..., strategy=MotionStrategy(trajectory_planner="curobo"))`.

---

## Safe Transit (Lateral Escape Before Lift)

Add a subsection here (trigger → code → evidence) when the arm must move laterally before
lifting to avoid sweeping through obstacles (opened drawers, adjacent objects, cabinet edges).

### Lift Before You Translate, Then Segment the Carry

**Trigger.** A pinched (not body-grasped) object that has to change **height and xy at the same
time** — a placement on a raised support, a carry across the table.

**Measured limit.** A verifier on `libero_goal_swap/task_2` composed a top-down cap pinch with the
placement snippet literally, so the program made **one 0.60 rad diagonal move that lifted 0.272 m
and translated 0.270 m at once**. The bottle left the jaws on **2/5** seeds (jaw 0.185 → 0.0138 /
0.0136). Split into *lift vertically to the transit height first, then carry in segments of
≤ 0.02 m*, the same program held the jaw at **0.1853–0.1857 across 16–17 waypoints and passed 5/5**.

**Why it is the same rule as the neck-pinch one.** The cap/neck pinch's friction-torque margin is
small (`grasp.md` § *Body Pinch at the Centre of Mass for a Necked Bottle* derives it); a diagonal
move spends that margin on vertical acceleration **and** on the yaw in one call. The limit is
acceleration and simultaneity, not distance — a **2 cm** per-move segmented carry already held an
object that an **11 cm** single retract dropped.

**Do not read a vertical lift as transit evidence.** The proposing task itself reported "the bottle
rose 0.2743 m with no slip", which was true and said nothing about a lateral carry; the slip only
appeared when someone drove the two snippets together. When you grade a carry entry, ask which axes
moved in the *same* call.

---

## Placement Approach

Add a subsection here (trigger → code → evidence) for approach sequences above the target
(hover height, descent speed, drop vs. lower) that prevent bounce, tip, or miss.

### Stage the Approach: Never Combine a Large Wrist Rotation With a Deep Reach

**Trigger.** A single move that both rotates the wrist a long way and reaches low/forward; symptom is
that the eef travels **away** from its target on at least one axis while the other joints look fine.

**Measured** on `libero_goal_swap/task_9`: the plan reported `max_joint_delta_rad: 0.05` and then drove
the eef from x 0.4513 to 0.3456 — 16 cm in the wrong direction — ending short of the target. Splitting
it into **translate with the reset orientation → rotate in place at height → vertical descend** made
every stage converge on 15/15 seeds.

```python
home_q = [float(v) for v in get_robot_state().end_effector_poses["primary"].quaternion_wxyz]
goto([x, y, top + 0.10], home_q,  base_frame)   # carry the approach over the object
goto([x, y, top + 0.10], grasp_q, base_frame)   # rotate where nothing can be hit
goto([x, y, grasp_z],   grasp_q, base_frame)   # descend on the object
```

A **second** consequence, measured the same day on `libero_goal_swap/task_8`: the first
home->low-standoff move **always** lands 4-8 cm short in +y, and the "sensible robustness" retry then
sweeps the 8 cm-wide open jaw horizontally at the target's own mid-height — it knocked the bottle over
on 7/15 seeds. Re-issue a long approach **at height**, never sideways at grasp height.

### A Descent That Self-limits on the Held Object Is a Placement, Not a Failure

When you carry an object down onto a receiving surface, the descent may stop **short of its
commanded height** because the object is already resting on the surface — the joint servo cannot
close the last few centimetres and returns `ok=False`. That is a successful placement, not a
stuck stage. Measured on `libero_goal_swap/task_9` (verifier, 5/5 seeds): a descend-to-release
stage froze at eef z 0.3401–0.3403 against a commanded 0.2879–0.2889 — **5.2 cm short, with xy
within 5 mm and the short axis exactly z** — because the upright bottle's base met the plane.
Every seed passed. The read to use is *which axis is short and by how much*: a pure-z shortfall
with xy converged is contact; a joint-space residual on several axes is a servo failure.

**But a small pure-z shortfall is not evidence of contact (2026-10-20).** On
`libero_goal_swap/task_2` a 2 mm threshold read `contact=True` at a shortfall of exactly **4.0 mm**
with xy within 1.2 mm — **on the two seeds where the bottle had already dropped and the jaws were shut
on air** (jaw 0.0131/0.0132). The ordinary servo shortfall is the same size as the threshold, so the
shortfall alone certifies nothing. Require **> 10 mm**, or a positive corroboration such as
`gripper_positions["primary"] >= 0.10` (still holding something), and treat contact inferred from a
few millimetres as *unverified* rather than as a measured placement.

For a **held object**, the release can be judged the same way: if `open_gripper()` returns
`ok=False` after a few steps while `gripper_positions["primary"]` has moved most of the way open
(measured 0.72–0.94), the object is released — read the achieved jaw position, not the flag.

**`ok=False` is not always a servo wall.** On `libero_goal_swap/task_8` the placement descent
returned `ok=False` with `failure_code=terminated` — the benchmark's success predicate had already
fired at object/support contact and ended the episode. The outside signature is the same as a
servo-limited descent (pure-z shortfall, xy converged, one axis short), so a program must read
`failure_code` before it diagnoses anything: on `terminated`, do not retry, do not raise, and do not
assume a release happened.

`failure_code` may also be **absent**. On `libero_goal_swap/task_2` the same descent returned
`ok=False` with `failure_code=None` on **5/5** seeds and never `terminated`; the termination appeared
only later, in `outcome.json` (`termination_reason=task_succeeded`). So `None` is not evidence of a
failed motion and must not send a program into a retry: grade the measured shortfall **and** the jaw
(see the 10 mm rule above), not the code.

The transit that got a held bottle over a 0.33 m obstacle without sweeping through it (5/5 seeds,
40–42 + 44–51 + 122 steps): raise at the grasp xy to `q99(obstacle_z) + 0.07`, translate at that
height, then descend in world z at the placement xy.

### Close the Loop on the Measured Pose After Every `move_to_pose`

**Trigger.** A pose is reached only approximately — `move_to_pose` returns
`ok=False, code=controller_failed` while the arm visibly got there — or a grasp/place that needs
better than ~1 cm sits just above or beside its target.

The residual is **systematic**: re-issuing the pose with the measured error added converges. Measured
on `libero_goal_swap/task_8`: first attempt left **8.4 mm** and reported `ok=False`; one correction
left **0.6 mm**. So `ok=False` with a small final error is **not** a failed motion — read the measured
eef position before treating a stage as failed. Cap the corrections at 3 so an unreachable pose
cannot eat the episode; each costs ~6-10 ticks.

```python
def goto(goal, quat, frame, tolerance=0.006, attempts=3):
    goal = [float(v) for v in goal]
    want = list(goal)
    for attempt in range(attempts):
        move_to_pose(Pose(position=want, quaternion_wxyz=quat, frame=frame), tolerance, 200)
        eef = [float(v) for v in get_robot_state().end_effector_poses["primary"].position]
        if max(abs(eef[i] - goal[i]) for i in range(3)) <= tolerance:
            break
        want = [want[i] + (goal[i] - eef[i]) for i in range(3)]
```

### Carry Offset From the Pre-Grasp Measurement, Not From the Held Object

After a rim/edge pinch the object's centre hangs **off the eef**, so the placement eef target must be
corrected by that offset. Measure it **before any motion**, from the same unoccluded cloud you picked
the grasp with; re-localising the *held* object to recover it aims the placement 1–2 cm off, because
the held cloud is biased along the camera's depth axis.

`quantile` is the three-line helper defined in `grasp.md` / `localize.md`; copy it into your program
(cross-file references are not imports).

```python
# BEFORE motion: grip point and object centre from one cloud.
points = bowl.point_cloud.points
rim_z = quantile((p[2] for p in points), 0.98)
y_hi = quantile((p[1] for p in points), 0.98)
grip = (quantile((p[0] for p in points), 0.50), y_hi - 0.002, rim_z - 0.020)
centre = (quantile((p[0] for p in points), 0.50), quantile((p[1] for p in points), 0.50))
design_offset = (centre[0] - grip[0], centre[1] - grip[1])
place_x = plate_centre[0] - design_offset[0]
place_y = plate_centre[1] - design_offset[1]
```

**Evidence.** `libero_goal_swap/task_8`: measured carried offset in y was **−0.0481..−0.0512 m**
against a design offset of **−0.0437..−0.0443** — the rigid-pinch premise holds and a pendulum
reading is refuted; aiming the eef at the plate centre would have left ~5 cm of y error. The x term
reproduces in sign but the entry **under-predicts it by 4.7–7.6 mm**, which is exactly the observed
`+6.6..+13.0 mm` landed residual in y; add ~5 mm to `|design_offset|` when sub-centimetre placement
matters. (Proposer's version was mock-validated but never run as a version; the verifier reproduced
the mechanism, not a sweep at 5/5.)

### The Release Push: Opening the Jaws Displaces the Object

**Trigger.** An object grasped at its rim/edge (jaws on a thin part, object hanging to one side of
the eef) lands systematically off-centre in the direction of the grasp, and the pass count hides it
until one seed falls off the support.

**How to measure.** Place 5-10 times, record `final_object_centre - support_centre`, and aim the
place pose short by the mean of that vector, *against* the grasp direction.

**Measured** on `libero_goal_swap/task_8`: the bowl landed **+1.0..+2.5 cm in world y on all 15 dev
seeds** (mean 1.42 cm, median 1.2 cm), x unbiased; the one seed outside the goal tolerance was the
+2.5 cm tail. Aiming 1.3 cm short took the task from 14/15 to 15/15. **That 1.3 cm is a fitted
constant for this scene, not a physical universal** — a new scene needs its own measurement.

### Grade a Placement by the Object's LANDED POSE

A pass count cannot tell two placements apart, and on some benchmarks the episode ends **at
contact**, so "it passed" does not even mean "it was released". Grade the geometry directly, in the
support plane, at the instant the episode is decided:

- **object-centre residual to the target-centre** (x, y in `robot_base`), from the object's own
  per-axis cloud quantiles — not `estimate_geometry().pose`;
- **object-bottom minus support-top**, using `q90` of the support's top surface (a plate's rim, not
  its median).

Both are measured against the *landed* object. Measured on `libero_goal_swap/task_8` (verifier,
seeds 51–55, 5/5): residual to plate centre **7.2–13.0 mm**, object-bottom minus support top
**−7.2..+0.1 mm** — and those numbers are taken **at contact while the gripper is still closed**,
because this benchmark's predicate fires there. Say which instant you measured; a residual taken
before release is not a settled placement.

If the arm must back out first, release and retract, then re-observe; a retreat that returns after
**1 tick** with `ok=False` is evidence the episode was already over, not that the retreat failed.

### Physics Settling After Release

After releasing a grasped object, the object needs a few control ticks to settle before the
episode's success predicate is evaluated — observation calls do not advance time.

**`set_gripper(1.0)` does NOT buy those ticks.** When the jaw is already at the requested
width the adapter returns immediately with `steps_executed = 0`, so a program relying on it to
settle gets **no** ticks at all. Measured on `libero_goal_task/task_1` and independently on
`libero_spatial_task/task_4`, which lost a whole program version to the false hypothesis. On
LIBERO, Robosuite and RoboCasa `set_gripper` now also *refuses* any value between 0.0 and 1.0
with `unsupported`, so there is no partial-width call to fall back on either.

**`set_grippers({"primary": 1.0})` — the multi-arm form — buys no ticks either.** Measured on
`libero_goal_swap/task_9`: `ok=False`, `steps_executed=0` on 15/15 calls across five seeds, with
`settle_steps_total` 0 on every episode. The warning above names only `set_gripper`, which is
easy to read past when the program happens to use the plural form.

Advance time with something that actually steps. A one-tick hold of the arm's own measured
joints is the cheapest, and it commands the gripper explicitly rather than relying on a latched
value:

```python
open_gripper()
state = get_robot_state()
for tick in range(settle_ticks):       # measure how many you need; do not assume
    step(RobotAction(arms={"primary": ArmCommand(
        "joint_position",
        state.joint_positions["primary"],
        gripper_position=1.0,
        embodiment=state.embodiment,   # required: the default is "robosuite"
    )}))
```

Check `steps_executed` on whatever you use, rather than assuming a call advanced time.

---

## Basket / Container Placement

General rule: transit at `rim_z + 0.30`, release at `rim_z + 0.10`. Never descend inside the
container — drop from above.

**NEVER call `go_home()` while holding a grasped object.** Going home during transport opens the
arm configuration and drops the object. Transport in one continuous arc: lift → translate →
descend → release.

| Object type | Transit height | Release height | Notes |
|---|---|---|---|
