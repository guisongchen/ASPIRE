# 概念与术语（Concepts and Glossary）

这份文档把两类词汇分开写清楚：一类描述**"怎么组织、怎么跑一次工作"**，另一类描述**"怎么控制机器人"**。两套词汇重叠得很厉害，实际使用中最常出错的四处是：

- `step` 有两个意思（单位 vs 一次 API 调用）；
- `campaign` 经常被误当成"测试套件"，其实套件是 `tasks.json`；
- `task` 既指 benchmark 的工作单元，也常被用来指"派给某个 agent 的活儿"；
- "证据"经常被误当成"文档里写得够多"。

**范围：** CaP harness 与驱动它的 iterative-debugging pipeline。凡属**本仓库**固定性质的，文中直接写清；凡属**campaign 设定**（种子区间、GPU 策略）的，文中明说是设定——这些会随每次 campaign 变化，权威来源是该 campaign 的 `campaign.json` 与 handoff。

**语言约定：** 本文按操作者要求用中文书写，**专业术语保留英文**（缩写或全称），与代码、运行产物字段、以及 `docs/` 其余英文文档保持一致，便于逐字对照。

---

## 1. 对象模型

```
campaign                     一次 iterative-debugging pipeline 的执行，覆盖一个 task list
└── RUN_ROOT = outputs/runs/<stamp>/
    ├── campaign.json        dev / unseen / held-out 种子分配、heldout_count
    ├── progress.md          每个 task 一行：状态、速率、skill 状态
    ├── curriculum.md        下一个做哪个 task、为什么
    ├── refuted_proposals.md 不得提升的提案，附驳回所依据的数字
    ├── prompts/             每个 agent 被派工时的原文
    ├── verification/        每个提了技能的 task 一个目录
    └── <benchmark>/<suite>/task_<id>/            ← 一个 task
        ├── code/v00…v07_<slug>.py   version 序列，一次修复一个 failure mode
        ├── fix_code.py              选定的程序（Stage 2 只读这个路径）
        ├── findings.md              什么坏了、什么修好了
        ├── skill_report.json        对 skill library 的评分与提案
        ├── debug/{initial,attempts,explore,holdout,logs,blocked}
        └── validation/              Stage 2 held-out，coordinator 专用
```

| 术语 | 定义 | 别和什么混 |
|---|---|---|
| **campaign** | 一次 pipeline 执行：一个 `RUN_ROOT`、一套种子分配、一个共享的 skill library 版本。它既承载**配置**，也承载**过程中演进的记录**。 | 测试套件（那是 `tasks.json`）；manifest（那是单次 validation run 的身份） |
| **task** | 一个 `(benchmark, suite, task_id)` 单元——`tasks.json` 的一行、campaign 下的一个目录。自带 version 序列、自己的 dev / unseen / held-out 测量、自己的 findings。 | "派给 agent 的活儿"、shell 里的 job |
| **task list** | `tasks.json`：一次 campaign 要尝试的 task 集合。**这才是最接近"测试套件"的东西。** | campaign 本身 |
| **version**（`vNN`） | 一个程序文件，针对一个 failure mode 的修复。**不允许就地修改**；每个 version 都要跑完整 dev 种子 sweep。 | git commit；"又一次尝试" |
| **sweep** | 把一个 version 在**全部 dev 种子**上跑一遍：`run-batch --seeds 51 52 …`，结果落在 `debug/attempts/vNN/`。 | 单个 episode |
| **run / episode** | 一个 program 在一个 seed 上的一次运行，产物在一个 run 目录里：`<output-root>/<seed>/<run-id>/`。 | 一次 sweep |
| **seed** | 一个场景。`--init-mode seeded` 下每个整数 seed 是一套**不同摆位**；`saved` 会把种子塌缩到固定的 init-state 列表上。按用途分成 dev / unseen / held-out，见 §3。 | 程序自身 RNG 的随机种子（不存在——程序只能 `import math`） |
| **Stage 1** | worker 在 dev 种子上的调试循环。 | Stage 2 |
| **Stage 2** | coordinator 用 `scripts/run_validation.py` 跑 `fix_code.py` 的 held-out（种子 1–50）。 | unseen check |
| **unseen check** | worker 在 Stage 1 末尾对选定程序做的**一次性**测量（种子 66–80）。只测一次；拿它调试就等于把它变成训练集。 | Stage 2 的 held-out |
| **verification** | 另一个 agent 只用 skill library + 候选提案重新解这个 task，拿不到 proposer 的代码与产物。结论：`verified` / `refuted` / `untested`。 | 把 worker 自己的程序再跑一遍 |
| **fix_code.py** | 选定的程序；Stage 2 精确读这个路径。 | 得分最高的 version 文件（它是其逐字节副本） |
| **skill library** | `.claude/skills/iterative-debugging/skills/`：所有 task 都读的策展库，**也是 task 之间唯一被许可的通道**。有 content hash，记在 `progress.md` 与每个 `skill_report.json` 里。 | worker 的程序；campaign 的文档 |
| **manifest / identity** | `validation/runs/<identity>/manifest.json`：把 program、benchmark、suite、task id、seeds、init mode、camera 参数一起哈希成身份。不同 identity 的结果**永不混用**。 | campaign 的配置 |
| **blocked mode** | 用尽 version 预算仍没修好的 failure mode，记在 `debug/blocked/<mode>.md`。 | 一个失败的 task——task 只是换条路继续 |

