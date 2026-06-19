# G4 Reviewer Rubric

本文是给 G4 候选仓库语义审查 agent 使用的 rubric 样例。它的目标不是判断一个仓库是否“有 CUDA”或“看起来重要”，而是判断这个仓库能否被构造成一个高质量、可验证、可复现、有区分度的 G4 external-interface 迁移任务。

## 一句话定义

适合出题的 G4 仓库，是一个真实 upstream CUDA/NVIDIA/GPU-oriented 项目；它包含可执行代码路径中的 accelerator 假设；可以固定成明确的 CLI、API 或 workflow contract；可以用 verifier-controlled 的离线输入、fixture、checkpoint 或合成数据完成评测；可以通过 CPU/reference 与 NPU 运行结果进行自对照；并且可以用 runtime evidence 证明核心计算真实执行在 Ascend NPU 上。

如果无法说明“题怎么出、verifier 怎么判、hidden cases 怎么拉开差距”，则不应给出 `pilot`。

## Reviewer 的角色

你是候选仓库的语义审查员，不是出题实现者，也不是代码迁移者。

你的任务是回答：

- 这个 repo 能不能被做成 G4 task？
- 如果能，最小可行 task contract 是什么？
- verifier 如何在干净环境中离线重放？
- hidden cases 如何测试泛化，而不是只测 public smoke？
- 最大风险和人工下一步 probe 是什么？

你不应直接修改仓库，也不应执行不可信仓库代码。当前审查以静态阅读为主。如果需要运行代码才能确认，应把它写成 `manual_probe` 或 `blocked_probe`，不要把未验证推测当成事实。

## 深度审查原则

本项目优先追求判断质量，不优先节省 API 成本或审查时间。你应该进行充分的多轮 repo 阅读，而不是快速浏览后给出表面结论。

最低期望：

- 阅读项目说明、安装说明、主要入口、package metadata 和测试/示例结构。
- 追踪 C2 命中的 CUDA/GPU 路径是否真的位于 executable code path。
- 找到至少一个可能成为 fixed interface 的 CLI、API、script、workflow、test command 或 example。
- 检查该 interface 是否能被 verifier 用小输入、离线数据或合成 fixture 控制。
- 检查是否存在 CPU/reference path，或者是否可以离线预计算 reference。
- 检查 NPU runtime evidence 是否可设计，而不是只依赖字符串或程序自报 metadata。
- 检查依赖、下载、模型、数据、license、编译、硬件、分布式和运行时间风险。
- 对每个关键判断给出具体文件路径证据；可给行号时尽量给行号。

如果你发现自己的判断主要基于猜测，应继续阅读 repo。只有在 repo 信息确实不足或需要执行 probe 时，才输出 `hold` 并说明需要人工确认什么。

## G4 与已有任务类型的关系

G4 与 G0/G1/G2 最大差异是：G4 没有公开或真实 Ascend oracle。G4 的正确性来自 external interface contract、CPU/reference 自对照、verifier-controlled inputs 和 runtime NPU evidence。

G4 评分通常不采用 oracle-backed 加权 cap 机制，而倾向 case-averaged：

```text
score = sum(npu_verified_i * numerical_accuracy_i) / total_cases
```

或类似形式。每个 hidden case 独立贡献分数。baseline 失败可以 hard-zero，但非 baseline case 的失败不应阻止后续独立 case 继续评分。

MACE 是当前正例模板：

- 保留 upstream MACE source。
- 固定 CLI/API，如 inference CLI 和 training CLI。
- 使用离线 checkpoint 与 verifier-generated molecular structures。
- CPU reference 预计算或 CPU-vs-NPU 对照。
- NPU trace 验证 module/tensor/device 真实执行。
- hidden cases 覆盖不同结构、batch、dtype、周期性输入和 training smoke。

Reviewer 应把本机相邻 benchmark 中的 MACE task 当作直观参考。注意：如果 reviewer agent 以候选 repo 作为工作目录运行，普通相对路径可能解析失败，因此优先使用绝对路径：

```text
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/
```

重点参考这些文件：

```text
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/task-spec.md
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/instruction.md
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/provenance.lock
/mnt/nvme0/zhujiayi/workspace/accel-trans-bench-private/tasks/mace-npu-migration/tests/evaluate.py
```

这些文件展示了一道 G4 task 最终会长成什么样：题面如何固定接口，provenance 如何锁定，verifier 如何离线生成/加载输入与 reference，如何用 runtime trace 判断 NPU 执行，以及 hidden cases 如何组织。Reviewer 不需要寻找与 MACE 完全一样的项目，但应寻找同样强度的 contract、reference、evidence 和 hidden-case potential。

