# 产物与文档位置索引

> 这份文件回答**"东西在哪"**，不回答"结论是什么"。结论在 `docs/stage-summary-2026-10-21.md`、
> 两份 claim 报告与知识库里。
> 清点日期：**2026-10-24**（数字是当天实测；`outputs/` 不入库，只存在于跑过实验的那台机器）。

## 0. 三句话导航

| 你想做的事 | 去哪 |
|:---|:---|
| 读结论、看三条主张的边界 | `docs/stage-summary-2026-10-21.md` |
| 重算任何一个数字 | `outputs/` 下的 `trace/calls/*`、`program-result.json`、`steps.jsonl` + 对应的 `analyze_*.py` |
| 复用知识（不涉及本项目细节） | 知识库 `aspire_trace_contribution` / `aspire_skill_library_ablation` / `imitation_action_metric_pitfalls` |
| 做 **agent 数据（AD）分析** | 见 §6，那里按"要什么字段"列了入口 |

---

## 1. 文档（`docs/`，全部入库）

| 文件 | 作用 | 什么时候读 |
|:---|:---|:---|
| `stage-summary-2026-10-21.md` | **阶段小结**：三条主张的来龙去脉、三条方法学教训、未测清单 | 第一次接手时先读这份 |
| `ablation-design.md` | 预注册（§1–§4）+ 偏离记录 D-1…D-12（§5） | 要看某个结论当时是怎么定的、为什么改 |
| `evidence-manifest-2026-10-21.md` | 证据清单：938 MB / 207 264 个证据文件的合并 sha256；修剪记录 | 怀疑"原始数据还在不在"时 |
| `api-reference.md` | 生成程序能用的全部原语、返回值、限制 | 写/读任何程序前 |
| `run-artifacts.md` | 一次 run 的目录结构（trace / media / episode / source） | 第一次读 run 目录时 |
| `glossary.md` | 术语（tick、version、seed 分区、verifier…） | 术语不确定时 |
| `backend-selection.md` / `optional-backends.md` / `libero-pro.md` | 环境与后端配置 | 重建环境时 |
| `system2.md` / `yam-real.md` / `behavior.md` 等 | 真机与其他 benchmark | 与仿真无关，本轮未用 |

两份 claim 报告在 `outputs/ablation/results/`（不入库）：

| 文件 | 内容 |
|:---|:---|
| `claim1_report.md` | 主张 1：pilot 数字、T0 的 8 版轨迹、两条待处理记录 |
| `claim2_report.md` | 主张 2：三条件阶梯、两任务 2×2、条件性成立的论证 |
| `claim3_report.md` | 主张 3：为什么"必要条件为负"是**指标错**（三个测量） |

---

## 2. 实验产物总览（`outputs/`，**不入库**）

### 2.1 规模（2026-10-24）

| 计数 | 值 |
|:---|:---|
| run 目录（每个含 `run.json` + `episode/steps.jsonl`） | **1 764** |
| trace 调用记录（`trace/calls/*/input.json`） | **171 994** |
| `outcome.json` / `program-result.json` | 各 2 037 |
| 视频 `*.mp4` | 3 418（2.5 GB） |
| 深度 `*.npy` | 3 327（5.2 GB） |
| 掩码渲染 `*.png`（**只剩 overlays**） | 8 601（2.5 GB） |
| 总占用 | `outputs/runs` 6.7 GB + `outputs/ablation` 7.7 GB |

### 2.2 主张 1（trace）— `outputs/ablation/claim1/`，5.7 GB