---

## 2. tick 与 step

**一个 tick 就是仿真器推进一步控制步，也是 harness 计费的单位。** `step(action)` 文档里写明的成本就是 **one tick**。运行产物中 `StepResult.diagnostics` 记录 `control_period_s: 0.05`，因此一个 tick 就是一个 20 Hz 的控制周期：

> **1000 tick ≈ 50 s 仿真时间**（由 1 / 0.05 s 推出）

harness 对外只暴露控制 tick；一个 tick 内部 MuJoCo 跑多少物理子步，程序看不到，本仓库文档也没有声明——不要假设它等于 1。

### "step" 的两个意思

| 含义 | 出现位置 | 指什么 |
|---|---|---|
| **step = tick**（单位） | `episode_max_steps`、`steps_executed`、`steps_remaining`、`step_index`、`max_steps` | tick 计数 |
| **step = 一次 API 调用**（动作） | 散文里的"下一步执行 `move_to_joints`" | 一次调用 |

**两者不可互换：一次运动调用可以吃掉很多 tick。**

```
step(action)                 -> 恰好 1 tick
move_to_pose / move_to_joints-> 0 … max_steps ticks（实测 5、9、18、41、54、200）
execute_trajectory           -> 很多 tick，按固定频率执行
open_gripper / close_gripper -> 约 8–18 ticks（夹爪本身要动）
perception + IK 调用         -> 0 tick
```

### 预算到底限制了什么

- 预算是**运动预算**，不是"调用次数预算"。在实测的约 0.009 rad/tick 下，1.2 rad 的关节目标要 ~130 tick，所以 1000 tick 只装得下六到八个中等规模的动作。
- **感知与 IK 免费**：`localize_object`、`segment_text`、`mask_to_point_cloud`、`estimate_geometry`、`solve_ik` 都不推进仿真时间。用 `solve_ik` 试位姿不花钱，**移动到那个位姿才花钱**。
- 预算是**按 task 声明**的（`task_metadata.episode_max_steps`），不是全 benchmark 统一；`run_validation.py --max-steps` 默认取任务声明的 horizon，没有声明才回落 1000。
- 成功会**提前结束**（`termination_reason: task_succeeded`），不必用满。预算用尽则**截断** episode，之后的动作调用失败：`adapter_failed: … executing action in terminated episode`——此时程序仍可能报 `program_ok: true`，但最后几个 stage 其实一次都没执行。

---

## 3. seed 分区：dev / unseen / held-out

**三个分区不是三种数据，而是三种被允许的使用方式。** 同一批 1–80 的场景，按「谁能看、能拿它做什么」切开。

