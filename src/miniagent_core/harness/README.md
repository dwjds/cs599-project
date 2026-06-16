# MiniAgent Harness

`miniagent_core.harness` 是 MiniAgent 的项目级运行控制层。它统一真实运行、评测运行、隔离 workspace、runtime trace、deterministic replay 和 regression compare。
它负责接收任务、准备上下文、选择 skill、调用模型、执行工具、记录轨迹、处理错误、评测结果。

一句话：Harness 让 Agent 可调试、可审计、可复现、可比较。

## What It Owns

Harness 负责：

- 创建 `RuntimeContext`。
- 统一装配 `MiniAgentApp` 依赖。
- 注入 `TraceSink`。
- 启动 live CLI / QQ runtime。
- 为 benchmark 创建可控 `AgentRuntimeSession`。
- 支持 isolated eval workspace。
- 生成 eval、memory、replay、regression 报告。

Harness 不负责：

- 替代模型理解任务。
- 替代 `SkillRuntime` 的脚本安全策略。
- 提供系统级 sandbox。
- 重新执行 replay 中的真实 LLM 或真实工具。

## Module Layout

```text
src/miniagent_core/harness/
├── assembly.py          # AgentAssembly / RuntimeComponents
├── config.py            # HarnessConfig
├── context.py           # RuntimeContext
├── regression.py        # compare_reports()
├── replay.py            # deterministic replay from trace
├── runtime.py           # MiniAgentHarness / CLI parser
├── runtime_session.py   # AgentRuntimeSession.run_turn()
├── trace.py             # TraceSink and trace helpers
└── README.md
```

## Core Objects

| Object | Role |
| --- | --- |
| `HarnessConfig` | workspace、model、results_dir、tmp_dir、isolated 等配置 |
| `RuntimeContext` | run_id、mode、session_key、project_workspace、state_workspace |
| `AgentAssembly` | 创建 app、tools、skills、memory、sessions、attachments、trace |
| `RuntimeComponents` | 一次装配完成后的依赖集合 |
| `MiniAgentHarness` | live/eval/memory/replay/compare 高层入口 |
| `AgentRuntimeSession` | eval 场景的可控单轮 runtime |
| `TraceSink` | JSONL runtime event writer |

## Runtime Flow

```text
HarnessConfig
    |
    v
RuntimeContext
    |
    v
AgentAssembly.build_components()
    |
    v
MiniAgentApp / AgentRuntimeSession
    |
    v
TraceSink -> runtime_trace.jsonl
```

`MiniAgentApp` 仍可以直接创建，但推荐通过 Harness 入口运行，这样 live、eval、replay 和 regression 都使用同一套装配模型。

运行时的“做没做成”不只靠模型自觉判断：

```text
TurnIntent
    |
    v
Agent loop executes LLM/tools
    |
    v
RuntimeVerifier checks evidence/artifacts/script success
    |
    v
RuntimeRecovery retries with forced tools or returns a trusted error
```

这让文件证据、输出产物、脚本执行和进度中间态都有统一校验点。

## Workspace Model

Harness 区分两个 workspace：

| Field | Meaning |
| --- | --- |
| `project_workspace` | 读取项目能力，例如 `skills/`、`AGENTS.md`、`SOUL.md`、`USER.md` |
| `state_workspace` | 写入运行状态，例如 inbox、outbox、sessions、memory、traces |

Live runtime：

```text
project_workspace = workspace
state_workspace   = workspace
```

Isolated eval：

```text
project_workspace = workspace
state_workspace   = workspace/benchmarks/tmp/<run_id>/<task_id>
```

`--isolated` 隔离的是运行状态，不是系统级安全沙箱。skill 脚本和工具如果拿到真实绝对路径，仍可能访问真实文件。

## Commands

完整命令见根目录 [COMMANDS.md](../../COMMANDS.md)。

Live runtime：

```powershell
python miniagent.py harness run --channels cli
python miniagent.py harness run --channels qq
python miniagent.py --channels cli
python miniagent.py --channels qq
```

Evaluation：

```powershell
python miniagent.py harness eval --limit 3 --isolated
python miniagent.py harness eval --limit 20 --delay 5 --isolated
python miniagent.py harness eval --tasks workspace/benchmarks/harness_flow_tasks.json --isolated
python miniagent.py harness memory
```

Replay / regression：

```powershell
python miniagent.py harness replay --source workspace/traces/runtime_trace.jsonl
python miniagent.py harness compare --base old.json --head new.json
```

## Runtime Trace

Trace 默认写入：

```text
<state_workspace>/traces/runtime_trace.jsonl
```

常见事件：