| 路径 | 内容 | 大小 |
|:---|:---|:---|
| `T0/` | **r1 的无 trace 臂**：`runroot/.../task_9/{code/v00…v07, debug, fix_code.py, findings.md, skill_report.json, validation/}` | 2.3 GB |
| `T0-r2/` | **r2 的无 trace 臂**（冻结 v00 + 8 个消融 + held-out + `feedback/` 反馈包） | 1.7 GB |
| `T1-r2/` | **r2 的有 trace 臂**（8 版 + held-out） | 1.4 GB |
| `T1/` | 空壳（r1 的 T1 用 campaign 的 task_9，没在这里跑） | 24 KB |
| `verify/` | **库提案转移性验证**：`BRIEF.md`、`code/{A1,A2,B1,B2}.py`、`runs/`、`VERDICT.md`、`results.md`、`NOTES.md` | 503 MB |
| `addendum_{T0,T1,T0-r2,T1-r2}.txt` | 两臂的 dispatch 文案（同轮两臂只差"拿回什么"一节） | 28 KB |
| `replicate2_log.md` | 第二轮**逐次回传的原始记录**（每次请求/执行/回传） | 12 KB |
| `README.md` / `HANDOFF.md` | 第一轮协议、披露 D-C1-1；第二轮的操作记录（已收口） | 20 KB |
| `analyze_claim1.py` | 配对分析（判据写死；**按求解汇总，不拼伪样本**） | 12 KB |

**配对数据**：`outputs/ablation/results/claim1/claim1.json`（r1）、`claim1_replicates.json`（r1 + r2）。
重算：`.venv-libero/bin/python outputs/ablation/claim1/analyze_claim1.py`。

### 2.3 主张 2（技能库）— `outputs/ablation/claim2/`，2.1 GB

- `{C0,C1,C2}/runroot/...`：三个条件的 run root（C1 未执行，见 D-1）。
- `C0/_contaminated_attempt1/`：**被污染的第一轮**，保留作审计（不要拿它算数）。
- 结果：`outputs/ablation/results/{c0_task8,c0_task9,c2_task8,c2_task9,c0_both,c2_both}.json`、
  `calibration_c0_task9.md`、`claim2_report.md`。
- 分析脚本：`outputs/ablation/analyze_claim2.py`。
- campaign 里的三臂副本：`outputs/runs/2026-10-18_11-06-41_goal-swap/ablation/claim2/`（9.1 MB）。

### 2.4 主张 3（agent 数据）— `outputs/ablation/claim3/`，144 KB

| 文件 | 作用 |
|:---|:---|
| `analyze_action_autocorr.py` → `action_structure.json` | 陷阱一：89.4% 的 tick 目标不变、lag-1 = 0.0023 rad |
| `check_input_floor.py` | "只给本体"的理论下限：中位 0.0661 rad |
| `classify_program_args.py` → `program_arg_classes.json` | 静态 taint：72 个程序、392 个 actuator 调用点（**判据已废弃**，见 3b_first_pass） |
| `validate_against_traces.py` | 用 trace 校验静态分类（发现"占比"恒为真） |
| `measure_two_layers.py` → `two_layers.json` | **3b 的正式测量**：结构不变性 + 23–40% 参数叶子随测量变化 |
| `3b_first_pass.md` | 为什么"测量条件占比"这个判据没有分辨力 |
| `gate_repro/`、`gate_prevaction/` | BC 门禁的复现与 `--prev-action` 对照（各含 `summary.json`） |

**训练数据索引**：`outputs/runs/2026-10-18_11-06-41_goal-swap/distill/task_9_heldout/index.jsonl`
（50 集 / 48 成功 / 27 827 tick），配套 `episodes/` 与 `gate_holdout1-10/`。

### 2.5 campaign 本体 — `outputs/runs/2026-10-18_11-06-41_goal-swap/`，6.7 GB

