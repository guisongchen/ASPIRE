# 后端选型：SAM3 / PyRoki / Contact-GraspNet / cuRobo

这份文档把四个 provider 服务讲清楚：各自**负责什么、什么时候该用、参数与返回值是什么、代价多大**，
并补上两个容易被省略、但实际最影响结果的话题：**插值到底做了什么**，以及**要不要再加一个深度估计模型**。

内容来自两处，文中标了出处：

- **文档与接口**：`docs/api-reference.md`、`docs/optional-backends.md`、worker/verifier prompt 模板。
- **本 campaign 的实测**：`libero-pro/libero_goal_swap/task_9` 的 trace 与运行记录（`trace/calls/`、
  `episode/steps.jsonl`、`episode/reset.json`），以及 task_0 的验证报告。凡是"实测"的数字都可以复核。

> 术语（campaign / task / tick / step / seed 分区）见 [glossary.md](glossary.md)；cuRobo 的安装与启动见
> [optional-backends.md](optional-backends.md)。

---

## 1. 四个服务一览

| 服务 | 端口 | 常驻显存（实测） | 职责 | task_9 是否真正调用 |
|---|---|---|---|---|
| **SAM3** | 8114 | **3 852 MiB** | 文本 prompt → 像素掩码 + 分数 | ✅ 每集 3–5 次 |
| **PyRoki** | 8116 | 无 CUDA 上下文 | IK：Pose → 关节角 | ✅ 每集 10–11 次 |
| **Contact-GraspNet** | 8115 | 228 MiB | 点云 + 掩码 → 抓取位姿候选 | ❌ 一次未用 |
| **cuRobo** | 8118 | 674 MiB | IK、碰撞感知规划、integrated 位姿+路径、双臂 14-DoF | ❌ 一次未用 |

harness 只在程序**显式请求**某个后端时才调用它（`optional-backends.md`："Backend fallback occurs only
when the program requests it explicitly"）。所以"四个都起着"和"用了几个"是两件事：task_9 的抓取姿态
来自几何，运动全部走 PyRoki + 关节伺服。

---

## 2. 它们各自在数据流的哪个位置

```
任务语言 ──► prompt 字符串
                ▼
        segment_text("agentview", "bottle") ──HTTP──► SAM3 :8114
                ▼ 掩码 + 分数
        mask_to_point_cloud(mask, 深度, 内参, 外参)
                ▼ 世界坐标点云（robot_base）
        estimate_geometry / 逐轴分位数
                ▼ 目标位姿（bx, by, z_body…）
        solve_ik ──► PyRoki :8116 ──► 关节角
                ▼
        move_to_joints / move_to_pose / execute_trajectory ──► 推进 tick
                ▲
        （可选）plan_motion(..., MotionStrategy(...)) ──► cuRobo :8118
        （可选）generate_grasps(..., backend="contact-graspnet") ──► :8115
```

**`localize_object` 不是一次 provider 调用，而是一个组合**：trace 里它展开成 `segment-text` →
`sam3-segment-text`（HTTP 到 SAM3）→ `mask-to-point-cloud` → `estimate-geometry`。所以"每集 5 次
`localize_object`" = 5 次 SAM3 推理。

---

## 3. SAM3 —— 唯一的感知入口

**接口**：`segment_text(camera_name, text)` → `SegmentationSet`（最多 5 个候选，各带
`mask` / `score` / `box_xyxy` / `label`）；高层封装 `localize_object(query, camera_name=...,
target_frame=...)` → `LocalizationResult`（`.ok`、`.point_cloud`、`.geometry.{pose, extents, point_count}`）。

**服务的 HTTP 契约**（离线复算时直接用过）：

```json
POST /segment        {"image_base64": "<png>", "text_prompt": "bottle"}
                     → {"results": [{"mask_base64", "shape": [512,800], "box", "score", "label"}, …]}
POST /segment_point  {"image_base64": "<png>", "point_coords": [[u,v], …]}
                     → {"scores", "masks_base64", "masks_shape", "masks_dtype"}
GET  /healthz
```

`mask_base64` 解出来就是 512×800 的 uint8 掩码——**离线也能复算**，不必再起 episode。

**task_9 实测**：prompt 分数 `"bottle"` 0.953 / `"wine bottle"` 0.926 / `"rack"` 0.32 / `"wine rack"`
0.207；瓶掩码 1 593 px；架掩码 **30 490 px**（含垫板）；每次 `localize_object` 1.5–2.5 s；**tick 成本 0**。

**两个失败签名都来自它**