| Event | Meaning |
| --- | --- |
| `llm_request` | 一次模型调用前的摘要，包括 model、message_count、tool_count、forced_tool |
| `llm_response` | 模型回复和 `tool_calls` |
| `recovery_plan` | verifier 失败后的恢复计划，例如 forced tools、retry count |
| `tool_call` | 工具名、参数、参数解析错误 |
| `tool_result` | 工具结果、失败状态、失败类型 |
| `memory_retrieval` | query、top_k、candidate_pool、retrieved_ids |
| `skill_activation` | 命中的 skill、score、reason |
| `skill_action_plan` | `actions.json` 生成的确定性 action plan |
| `file_grounding_preload` | runtime 预读附件内容 |
| `evidence_collected` | 本轮已获得文件内容证据 |
| `output_artifact` | 本轮产生真实输出文件或产物 |
| `file_created` | outbox 中新增文件 |
| `grounding_violation` | 文件内容请求缺少读取证据 |
| `output_violation` | 声称保存文件但没有真实产物 |
| `script_tool_violation` | 需要脚本但没有成功执行 `run_skill_script` |
| `turn_intent` | 本轮结构化意图，包括是否需要文件证据、输出产物、脚本 |
| `turn_completed` | 本轮最终结果 |
| `judge_result` | benchmark judge 结果 |

Trace 写入是 best-effort，trace 失败不会中断主流程。

## Runtime Gates

Harness 通过 `TraceSink` 观察运行过程，真正的门禁逻辑由 `src/miniagent_core/app.py` 调用 `TurnIntent`、`RuntimeVerifier` 和 `RuntimeRecovery` 执行。

当前 gate：

- **File grounding**：涉及附件内容时，必须有文件读取或提取证据。
- **Output artifact**：要求保存/导出/修改文件时，必须有真实输出产物。
- **Script execution**：命中脚本型 skill 且任务需要脚本时，设置 `tool_choice=run_skill_script`，没有成功执行则不允许最终回答。
- **Claim verification**：自然语言里声称文件路径但没有工具产物，会触发 `output_violation`。
- **Incomplete progress**：工具调用后仍只回复“正在处理/下一步将”，会触发恢复，不作为最终答复。

这些 gate 让 replay 和诊断能回答：模型到底做了，还是只是说了。

## Deterministic Replay

Replay 不重新调用 LLM，也不重新执行工具。它读取 trace，重建执行时间线并检查一致性：

```text
llm_request -> llm_response -> tool_call -> tool_result -> turn_completed
```

Replay 会检查：

- `llm_request` 是否有对应 `llm_response`。
- `llm_response.tool_calls` 是否对应真实 `tool_call`。
- `tool_call` 是否有对应 `tool_result`。
- 是否缺少 `turn_completed`。
- 是否存在 violation。

Replay 适合失败复盘、生成调试报告、沉淀 regression 样本。

## Evaluation

Agent task evaluation 由 `src/miniagent_core/benchmark.py` 负责 task loading、judge 和 report，但执行路径走 Harness runtime：

```text
run_benchmark()
    |
    v
MiniAgentHarness.build_eval_session()
    |
    v
AgentRuntimeSession.run_turn()
```

支持的 task judge：

- `expected_reply_contains`
- `expected_reply_contains_any`
- `expected_tools_all`
- `expected_tools_any`
- `expected_outbox_suffixes`
- `expected_outbox_files`
- `expected_max_tool_calls`

Memory retrieval evaluation 会构建临时 memory workspace，统计 Recall@K 和 MRR。

## Reports

默认输出：

```text
workspace/benchmarks/results/
```

常见文件：

| File | Meaning |
| --- | --- |
| `latest.md` | 最近一次 agent eval |
| `<run_id>.json` | agent eval JSON |
| `memory_retrieval_latest.md` | 最近一次 memory eval |
| `memory_retrieval_<run_id>.json` | memory eval JSON |
| `replay_latest.md` | 最近一次 replay |
| `replay_<run_id>.json` | replay JSON |
| `regression_latest.md` | 最近一次 compare |
| `regression_<run_id>.json` | compare JSON |

## Current Boundaries

- isolated eval 不是系统级 sandbox。
- Replay 是 trace replay，不是重新执行。
- 多 skill workflow 尚未结构化编排。
- Trace schema 尚未版本化。
- `benchmark.py` 尚未完全拆到 `harness/suites/`。

## Debug Checklist

遇到“模型又胡说/没读文件/没保存文件”时，按这个顺序看 trace：

```text
1. skill_activation: 是否命中正确 skill
2. llm_request: forced_tool / requires_output_file / requires_file_grounding 是否正确
3. llm_response: tool_calls 是否为空
4. tool_call/tool_result: 工具是否真实成功
5. output_artifact/file_created: 是否有真实产物
6. *_violation: 哪个 gate 拦住了
7. turn_completed: 最终返回了什么
```