| 路径 | 内容 |
|:---|:---|
| `progress.md` / `curriculum.md` / `refuted_proposals.md` | 状态、任务顺序与决策、被否提案（自动生成 + 人工裁决） |
| `RESUME.md` | campaign 入口（已指向阶段小结；campaign 已收口 5/10） |
| `libero-pro/libero_goal_swap/task_{0,2,5,8,9}/` | 每个任务：`code/vNN_*.py`、`fix_code.py`、`debug/`、`validation/`、`findings.md`、`skill_report.json`、`task_analysis.md` |
| `verification/<task>/` | 独立 verifier 的产物：`proposed_skills.md`、`verification_report.json`、`notes/`（251 MB） |
| `distill/` | 主张 3 的导出索引与 episode 数组（4.6 MB） |
| `prompts/` | 每次 dispatch 生成的 agent 作业书（**每份都可查"当时跟它说了什么"**） |
| `task_9_*.md` / `*.png` | 技术叙事与架构图 |

单个 run 的内部结构见 `docs/run-artifacts.md`。速查：

```
run-<UTC 时间戳>-<id>/
├── outcome.json            # program_ok / task_success / termination_reason
├── source/program-result.json   # 程序自己 return 的 report + stdout/stderr
├── trace/calls/<序号>-<工具>/{input,output}.json   # 每一次原语调用的参数与返回
├── trace/events.jsonl      # span 与时序
├── episode/steps.jsonl     # 逐 tick：动作目标 + 关节状态 + eef + 夹爪
└── media/{keyframes,overlays,videos}
```

---

## 3. 分析脚本一览

| 脚本 | 做什么 | 时长 | 产物 |
|:---|:---|:---|:---|
| `outputs/ablation/claim1/analyze_claim1.py` | 主张 1 的配对分析（bootstrap + 精确 McNemar，按求解汇总） | 秒级 | `claim1_replicates.json` |
| `outputs/ablation/analyze_claim2.py` | 主张 2 的三条件配对分析 | 秒级 | `c0_*.json` / `c2_*.json` |
| `outputs/ablation/claim3/*.py` | 动作结构、输入下限、两层测量、静态分类、trace 校验 | 5–25 s | 同名 `.json` |
| `scripts/export_agent_rollouts.py` | validation run → BC 索引（不复制像素） | 分钟级 | `distill/<tag>/index.jsonl` |
| `scripts/train_bc_gate.py` | BC 门禁（`--prev-action` 打开公平比较） | 23 s CPU | `summary.json` |
| `scripts/sanitize_run.py` | 生成 T0 的**反馈包**（只拷 `outcome.json` 三键 + `program-result.json`，自检无第三文件） | 秒级 | `feedback/vNN/` |
| `.claude/skills/iterative-debugging/scripts/run_validation.py` | held-out 评测（coordinator 跑） | 5–7 min/50 seeds | `validation/runs/<id>/` |
| `.claude/skills/iterative-debugging/scripts/dispatch.py` | 生成 worker/verifier 的作业书 | 秒级 | `prompts/*.txt` |
| `.claude/skills/iterative-debugging/scripts/lint_skills.py` | 库的静态闸门（AST 白名单等） | 秒级 | 退出码 |

---

## 4. 技能库

路径：`.claude/skills/iterative-debugging/skills/`（6 个文件：`grasp.md`、`localize.md`、`transport.md`、
`manipulation.md`、`task-exploration.md`、`README.md`）。

- 收口时版本：**`ae3e50d7bd5b21af`**（`campaign.py --skill-library-sha` 可查）。
- 改库的规矩与两次合并的记录：`docs/ablation-design.md` D-11 / D-12。
- 每条实测数字都带"几个求解、哪批 seed"——引用时连同这句一起抄。

---

## 5. 知识库对应关系（`~/projects/knowledge_database`）

| 主题 | 笔记 |
|:---|:---|
| 项目主页（三条主张的最终状态） | `01_projects/mini_aspire_agentic_data_factory.md` |
| 主张 1（trace 值多少、为什么未复现） | `03_resources/robotics/agentic/aspire_trace_contribution.md` |
| 主张 2（库的消融） | `03_resources/robotics/agentic/aspire_skill_library_ablation.md` |
| 主张 3（数据能否学）与指标口径 | `03_resources/robotics/agentic/imitation_action_metric_pitfalls.md` |
| 方法层（分工/预算/消融/泄漏/重复求解/证据） | `03_resources/robotics/agentic/aspire_agentic_campaign_playbook.md` |
| 证据表（task_0/9 的协议与归因） | `03_resources/robotics/agentic/aspire_campaign_evidence_task0_task9.md` |
| harness 的 trace 契约 | `03_resources/robotics/agentic/aspire_harness_trace_contract.md` |