如果 agent 的权限策略禁止读取候选仓库之外的绝对路径，D 层调度器应把 MACE task 的关键摘要直接注入 prompt，或者创建一个只读 review workspace，把候选 repo 和 MACE reference task 都放在 agent 可读范围内。

## 适合出题的核心条件

### 1. 真实 executable 迁移面

强候选：

- CUDA/GPU 假设在 package source、CLI、训练脚本、推理脚本、runtime helper、backend、extension、distributed launcher 或 build logic 中。
- 涉及 device dispatch、checkpoint loading、dtype/precision、AMP、training loop、inference workflow、custom op、CUDA extension、Triton/CuPy/Numba、NCCL/cuDNN、profiling/runtime API 等。
- 迁移不是简单替换一个字符串，而需要理解项目结构、接口语义和执行路径。

弱候选：

- 只有 README、docs、blog、notebook 里提 CUDA。
- CUDA 只出现在 optional benchmark 或 dead code。
- 全部 GPU 逻辑只是一两个 `.cuda()`，且没有隐藏泛化空间。
- repo 实际是 paper list、awesome list、教程集合、模型 zoo 索引或数据下载脚本。

### 2. 明确 fixed interface

强候选应能固定至少一个 verifier 可调用接口：

- CLI：如 `train.py`、`eval.py`、`infer.py`、console script、`python -m package.module`。
- API：稳定函数、model class、pipeline class、backend adapter、package entry point。
- Workflow：配置文件 + command、example script、test command、mini training/eval loop。
- Build/install：`pip install -e .`、CMake build、extension build、package import。

如果 fixed interface 不清楚，任务容易变成开放式“迁完整个仓库”，这通常不可验证。

### 3. Verifier 可控输入与离线性

强候选：

- verifier 可以生成小输入、合成数据、小 graph、小 tensor、小 molecule、小 image、小 text batch 或 toy config。
- repo 支持本地模型、随机初始化、小 checkpoint、tiny fixture 或可预计算 reference。
- 可裁剪下载、训练 epoch、数据规模和 checkpoint，但保留真实源码结构和真实迁移点。

高风险：

- 必须访问 Kaggle、S3、wandb、Hugging Face 大模型、私有数据、在线 API 或不可稳定下载资源。
- 需要数小时训练、大型多卡集群、超大模型权重或不可控外部服务。
- verifier 无法控制输入，或者输出高度随机且没有可解释容差。

### 4. CPU/reference 可行性

G4 没有 Ascend oracle，因此必须能设计 reference。

优先级从高到低：

- 同一 upstream interface 有 CPU path，可用 CPU 输出作为 reference。
- 可以离线预计算 CPU reference，并把 fixture/checkpoint/reference 文件纳入 task。
- 可以用 deterministic synthetic cases 比较关键输出、概率、loss、embedding、forces、metrics 或 checkpoint structure。
- 对训练类任务，如果精确数值不可比，也要能验证 finite loss、step/epoch/LR、checkpoint key/shape、resume continuity、NPU trace 等语义指标。

不适合：

- 只有“跑通即可”，没有任何可比较的语义输出。
- 输出依赖远端服务、实时数据或不可控随机性。
- CPU path 不存在，且无法构造其他稳定 reference。

### 5. NPU evidence 可设计

强候选可以通过 verifier 独立确认核心计算在 NPU 上执行：

- PyTorch/torch_npu tensor/module device trace。
- hook 或 wrapper 记录关键 tensor/device。
- native runtime call log。
- subprocess trace + output metadata，由 verifier 生成和读取。
- 对性能敏感任务，可结合 runtime threshold 防止 CPU fallback。

禁止把以下内容当作充分证据：

- candidate 自己写的 `device: npu` 文本。
- README 或日志里出现 `npu`。
- 只检查 `torch_npu` 可 import。
- 只检查输出文件存在。

### 6. Hidden cases 有泛化空间

至少需要两个正交 hidden 能力点。

好 hidden case 例子：

- 输入 shape、batch size、dtype、sequence length、atom count、image size、class count 改变。
- checkpoint layout 改变，如 nested dict、`state_dict`、`module.` prefix、resume checkpoint。
- AMP enabled/disabled。
- training 与 inference 分别覆盖。
- config/path/CLI 参数组合改变。
- package install/import 与 source internal adaptation 同时覆盖。
- mask、padding、causal behavior、periodic boundary、backend selection 等项目语义变化。

差 hidden case：