| 分区 | 大小 | 谁用 | 允许做什么 | 结果落在 |
|---|---|---|---|---|
| **dev** | 51–65（15） | worker | **调试**：每个 version 都在这里跑全量 sweep，按 failure mode 分组 | `debug/initial/`、`debug/attempts/vNN/` |
| **unseen** | 66–80（15） | worker | **只测一次**：Stage 1 末尾对选定程序做一次泛化检查 | `debug/holdout/` |
| **held-out** | 1–50（50） | 只有 coordinator | **验收**：Stage 2 对 `fix_code.py` 跑 50 集，得到任务的最终速率 | `validation/runs/<identity>/` |

上表是 LIBERO 的 pipeline 默认值；每个 campaign 的实际分配以 `campaign.json` 为准。

### 为什么是三个而不是两个

- **dev = 训练集。** 可以反复看、反复调——「按 failure mode 迭代」就发生在这里。
- **unseen = 便宜的、worker 自己做的防过拟合检查。** 15 个种子约一分钟，目的是在交接之前就发现「只对 51–65 有效」的程序。它的一次性是设计的一部分：一旦看着它调，它立刻变成 dev。
- **held-out = 验收测试集。** 50 集，样本量是 unseen 的三倍多；而且 **worker 与 verifier 都看不到**，所以它是唯一没有被任何决策污染过的数字。

三个分区还有一个结构性作用：**它让职责分离有意义**。worker 拿不到 1–50，不是靠自觉——Stage 2 的命令由 coordinator 执行，产物落在 coordinator 专用的目录里。

### 破坏规则的后果

- 拿 unseen 调试 → 它变成 dev，unseen 数字从此乐观偏置。
- 把 unseen 测第二次 → 同样的问题；第二次看就不可避免地开始「选」。
- 用 held-out 做任何决策（选 version、调参数）→ 最终速率不再是测量，而是拟合。

### 一个一致性证据

task_9 的分区数字：

```
dev 14/15 (93%)   unseen 13/15 (87%)   held-out 48/50 (96%)
```

三者方向一致、量级接近，held-out 甚至略高 → **没有过拟合信号**。反过来的形状才是警报：dev 很高而 held-out 大幅下降（例如 14/15 对 25/50），说明程序在拟合那 15 个场景。

### 为什么 dev 用 15、held-out 用 50

不是随意的，是两个不同目的的折中：

- dev 要**反复跑**（一个 task 最多 8 个 version × 15 种子，每轮约 4 分钟），所以必须小。
- held-out 只跑**一次**，要的是精度：15 次试验的 95% 置信区间约 68%–100%，50 次约 86%–99%。同样 96% 的估计，50 集才有区分度。

### 前提：必须 `--init-mode seeded`

`saved` 会把种子塌缩到同一批 saved init state 上，三个分区退化成同一个场景，分区也就失去意义。

---

## 4. 排课与 curriculum：为什么按这个顺序做 task

10 个 task 彼此**独立**（不同场景、不同程序、不同判定，task 之间禁止互读目录），所以「顺序」是 campaign 的**设计选择**，不是依赖链。这个选择就是 curriculum learning 在这里的具体形态。

### 排序由三个轴共同决定

| 轴 | 在这里怎么体现 |
|---|---|
| **组合深度**（最接近「难度」） | 单子目标先做，双子目标最后（task_3 = 开上层抽屉 + 放碗）。原始理由是「defer the hardest composition until the library is richest」 |
| **库覆盖度** | 库里有成文指导的先做（task_9 / task_2 的酒瓶）；库里缺模式的推后（task_3 缺 pull-open） |
| **家族迁移** | 同族任务成组做、复用同一套技能（1/4/8 三个碗任务）；不同接触模式各占一个位置（5 推、7 旋钮） |

外加一个**非教学目的**的理由：第一个 task 用来端到端验证流水线（最便宜的一次完整跑通），这是 smoke test，不是难度梯度。

### 它是「自适应课程」，不是固定课表

`curriculum.md` 是一轮一轮写的：每轮开头读上一轮结果，再决定剩余顺序；原始排课里还带着「unless findings say otherwise」这个开关。

### 注意：这些轴是「先验」，不是实测难度

task_0 被当作最便宜来打底，实际 **0/50**（卡在抓取接近，而且「库里有 drawer pattern」是协调者的 overclaim，Round 2 已自我更正）；task_9 判「库强」判对，**48/50**。所以排序**不等于**难度曲线——不要因为某个 task 排在前面就认为它简单。