---

## 6. 做 agent 数据（AD）分析：按"要什么"找入口

| 你要的 | 在哪 | 备注 |
|:---|:---|:---|
| 逐 tick 状态与动作 | `**/episode/steps.jsonl` | 每行：`action.arms.primary.target.$array_inline`（7 维）、`gripper_position`、`robot_state.joint_positions`、`timestamp_s` |
| 每次原语调用的参数与返回 | `**/trace/calls/<序号>-<工具>/{input,output}.json` | 工具名在目录名里；provider span（`pyroki-*`、`sam3-*`）是重复记录，统计时要去掉 |
| 程序自己的报告（含它测到的量） | `**/source/program-result.json` | 各臂的 `report` 结构不同；读之前先看该任务的 `findings.md` |
| 成功/失败与终止原因 | `**/outcome.json` | 只有三键，是**唯一**允许跨条件比较的字段 |
| 已导出、可直接训练的索引 | `distill/task_9_heldout/index.jsonl` | 50 集 / 48 成功 / 27 827 tick；视频按需解码 |
| 动作流统计（自相关/分段常数） | `outputs/ablation/claim3/analyze_action_autocorr.py` | 换数据后先重跑它，89.4% 不是常数 |
| 结构 vs 参数的两层测量 | `outputs/ablation/claim3/measure_two_layers.py` | 需要某个 run 根的 15-seed sweep |
| 跨条件的配对比较 | `outputs/ablation/claim1/analyze_claim1.py` | 只读 `validation/` 下的 held-out 批次 |

**三个容易踩的点**：

1. **不要把 trace 里的 provider span 当成独立调用**（`pyroki-solve-ik` 与 `solve-ik` 是同一次）。
2. **不要让不同 run root 的种子混着算**（求解不配对，种子才配对）。
3. **`steps.jsonl` 的 `action` 是"本 tick 的目标"，不是"本 tick 实际执行的 qpos"**；后者在 `robot_state`。

---

## 7. 已修剪 / 不可用（2026-10-21）

| 项 | 状态 | 影响 |
|:---|:---|:---|
| `media/keyframes/**/*.png` | **已删**（98 672 个 / 33.6 GB） | 静帧不再可查；画面看 `*.mp4`；深度 `agentview-depth.npy` 保留 |
| `media/overlays/*.png` | 保留（8 601 个 / 2.5 GB） | 掩码的唯一视觉记录 |
| `.uv-cache` | 已删 | **没有释放空间**：uv 用硬链接，inode 仍被 venv 引用（`.venv-graspnet` 从 57 MB 变成 8.3 GB 就是这个） |
| `--no-videos` 跑过的探针 run | 画面全无（静帧已删、本来就没视频） | 只有数值证据 |

证据清单与摘要见 `docs/evidence-manifest-2026-10-21.md`。

---

## 8. 命名约定速查

- **run**：`run-<UTC>-<id>`；按 seed 分目录：`debug/initial/0051/run-…`、`attempts/v03/0051/run-…`、`validation/runs/<id>/`。
- **版本**：`code/v00_initial.py`、`v01_<slug>.py`…；选中版复制为 `fix_code.py`（**冻结后不再改**）。
- **task_analysis / findings / skill_report**：每个任务一份，随任务目录。
- **反馈包（仅 T0 条件）**：`feedback/<版本>/<seed>/{outcome.json, program-result.json}`，只此两文件。
- **seed 分区**：dev 51–65（调参）、unseen 66–80（每臂只测一次）、held-out 1–50（只由 coordinator 跑）。