1. **掩码透洞**：瓶脖处 2 个像素深度 1.14 / 1.19 m（18 cm 后的桌面），把世界 AABB 从 1.4 cm 撑到 22 cm，
   `geometry.pose` 偏 **10.63 cm**。
2. **掩码含背景**：`"rack"` 覆盖架下层板条 + 垫板，点云 z 跨度 0.44 m，"crest" 只是掩码的最远上角。

**处置（全部 0 tick）**：逐轴分位数而非 AABB 中点；按深度剔离群点；薄突出物用 Foremost-Depth Shell；
多候选用相对阈值 + 空间聚类。**不需要换模型。**

**它也是显存最重的组件与并发事故的受害者**：task_0 里 4 个并发 Contact-GraspNet 调用打爆 12 GB 卡后，
SAM3 开始返回 HTTP 500，而 `/openapi.json` 仍是 200。

---

## 4. PyRoki —— 默认 IK 后端

**接口**：`solve_ik(target_pose, seed_joints=None, *, arm="primary", backend=None)` → `IKResult`

| 字段 | 含义 / 实测 |
|---|---|
| `ok` / `joint_positions` | 是否成功、7 个关节角 |
| `diagnostics.provider` | `pyroki` / `curobo` |
| `diagnostics.position_error_m` / `orientation_error_rad` | 残差，成功时通常 1e-7 / 0.001 |
| `error.code = "ik_failed"` + `details` | 失败时的门限与实测残差：`max_position_error_m 0.02`、`max_orientation_error_rad 0.15` |
| `seed_joints` | 让解留在当前分支——把"大位移"变成"小位移"的关键 |

**它负责**：单点位姿可行性。**它不产出路径**（路径由 §7 的插值产生）。

**实测**：tick 0、毫秒级，一集调 10–11 次完全免费；残差门限 0.02 m / 0.15 rad（task_0 实测有
0.019–0.021 m 的临界样例）。

**它解决不了的**

| 问题 | 原因 |
|---|---|
| 路径会不会撞 | 没有世界模型 |
| 有没有路可走 | 只回答单点，不回答连通性 |
| 起点是否已在障碍里 | 无此概念 |
| **解出来但走不到** | IK 残差小 ≠ 控制器能跟随（task_9：侧抓族 6/6 伺服超时，残余 0.45–0.87 rad） |

最后一条最重要：**"IK 有解"与"能执行"是两件事，换 IK 后端救不了它。**

---

## 5. Contact-GraspNet —— 学习式抓取位姿

**接口**：

```python
grasps = generate_grasps(camera_name, mask, backend="contact-graspnet", max_candidates=5)  # → GraspSet
best   = select_grasp(grasps, strategy="top_down")   # 或 "highest_score"；→ GraspCandidate | None
```

| 参数 | 含义 |
|---|---|
| `camera_name` + `mask` | 用哪个视角、哪块像素作为输入（掩码质量直接决定候选质量） |
| `backend` | 当前只有 `contact-graspnet` |
| `max_candidates` | 最多返回几个候选（默认 5；运行记录里也是保留 5 个） |
| `strategy` | `top_down`（优先垂直向下）或 `highest_score` |

**适用**：物体形状不规则、抓取轴不显然；需要在多候选里择优（遮挡、贴边）；想省掉手写几何。

**代价与实测**：12 GB 卡上 **4 个并发调用直接 OOM**，级联使 SAM3 返回 500、两个 sweep 作废——用它的
sweep 必须 `--workers 2`；而且它只给候选，**选择策略要自己定**。

**task_9 未用的原因**：瓶子是规则圆柱，位姿可由"点云中位数 + 45% 高度 + 坐标轴构造的手腕朝向"得到，
且**闭爪读数（0.371 = 该高度瓶宽）能当场校验**。引入 graspnet 只增加不确定性与显存风险。

---

## 6. cuRobo —— 碰撞感知规划与双臂

**接口**

```python
MotionStrategy(ik_solver="pyroki", trajectory_planner="curobo")   # PyRoki 解 IK，cuRobo 规划路径
MotionStrategy(pose_planner="curobo-integrated")                  # cuRobo 连位姿一起解
solve_ik(pose, backend="curobo")                                   # 带碰撞判定的独立 IK
```

**参数与行为**

