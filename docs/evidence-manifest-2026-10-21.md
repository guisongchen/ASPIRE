# 证据清单（本地产物，2026-10-21 收口时生成）

`outputs/` 在本仓库里是 **gitignored**（`.gitignore:13`）。这意味着**原始 run 目录只存在于这台机器上**：
主张 1 两轮的全部 trace/关键帧、主张 3 的 890 个 episode 的 `steps.jsonl`、库验证的 4 个变体——都只有
一份。本文件的作用是把"盘上有什么"变成**可核对的记录**：即使产物丢了，也知道丢了什么、结论依据的是哪一批。

## 清点

| 区域 | 大小 | 内容 |
|:---|:---|:---|
| `outputs/ablation/claim1/T0/` | 9.8 GB | r1 的 T0 臂（8 版 × 15 dev + 50 held-out） |
| `outputs/ablation/claim1/T0-r2/` | 5.9 GB | r2 的 T0 臂（冻结 v00 + 8 版消融 + 50 held-out） |
| `outputs/ablation/claim1/T1-r2/` | 5.3 GB | r2 的 T1 臂（8 版 + 50 held-out） |
| `outputs/ablation/claim1/verify/` | 小 | 库提案转移性验证的 4 个变体（60 episodes） |
| `outputs/ablation/results/` | 116 KB | 两份报告、配对数据 `claim1.json` / `claim1_replicates.json` |
| `outputs/ablation/claim3/` | 144 KB | 三个分析脚本 + `action_structure.json` + 两份 gate summary |

**可核对的小文件证据**（`.json`/`.jsonl`/`.md`/`.py`，含 `trace/calls/` 的每次调用记录）：

- 文件数 **207 264**
- 字节数 **938 190 977**（938 MB）
- 合并摘要 `sha256` **`868d767ab3487a53ef55187f69966bb5e112034e3f4a32aa4b72f359c65b6f7b`**
  （对每个文件的 sha256 再取一次 sha256，按路径排序）

**规模**：620 个 run 目录（各有 `run.json` 与 `episode/steps.jsonl`）。

**未纳入摘要**：`media/keyframes/`、`media/overlays/`、视频与深度图——它们占绝大部分字节，但**结论不
依赖它们**（关键帧只在两处被读过：主张 1 的 T1 臂用它判掩码，库验证的 A2 用它确认瓶子是躺着的；两处
的结论都有 program-result 里的数值旁证）。

## 怎么复核

```bash
cd /home/ccc/projects/ASPIRE
python3 - <<'PY'
import hashlib, pathlib
roots = [pathlib.Path(p) for p in
         ('outputs/ablation/claim1', 'outputs/ablation/results', 'outputs/ablation/claim3')]
files = sorted({f for r in roots for pat in ('*.json','*.jsonl','*.md','*.py')
                for f in r.rglob(pat) if f.is_file()})
d = hashlib.sha256()
for f in files:
    d.update(hashlib.sha256(f.read_bytes()).digest())
print(len(files), d.hexdigest())
PY
```

## 丢了会怎样

- **结论不会丢**：`docs/`、两份 claim 报告、`docs/ablation-design.md` 的 D-1…D-12、阶段小结、以及知识库
  里的条目都在版本控制里，且各自带数字与 seed 集。
- **可以复核的东西会丢**：主张 1 的逐种子配对、主张 3 的逐 tick 动作、库验证的逐变体 trace。也就是说，
  **别人无法再从原始数据重算这些数**，只能读我们的报告。

所以本文件不是备份，是**交代**：记录收口时盘上有什么、摘要是什么、哪些没有纳入。