### 顺序带来的偏差，以及 campaign 自己的补偿

早期任务是在**更弱的库**下评测的，成绩天然吃亏。因此 campaign 在结尾做一次**一次性重跑**：低于 80% 的任务用最终版库重跑（`rerun_02/`）。看早期任务的低分时，先查它评测时的库 content hash，再判断要不要当失败看。

### 和 ML 课程学习不完全对应的两点

- 被「学」的不是模型参数，而是 **skill library**；每个 worker 都是从零开始读当前库的新 agent，入库由 coordinator 的提升门决定（只有 `verified` 才进）。
- 没有难度估计器：排序是协调者读完上一轮产物后的判断，粗粒度、可审计（写在 `curriculum.md` 里），但不自动。

---

## 5. 成功，以及它周围的词

| 术语 | 含义 |
|---|---|
| `program_ok` | 程序层面没出错。这是**运行**状态，不是任务成功。 |
| `task_success` | benchmark 自己的判定接受了这一集。**一个 seed 是否通过只看它**——程序崩了但任务已完成，也算通过；不过 `program_ok: false` 仍是需要修的缺陷。 |
| `termination_reason` | episode 为何结束：`task_succeeded`、`program_completed`、`program_error`，或到 horizon 被截断。 |
| rate（速率） | 该分区内的通过数（dev `14/15`、unseen `13/15`、held-out `48/50`）。**速率永远绑定分区**——不要把 dev 速率和 held-out 速率当成同一种测量来比较。 |

种子分区是 **campaign 设定**，不是 harness 常量；三个分区各自的用途、默认值，以及「恢复 campaign 时以 `campaign.json` 为准」的规则见 §3。

### "通过"不等于"做完了":判定可能在接触瞬间触发

LIBERO goal 任务的判定是**物体状态谓词**（例如碗位于盘上方/盘内），它可以在**接触的那一刻**就成立，而 episode 随即结束——夹爪还没张开。task_8 的 48 个成功 episode 全部如此：`place_descend` 返回 `ExecutionStatus.TERMINATED`（35 tick），随后 `open_release` 只走 **1–2 tick**、`retreat` 只走 1 tick 就因 episode 已结束而返回。

由此要区分两句话：

| 说法 | 是否被测量 |
|---|---|
| 「碗被搬到盘上，谓词在接触时成立」 | ✅ 被测量，48/50 |
| 「碗被放下、松开、稳定静止」 | ❌ **没有**测量——松开与退避从未真正执行 |

这不是取巧：判定是 benchmark 的，所有臂用同一判定，所以速率可比。但它意味着**不能从"通过"推断出"释放成功"**，任何关于放置精度（残差 0.7–1.9 cm）的数字都只是**接触瞬间**的量。写结论、写技能条、写博客时必须带着这个限定。

---

## 6. 常混淆对照

| 一对概念 | 关键区别 |
|---|---|
| **campaign vs 测试套件** | 套件是 `tasks.json`（测例清单）；campaign 是**跑这个套件的这一次执行**（配置＋记录＋产物）。而且它比普通套件多一件事：**会反过来改写共享库**。 |
| **campaign vs manifest** | manifest 描述**一个**结果的身份（哪份代码、哪些 seed）；campaign 是容纳许多这类结果的记录本加工作区。 |
| **tick vs step** | 同一个单位；但 "step" 也用来指一次调用，而**调用不是单位**。 |
| **version vs run** | version 是一个程序；run 是这个程序在某个 seed 上的一次运行。 |
| **task 顺序 vs 依赖** | 10 个 task 彼此独立，顺序是 curriculum 的选择（组合深度 + 库覆盖度 + 家族迁移），**不是**「后一个依赖前一个」。task_0 是 0/50，并没有挡住 task_9 的 48/50。见 §4。 |
| **排在前面 vs 简单** | 排序用的轴是协调者的**先验判断**，不是实测难度：task_0 被判为「最便宜」而实际 0/50。见 §4。 |
| **unseen check vs Stage 2** | unseen（66–80）是 worker 的一次性泛化检查；Stage 2（1–50）是 coordinator 对 `fix_code.py` 的验收评测。两者都**不是**调试集。 |
| **evidence vs 成文指导** | 随仓库发布、或上一轮 campaign 遗留在库里的内容，只能算**覆盖度**，不是证据——它没在这台机器上被复现过。证据 = 机制 + 本机可复现的数字，最好再加一次独立复现。因为某类物体"文档最厚"而优先做那个 task，是排课决定，不是"这个技术有效"的论断。 |
| **verification vs 重跑** | verification 问的是**文本能否传递**：一个全新 agent、拿不到 proposer 的程序。把 proposer 自己的程序再跑一遍，对 skill 一无所证。 |
| **blocked mode vs blocked task** | mode 可以 blocked 而 task 继续（换条路）；只有当没有任何可执行的替代方案时，task 才算被 block。 |