| 项 | 说明 |
|---|---|
| `trajectory_planner="curobo"` | 保留 PyRoki 解 IK，只让它规划到该 IK 结果的路径 |
| `pose_planner="curobo-integrated"` | 位姿与路径联合求解，可拒绝"能到但会撞"的目标 |
| 碰撞检查 | **默认开启**；`CAP_HARNESS_CUROBO_DISABLE_COLLISION` 关掉后返回的路径会被记为 `blind plan`，下游不再复核 |
| 失败返回 | 422，body 里点名约束：`start_state_feasible`、`goal_ik_found`、各约束代价——是诊断，不只是报错 |
| 双臂 | integrated 14-DoF 联合规划 |

**它解决 PyRoki 解决不了的问题**：路径是否撞、位姿可解但有无干净接近路径、起点是否已在障碍里、双臂协同。

**它解决不了的（实测）**

- **"解出来但走不到"**：task_9 的侧抓姿态族在两后端都能解，却每档都卡住；换 cuRobo 只是"更贵地规划到
  一个走不到的目标"。
- **边缘位姿更爱拒绝**：task_0 实测 `curobo-integrated` 在 4/5 个种子上拒绝 pyroki 勉强能解的站位。
- **与 PyRoki 种子混用有分支陷阱**：task_0 实测 pyroki 的 goal IK 与 cuRobo 路径终点落在不同 wrist 分支，
  j7 差 **2.018 rad**，随后关节伺服永远收敛不了。

**规矩**：先用 `solve_ik` 探可行性，再决定后端；自由空间 transit 交给 cuRobo，接触段保留插值（§7）。

---

## 7. 插值详解：中间那一段路是怎么来的

### 7.1 三种"插值"，三个调用

| 调用 | 谁决定中间路径 | 参数 | 能否避障 |
|---|---|---|---|
| `move_to_pose(pose, tolerance=…, max_steps=…)` | harness：**关节空间直线**生成 waypoint，按固定频率回放 | `max_steps`（waypoint 上限 = tick 上限，v00 用 150）、`tolerance`（0.04） | ❌ |
| `move_to_joints(q, tolerance=…, max_steps=…)` | **控制器**：每 tick 重复发同一目标，闭环收敛 | `tolerance`（**弧度**，常用 0.02–0.03）、`max_steps`（200） | ❌ |
| `execute_trajectory(traj, stop_on_termination=True)` | **调用方**给的 waypoint 序列，纯回放 | 轨迹本身 | ❌（除非轨迹已避开） |

`move_to_pose` 的内部展开在 trace 里能直接看到：

```
move-to-pose
 ├── plan-motion          ← 解 IK + 在关节空间插值成 trajectory
 │    └── pyroki-solve-ik
 └── execute-trajectory   ← 按固定频率逐个 waypoint 执行，每个 waypoint 花 tick
```

v00 的记录里正是 9 次 `move_to_pose` / 9 次 `plan_motion` / 9 次 `pyroki-solve-ik` / 5 次
`execute_trajectory`。

### 7.2 关节空间直线插值具体怎么算

```python
q0 = current_joints()             # 起点 7 个关节角
q1 = solve_ik(target_pose).joint_positions   # PyRoki 解出的终点关节角
N  = 150                          # waypoint 数 = tick 预算
for k in range(N + 1):
    s = k / N
    q = [(1 - s) * a + s * b for a, b in zip(q0, q1)]
    step(joint_command(q))        # 每个 tick 发一个中间点
```

关键点：**插的是关节，不是末端位置**。末端实际轨迹是 `FK(q(s))`——由运动学决定的曲线，**不是空间直线**。

### 7.3 为什么这条"直线"会变成弧线

- **运动学非线性**：关节按比例变，末端不按比例走。task_9 的 v01 从 home
  `[-0.00, -0.16, -0.00, -2.44, 0.00, 2.22, 0.78]` 到一个远分支 IK 解
  `[-0.32, 0.84, -0.12, -1.97, 1.21, 1.36, -0.45]`，关节空间是直线，末端却甩出大弧——关键帧里手臂
  扫过架子，130–150 tick 的 waypoint 预算在弧线中途耗尽，`move_to_pose` 全部超时。
- **奇异位形附近**：末端速度 `ẋ = J(q)·q̇`，J 在奇异点附近条件数暴涨，**关节匀速会变成末端剧烈摆动**。
- **中间段无检查**：只对端点做 IK，中间是否穿过桌面/抽屉/目标物体，没有任何人判断。

### 7.4 两个把误差直接送进物理的参数

1. **`tolerance`（弧度）不是米。** 0.03 rad 在 0.5 m 半径处 ≈ **1.5 cm** 的末端误差。task_9 的抓取阶段
   就是这样：伺服"成功"停在比目标高 1.5–2 cm 的地方，闭爪在空气里合上（读数 0.049，而非抓住时的 0.15/0.37）。
   **这是"程序全绿但什么也没抓到"的典型成因。**
