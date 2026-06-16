# Product Spec: MiniAgent

## 1. 产品定义

MiniAgent 是一个面向真实文件任务的可验证 Agent Runtime。它通过多渠道消息接入、工具调用、Skill、长期记忆、运行时校验、失败恢复和 Harness 评测，降低 Agent “声称完成但没有真实执行”的风险。

课程方向：**方向一：Agentic AI 原生开发**。

## 2. 问题定义

普通基于大模型的工具 Agent 存在以下工程问题：

- 根据文件名或历史回复猜测文件内容，没有真实读取证据。
- 声称文件已经保存，但 outbox 中不存在真实产物。
- 命中了 Skill，却没有执行对应脚本。
- 工具失败后继续声称成功，或停留在“正在处理”的中间状态。
- 运行过程缺少统一 trace，失败后难以诊断、回放和回归比较。
- CLI、QQBot 和评测入口使用不同执行路径，行为难以保持一致。

## 3. 目标用户与场景

### 3.1 目标用户

- 需要通过 CLI 或 QQBot 处理本地文件的个人用户。
- 希望研究 Agent Runtime、Tool Use 和 Harness 的开发者。
- 需要评估 Agent 工具调用可靠性的实验人员。

### 3.2 核心场景

- 用户上传 PDF、DOCX、XLSX 或文本文件，并要求基于真实内容回答。
- 用户要求筛选、修改、转换或生成文件，并获得真实 outbox 路径。
- 用户询问天气、代码位置等需要工具或脚本执行的任务。
- 开发者运行 isolated benchmark，检查工具行为、产物和失败类型。
- 开发者基于 trace 执行 replay 和 regression compare。

## 4. 功能需求

### FR-01 多渠道消息接入

系统必须将 CLI 和 QQBot 消息统一转换为 `InboundMessage`，并通过 `MessageBus` 交给同一套 Agent Runtime。

验收标准：

- CLI 与 QQBot 都通过 `MiniAgentApp.handle_inbound()` 处理。
- 回复统一转换为 `OutboundMessage` 并由原 Channel 发回。
- 不同会话使用不同 `session_key`。

### FR-02 文件证据约束

当本轮请求涉及可见附件内容时，系统必须在最终回答前获得真实文件读取或提取证据。

验收标准：

- 成功读取时 trace 中存在 `evidence_collected`。
- 没有证据时 `RuntimeVerifier` 返回 `grounding_violation`。
- 系统执行恢复重试，超过上限后返回可信错误。

### FR-03 输出产物约束

当用户要求保存、导出、转换或修改文件时，系统必须产生真实 outbox 产物后才能声称完成。

验收标准：

- 成功产物对应 `output_artifact` 或 `file_created` 事件。
- 没有产物时触发 `output_violation`。
- 最终回复中的路径必须来自真实工具结果。

### FR-04 Skill 与脚本执行

系统必须按请求和附件路由项目级 Skill，并通过统一 `run_skill_script` 工具执行受控脚本。

验收标准：

- `skill_activation` 只表示 Skill 被选中，不等于执行成功。
- 脚本路径必须位于对应 Skill 的 `scripts/` 下。
- 需要脚本但没有成功执行时触发 `script_tool_violation`。
- `Return code: 0` 才视为脚本成功。

### FR-05 确定性 Action

对于输入、输出和操作明确的单步任务，系统应优先使用 `actions.json` 生成确定性执行计划，减少模型自由规划的不稳定性。

验收标准：

- 匹配 Action 时 trace 中存在 `skill_action_plan`。
- Action 使用真实工具执行，并验证输出产物。

### FR-06 长期记忆

系统必须支持会话持久化、历史压缩、长期记忆存储和相关记忆检索。

验收标准：

- 原始会话保存在 `workspace/sessions/`。
- 长期事实保存在 `memory_store.jsonl`。
- 每轮只注入相关 top-k 记忆。
- trace 中记录 `memory_retrieval`。

### FR-07 可观测性与评测

系统必须支持统一 runtime trace、端到端任务评测、memory retrieval 评测、trace replay 和 regression compare。

验收标准：

- live 与 eval 使用相同 Agent loop。
- eval 可选择 isolated state workspace。
- benchmark 输出成功率、工具调用数、平均步骤和失败类型。
- replay 检查 trace 顺序与调用匹配关系。

## 5. 非功能需求

### NFR-01 安全性

- API Key、QQ Bot Secret 等敏感信息只能通过环境变量配置。
- Skill 脚本禁止路径逃逸。
- inbox 原文件不得被直接覆盖。

### NFR-02 可审计性

- 关键 LLM、工具、Skill、Memory、Violation 和产物事件必须写入 JSONL trace。
- Trace 写入失败不得中断主任务。

### NFR-03 可扩展性

- 新 Channel 通过实现 `BaseChannel` 接入。
- 新工具通过实现 `Tool` 并注册到 `ToolRegistry` 接入。
- 新 Skill 通过 `workspace/skills/<name>/SKILL.md` 接入。

### NFR-04 可测试性

- eval task 必须支持回复、工具、产物后缀和产物内容断言。
- isolated eval 不得污染 live inbox、outbox、session、memory 和 trace。

## 6. 范围边界

当前版本不承诺：

- 系统级安全沙箱。
- 自动执行真实 LLM/工具的 replay。
- 完整的多 Agent 协作编排。
- 多用户长期记忆隔离。
- 历史附件的独立持久索引。

## 7. 产品验收指标

- 端到端 Agent benchmark：当前基线 20 项通过 16 项，成功率 80%。
- Memory retrieval benchmark：20 项命中 19 项，Recall@4 95%，MRR 0.7667。
- Trace replay：能够重建调用时间线并发现无文件证据声明。
- 所有敏感配置从环境变量读取。