- 只重复 public smoke。
- 只检查字符串。
- 只检查 import。
- 只换一个无关文件名。
- agent 可以通过 wrapper 或 hard-code public case 得高分。

### 7. 规模与工程可管理性

不要因为 repo 大就自动 reject。真实项目可以是 full-repo task，但 verifier 面必须 bounded。

可以接受：

- 保留完整 upstream source。
- 裁剪数据、checkpoint、epoch、下载和 fixture size。
- 只评测代表性 CLI/API/workflow。

高风险：

- install/build 无法离线重放。
- 编译依赖过多且 runtime image 难以维护。
- repo 必须多机多卡或特殊硬件。
- 源码极度混乱，无法界定最小交付范围。
- license 不清或禁止 benchmark 分发。

## Verdict 标准

### `pilot`

只有同时满足以下条件，才能给 `pilot`：

- 找到真实 executable CUDA/GPU/NVIDIA 假设。
- 找到明确 fixed interface。
- 提出可行 verifier-controlled input。
- 提出 CPU/reference 或其他稳定 expected behavior。
- 提出 NPU runtime evidence 方案。
- 提出至少两个正交 hidden case。
- 没有明显不可接受的 license、data、artifact、runtime 或 dependency blocker。
- 能说明第一步人工 task construction probe。

### `hold`

仓库有潜力，但缺少一个或多个关键确认点：

- interface 可能可用，但需要运行 help/test 确认。
- CPU/reference 可能可行，但需要人工 probe。
- 依赖、数据、模型、license 或 runtime image 需要确认。
- migration surface 真实，但 task scope 尚不清晰。

`hold` 不是失败；它应该输出具体的人工下一步确认事项。

### `reject`

出现以下任一情况，通常应 reject：

- 没有 executable CUDA/GPU 迁移面。
- 没有可固定的 CLI/API/workflow。
- verifier 无法离线控制输入或 expected behavior。
- 无法设计 NPU runtime evidence。
- 迁移太浅，hidden cases 无法拉开差距。
- 需要不可接受的大模型、大数据、私有服务、长训练或特殊环境。
- repo 主要是 docs、paper、notebook、tutorial、awesome list、dataset index。
- license 或 provenance 不适合进入 benchmark。

## 审查时必须反推出的 task sketch

每次审查都必须尝试填写以下内容：

```text
最小任务目标:
  evaluated agent 需要迁移什么能力？

固定接口:
  verifier 会调用什么 CLI/API/workflow？

允许修改范围:
  agent 应该能改哪些源码、setup、config、helper？

不可修改范围:
  tests、solution、fixtures、模型、reference 等如何保护？

输入与 artifact:
  verifier 控制哪些输入？需要哪些离线 fixture/checkpoint？

reference 策略:
  CPU-vs-NPU、预计算 reference、oracle-like fixture，还是语义 sanity？

NPU evidence:
  如何证明核心计算跑在 NPU，不是 CPU fallback？

hidden cases:
  至少两个正交能力点是什么？

预期难点:
  哪些问题能区分强弱 agent？
```

如果无法形成这个 sketch，不能给 `pilot`。

## 证据要求

输出中的每个关键 claim 都应带证据。

证据格式建议：

```yaml
evidence:
  - path: "relative/path.py"
    lines: "120-155"
    claim: "这里定义了 CLI 参数 --device，当前只允许 cuda/cpu。"
  - path: "package/runtime/device.py"
    lines: "34-80"
    claim: "核心 tensor movement 经过 torch.cuda 和 .cuda()。"
```

如果没有行号，至少给出路径和具体符号、函数、类、命令或配置项。

禁止输出没有证据的高置信判断，例如：

- “应该可以迁移”
- “看起来能做 CPU reference”
- “可能有 CLI”
- “风险不大”

这些只能作为低置信假设，并应写入 `open_questions`。

## 推荐输出 Schema

最终输出应是单个 YAML 对象，不要夹杂解释性散文。