2. **`max_steps` 就是 tick 预算。** 三个 200 tick 的伺服 = 600 tick，占满 1000 的一多半；用尽后 episode
   被截断，后续调用报 `adapter_failed: … executing action in terminated episode`。

### 7.5 task_9 用过的三种纠正手段

| 手段 | 做法 | 效果 |
|---|---|---|
| **种子化 IK** | `seed_joints=current_joints()`，让解留在当前分支 | 把大位移切成小位移，弧线变短（v05 approach 216 tick 收敛） |
| **分段短移** | 长距离拆成 2 cm 一步 | 实测：一次 11 cm 横移把瓶子甩掉；5×2 cm 则完全跟随（高度稳定 0.217 m） |
| **伺服重发** | 同一目标反复 `move_to_joints` 直到 `ok` | v03 的 flip 三次收敛；抓取仍卡住——说明那是**姿态族**问题，不是插值问题 |

### 7.6 结论

**插值只保证两个端点，不保证中间。** 它便宜（0 规划延迟）、可预测（步数 = tick 数），代价是把路径风险
留给程序：要么用短段/高位路线把它约束住，要么显式换成碰撞感知规划来管中间怎么走。

---

## 8. 深度：像素→米，以及该不该再加一个深度估计模型

### 8.1 当前的深度是什么

`get_observation().cameras["agentview"]` 提供 `depth_m`（512×800，米，**相机轴向 z**，与 RGB 逐像素对齐）、
`intrinsics`、`camera_pose`。它是**仿真渲染出来的真值**，不是传感器测量：

- task_9 取值范围 0.63–3.07 m；瓶身表面 0.93–1.03 m
- 反投影后桌面拟合 **z = −0.0111 ± 5 mm**（跨大范围），瓶底 0.006、瓶顶 0.138 —— 一个立着的瓶子 + 一张平的桌子

### 8.2 换算公式

```
p_cam  = ( (u−cx)/fx · d ,  (v−cy)/fy · d ,  d )      ← 内参 K（task_9: fx=fy=618.039, cx=400, cy=256）
p_base = R(q_cam) · p_cam + t_cam                     ← 外参（task_9: t=(1.3186, 0, 0.6984)，光轴 ≈(−0.778,0,−0.628)）
```

算例（瓶脖顶）：像素 `(400,235)`、`d=0.9318` → `p_cam=(0, −0.0317, 0.9318)` → `p_base=(0.5738, 0, 0.1376)`。

两个坑：`mask.nonzero()` 给的是 `(row, col)` 而不是 `(u, v)`；`p_base = R @ p_cam + t` 是**左乘**，不是 `R.T`。

### 8.3 深度用在哪（六处，task_9 全部用到）

1. **掩码 × 深度 → 世界点云**（主路径：1 593 个点）
2. **剔背景**：那 2 个像素深度 1.14/1.19 m，而瓶身 0.93–1.03 m —— 深度是"这像素是不是物体"的第二个证据
3. **Foremost-Depth Shell**：只保留最前面 2 cm，去除掩码向后出血
4. **高度图 / 平面拟合**：架面 `z = 0.130 − 0.606·y`（残差 σ 5 mm）→ "运输要抬到 0.37 以上"
5. **几何自检**：桌面是常数平面，验证内参/外参约定没错
6. **状态判定**：抓住没有（点云高度跨度）、有没有触停（下降只差 z 方向 5.2 cm）

### 8.4 加一个深度估计模型有没有用

**在仿真里：没有用，甚至是负收益。**

- 当前 depth 是**真值**，没有噪声可以被"提高"；单目深度模型是**用估计替换真值**，且天然有尺度歧义
  （相对深度要靠额外信息锚定尺度），在反光/透明/黑色表面上退化最严重——而酒瓶正是黑色玻璃。
- 更关键：**task_9 的 10.63 cm 偏差不是深度错。** 那 2 个像素的深度（1.14 / 1.19 m）是**对的**，它们确实打在
  18 cm 后的桌面上。错在①掩码把它们算成瓶子②用世界 AABB 的中点聚合。**换一个更准的深度模型对这两件事
  都无能为力**——真正的修复是一次 `quantile`（0 tick）。

**在真机上：有价值，但作为补充通道。**

