# MiniAgent 测试与评估

## 1. 评估目标

MiniAgent 的评估重点不是只判断自然语言回答是否流畅，而是判断 Agent 是否真实完成任务：

- 是否调用了必要工具。
- 是否获得了文件内容证据。
- 是否生成了真实输出产物。
- 产物内容是否满足要求。
- 工具失败时是否能够恢复或可信失败。
- 长期记忆是否能检索到相关事实。
- Trace 是否能够重建执行过程。

## 2. 测试体系

### 2.1 端到端 Agent Benchmark

任务文件：`workspace/benchmarks/tasks.json`

覆盖：

- 普通问答与回复风格。
- 文本、PDF、DOCX、XLSX 附件读取。
- Markdown、Word、PDF、Excel 产物生成。
- Excel 筛选、公式、导出 PDF。
- Weather Skill。
- 代码导航。
- outbox 工具。

断言类型：

- 回复文本包含断言。
- 必须调用或任选调用工具断言。
- 产物后缀断言。
- 产物文本、sheet 和行数断言。
- 最大工具调用次数。

### 2.2 Memory Retrieval Benchmark

任务文件：`workspace/benchmarks/memory_retrieval_tasks.json`

指标：

- Recall@1 / Recall@3 / Recall@4 / Recall@5。
- MRR。
- 每条 query 的命中 rank。

### 2.3 Replay 与 Regression

- Replay：基于 trace 重建 `llm_request -> llm_response -> tool_call -> tool_result` 时间线，检查缺失和乱序。
- Regression：比较两次 benchmark 的任务状态、成功率、工具调用和失败类型变化。

## 3. 当前基线结果

该基线来自 `20260510_194443`，用于如实记录当前系统能力和失败类型。最新文档与 Skill 配置整理完成后，最终提交前仍需重新运行完整 20 项 benchmark。

### 3.1 Agent Benchmark

来源：`workspace/benchmarks/results/latest.md`

| 指标 | 结果 |
| --- | ---: |
| 总任务数 | 20 |
| 通过 | 16 |
| 失败 | 4 |
| 成功率 | 80.00% |
| 工具调用总数 | 27 |
| 平均工具调用数 | 1.35 |
| 平均步骤数 | 2.4 |

失败类型：

| 类型 | 数量 | 分析 |
| --- | ---: | --- |
| `missing_expected_reply` | 1 | 测试要求固定英文词 `trace`，模型使用了中文同义表达；后续新增 `expected_reply_contains_any` |
| `missing_expected_tool` | 1 | PDF 正确通过 Skill 脚本生成，但测试仍只期待旧工具；属于测试规格与实现不一致 |
| `outbox_missing_text` | 2 | 一个来自 Excel 筛选列配置与回退结果错误，一个来自 PDF 中文字体文本提取问题 |

这组结果保留了真实失败，而不是只展示成功案例。它说明 Harness 能将失败定位到回复、工具或产物内容层。

### 3.2 Memory Retrieval

来源：`workspace/benchmarks/results/memory_retrieval_latest.md`

| 指标 | 结果 |
| --- | ---: |
| Cases | 20 |
| Hits | 19 |
| Recall@1 | 65.00% |
| Recall@3 | 85.00% |
| Recall@4 | 95.00% |
| Recall@5 | 95.00% |
| MRR | 0.7667 |

### 3.3 Replay

来源：`workspace/benchmarks/results/replay_latest.md`

- Replay status：passed。
- Replayed iterations：11。
- Replayed tool calls：5。
- Replay 发现了 3 条 `file_grounding_without_tool` 诊断，证明 trace 能识别旧版本中的文件依据缺失问题。

## 4. 科学性与可信度

- 每个 eval task 使用独立 session key。
- `--isolated` 可将任务状态写入独立临时 workspace，避免相互污染。
- 文件任务不只看最终回复，还打开产物进行内容断言。
- 失败类型保存在 JSON 报告，便于回归比较。
- 任务间默认延迟 3 秒，降低 API 限流对结果的干扰。

## 5. 后续实验

- 重新运行完整 benchmark，验证已修正的 PDF 中文字体和测试断言。
- 增加 Verifier 开启/关闭消融实验，对比假行为率。
- 增加 Action Planner 开启/关闭实验，对比平均步骤数与成功率。
- 增加不同模型上的工具调用稳定性比较。
- 增加故障注入任务，例如脚本不存在、工具超时、输出目录不可写。

## 6. 运行命令

```powershell
python miniagent.py harness eval --tasks workspace/benchmarks/tasks.json --isolated --delay 3
python miniagent.py harness memory
python miniagent.py harness replay --source workspace/traces/runtime_trace.jsonl
python miniagent.py harness compare --base <old.json> --head <new.json>
```

## 7. 课程整理后的健康检查

```text
Python py_compile: passed
Harness eval --limit 0 smoke: passed
Skill Doctor: OK=122, WARN=2, ERROR=0
```

两个 Warning 来自 DOCX/XLSX Office validator 共用基类中的 stub-like marker，不影响当前核心工作流，但最终演示前应确认不会调用未实现分支。