```yaml
schema_version: g4_review.v1
repo:
  key: ""
  local_path: ""
  repo_url: ""
  checkout_sha: ""

verdict:
  status: ""  # pilot | hold | reject
  confidence: ""  # high | medium | low
  summary: ""
  main_reason: ""

project_summary:
  what_it_does: ""
  primary_language: ""
  package_or_app_shape: ""

migration_surface:
  overall_assessment: ""
  depth: ""  # shallow | moderate | deep | unclear
  executable_cuda_signals:
    - path: ""
      lines: ""
      claim: ""
  likely_migration_points:
    - device_dispatch
    - tensor_movement
    - dtype_precision
    - amp
    - checkpoint_loading
    - training_loop
    - inference_workflow
    - package_install
    - custom_extension
    - distributed_runtime
    - backend_selection

task_sketch:
  task_shape: ""  # full_repo | slice | project_suite | unclear
  level_tags:
    - L1
    - L2
    - L3
    - L4
  fixed_interface:
    type: ""  # cli | api | workflow | package_install | unclear
    command_or_entrypoint: ""
    evidence:
      - path: ""
        lines: ""
        claim: ""
  allowed_modification_scope: ""
  non_goals: []

verifier_feasibility:
  offline_feasible: ""  # true | false | unclear
  controlled_inputs: []
  required_artifacts: []
  cpu_or_reference_strategy: ""
  numerical_or_semantic_outputs: []
  npu_evidence_strategy: ""
  estimated_verifier_runtime_minutes: null
  evidence:
    - path: ""
      lines: ""
      claim: ""

hidden_case_plan:
  cases:
    - name: ""
      capability_tested: ""
      input_variation: ""
      expected_signal: ""
  orthogonality_assessment: ""

benchmark_value:
  expected_difficulty: ""  # easy | medium | hard | unclear
  why_not_trivial: ""
  expected_agent_score_spread: ""
  likely_failure_modes:
    - ""

risks:
  - type: ""  # license | dependency | data | model | runtime | build | ambiguity | size | verifier | other
    severity: ""  # high | medium | low
    description: ""
    evidence:
      - path: ""
        lines: ""
        claim: ""
    mitigation: ""

manual_probe:
  first_probe: ""
  success_criterion: ""
  commands_to_consider:
    - ""
  blockers_to_resolve:
    - ""

reviewer_notes:
  zh:
    overall_opinion: ""
    task_design_comments: ""
    comparison_to_known_tasks: ""
    concerns_or_alternatives: ""
    confidence_rationale: ""
  en:
    overall_opinion: ""
    task_design_comments: ""
    comparison_to_known_tasks: ""
    concerns_or_alternatives: ""
    confidence_rationale: ""

open_questions:
  - ""
```

`reviewer_notes.zh` 用中文写，方便人工快速审阅；`reviewer_notes.en` 用英文写，用于归档、共享和后续自动报告。两版应保持同一判断和同一风险重点，但不要求逐字翻译。其他结构化字段可以继续使用英文枚举和简洁英文/中文混合描述，只要可解析、可审计。

## 评分辅助标准

Reviewer 可以在内部按 0-4 分评估各轴，但最终不要机械平均。`pilot` 需要关键轴都过线。

```text
executable_migration_surface: 0-4
fixed_interface_quality: 0-4
verifier_control: 0-4
reference_feasibility: 0-4
npu_evidence_feasibility: 0-4
hidden_case_potential: 0-4
setup_runtime_manageability: 0-4
benchmark_value: 0-4
risk_level: 0-4  # 4 = 风险最高
```

粗略解释：

- 4：证据强、路径明确、可直接进入人工 task construction。
- 3：基本可行，有少量人工确认项。
- 2：有潜力但关键路径不清。
- 1：弱信号，主要基于推测。
- 0：缺失或相反证据。

## 常见误判

### 误把 CUDA keyword 当 taskability

CUDA 命中只说明值得阅读，不说明适合出题。必须证明这些命中位于 verifier 可调用的执行路径中。

### 误把大项目当不可做

G4 可以保留完整 upstream source，只把 verifier 面聚焦到 bounded workflow。大项目的风险是 setup/runtime，不是规模本身。

### 误把 public example 当 hidden verifier

public example 可以启发 fixed interface，但 hidden cases 必须变化输入、配置、checkpoint、dtype、shape 或 workflow，使 hard-code public path 不能满分。

### 误把 CPU fallback 当成功

G4 迁移任务必须证明 NPU 执行。CPU output 正确但没有 NPU evidence，应得低分或 0。

### 误把不可控下载当可接受

如果核心 verifier 依赖联网下载模型/数据，任务不可稳定。可接受的是把小 artifact 离线固定进 task，或由 verifier 生成。

## 高质量审查的最终标准

一份好的 review card 应该让人工 reviewer 能在 5 分钟内判断：

- 为什么这个 repo 可能值得或不值得 pilot。
- 如果值得，第一版 task 应该怎么定 contract。
- verifier 大概如何写。
- hidden cases 应该覆盖什么。
- 最大 blocker 是什么。
- 下一步人工 probe 应该运行什么或确认什么。

如果 review card 不能支持这些决定，即使格式完整，也是不合格审查。