| 场景 | 判断 |
|---|---|
| 黑亮/透明物体（酒瓶、玻璃杯、液面） | ✅ 结构光/ToF 会丢点或跳变，单目先验能补 |
| 深度有空洞/噪声 | ✅ 用于补洞/去噪，但**必须用传感器深度锚定尺度**，否则不能用于米级目标 |
| 作为一致性校验 | ⚠️ 仿真里用噪声校验真值没意义；真机上两个独立来源互检才有价值 |

**代价**：12 GB 卡上 SAM3 已占 3.85 GB、cuRobo 674 MB、graspnet 228 MB；再加一个深度模型要下调
`--workers`，单卡吞吐明显下降，并引入新的失败面（尺度漂移、幻觉几何）。

### 8.5 更便宜、更可靠的替代路径

| 手段 | 成本 | 作用 |
|---|---|---|
| **深度一致性过滤**（丢掉与掩码内深度中位数差过大的像素） | 0 tick | 本次只要去掉那 2 个点，误差就从 10.63 cm 回到 ~0 |
| **第二视角交叉校验** | 已有 `robot0_eye_in_hand`（fx=333.6，位置/朝向都不同） | 真正的第二测量，能同时发现"掩码错"和"深度跳变" |
| **分位数聚合** | 0 tick | task_9 唯一真正的修复 |

**结论**：先把零成本的统计手段用尽（分位数、深度过滤、双视角），再考虑引入新的感知模型；在仿真环境里
新增深度模型是把真值换成估计，不会更准。

---

## 9. 决策表

| 症状 / 需求 | 用什么 | 备注 |
|---|---|---|
| 需要知道"东西在哪" | SAM3 + 点云分位数 | 0 tick；注意掩码透洞与背景 |
| 只是要单点位姿的关节角 | PyRoki `solve_ik` | 0 tick，可带种子控分支 |
| 物体不规则 / 抓取轴不显然 / 要多候选 | Contact-GraspNet | `--workers 2`；还要自己写选择策略 |
| 程序 `ok=True` 但场景被推动、路径擦到东西 | cuRobo `trajectory_planner="curobo"` | 先用 `solve_ik` 探可行性 |
| 目标位姿本身要被"场景是否允许"约束 | cuRobo `pose_planner="curobo-integrated"` | 边缘位姿可能被直接拒绝 |
| 双臂要同时动 | cuRobo 14-DoF | |
| 接触（最后几厘米、按压、推） | 插值 / 关节伺服 | 碰撞规划会拒绝你想要的接触 |
| "位姿可解但手臂走不动" | **换姿态族或换抓取方式** | 换后端无效 |
| 深度看起来不对 | 先查掩码与聚合方式，再考虑换深度来源 | 见 §8.4 |

---

## 10. 成本与并发规则（12 GB 卡）

| 规则 | 依据 |
|---|---|
| SAM3 常驻 3.85 GB，是最大占用者，启动时先起它 | 实测 |
| 程序调用 `generate_grasps` 时 sweep 用 `--workers 2`，其余 `--workers 4` | 4 并发 graspnet 曾 OOM 并级联使 SAM3 返回 500 |
| 感知与 `solve_ik` 是 0 tick，可放心多调；`move_to_*`/夹爪/`step` 才花 tick | 实测 |
| provider 健康检查 200 不代表能推理（OOM 后 SAM3 会 500 而 `/openapi.json` 仍 200） | 实测；失败爆发时查服务日志里的 `torch.OutOfMemoryError` |

---

## 11. 未在本 campaign 验证的点

- 一个 tick 内部 MuJoCo 跑多少物理子步（程序看不到，文档也未声明）。
- `plan_motion` 是否也是 0 tick（只有 `solve_ik` 与感知调用经过实测确认）。
- cuRobo 的 `blind` 模式相对"高位包络"是否更优——没有 A/B 测过。
- 单目深度模型在本仿真场景的绝对误差——没有测（也就无法证明它会更好）。

---

## 12. 出处

- 接口签名与成本：`docs/api-reference.md`。
- cuRobo 能力与约束：`docs/optional-backends.md`。
- 运动策略与碰撞的告诫：worker/verifier prompt 模板（"Motion planning is not collision-aware unless you
  ask for it"）。
- 实测数字：`task_9/debug/attempts/v05/*/trace/calls/`（调用次数与入参）、`episode/steps.jsonl`（tick 与
  关节）、`episode/reset.json`（相机内参/外参）、`task_9/evidence_task9_localization_audit.md`（AABB 偏差）。
- 深度与内参：`task_9/debug/explore/coordinator_probe_envelope.py`、`heightmap.py` 及其 run 产物。
- task_0 的 cuRobo 实测：`task_0/validation/` 与 `refuted_proposals.md` §task_0。