---

## 7. 角色，以及为什么要分工

| 角色 | 可以做 | 不可以做 |
|---|---|---|
| **coordinator** | 决定下一个 task、执行**所有** episode、跑 Stage 2、派 agent、决定提升哪些 skill、更新 campaign 文档。单卡 campaign 里，它还是**唯一执行命令的人**（其它角色只写命令）。 | 在 worker 仍然负责的 task 上，**悄悄**用自己的程序替换对方的；诊断探针和后备草稿是允许的，但一旦由 coordinator 接管某个 version，必须在 campaign 记录与该 task 的 `CHANGELOG.md` 写明。 |
| **worker** | 观察场景、写程序、跑 Stage 1 sweep、做一次 unseen check、写 `findings.md` / `skill_report.json` | 跑 held-out 1–50；读同 campaign 里其它 task 的目录；修改共享 skill library |
| **verifier** | 读 skill library + 候选提案、写自己的程序、在 51–55 上跑（两次尝试） | 读 proposer 的代码、findings，或任何 task 目录 |

分工的目的是**归因**，不是层级："这次通过到底是因为修复、因为库、还是因为 coordinator 的提示"必须始终可回答。诊断探针谁跑都行，但**一个 version 应当只有一个作者**。

两条让结果可判读的规则，都是**社会性约束**（不是技术强制）：

- **sibling-task 隔离**——一个 task 不得读另一个 task 的目录。skill library 是唯一跨 task 通道，正是为了让"是库把它带过去的"这句话保持可检验。
- **provenance（来源）**——不读 `.bddl`、不读 asset 文件（`.xml` / `.urdf` / `.obj`）、不碰 `inner.*`、不读 reward 或 success，也不读 `third_party/` 下的 benchmark 实现源码。每个物体选择、grasp pose、尺寸、像素位置都必须来自运行时感知。**读判定器的实现，和读 task 的 goal 文件是同一种越界**：那是关于"如何判成功"的 ground truth，不是感知。

---

## 8. 出处

- 对象模型、stage 规则、version 预算、种子分区、提升门槛：`.claude/skills/iterative-debugging/SKILL.md` 与 worker / verifier 的 prompt 模板。
- run 目录结构、capture 选项、`--max-steps`：[run-artifacts.md](run-artifacts.md)。
- `step` 成本为 one tick、工具清单：[api-reference.md](api-reference.md)。
- tick 长度：任意 run 的 `episode/steps.jsonl` 中 `StepResult.diagnostics.control_period_s`，以及同文件的 `step_index`。
- campaign 设定：`<RUN_ROOT>/campaign.json`；库的 content hash：`<RUN_ROOT>/progress.md` 与各 task 的 `skill_report.json`。
- validation 身份：`validation/runs/<identity>/manifest.json`。
- 排课与 curriculum：`<RUN_ROOT>/curriculum.md`（每轮选了哪个 task、理由是什么）；结尾的 phase-3 重跑规则见 `gen_progress.py --rerun-threshold`（默认 0.8）。
- 后端选型（SAM3 / PyRoki / Contact-GraspNet / cuRobo 各自何时用、参数、代价与失败签名，外加插值语义与"要不要加深度模型"）：[backend-selection.md](backend-selection.md)；安装与启动见 [optional-backends.md](optional-backends.md)。
