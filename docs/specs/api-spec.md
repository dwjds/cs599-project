# API Spec: MiniAgent Runtime Contracts

本文定义 MiniAgent 内部 Python 接口、消息协议、工具协议和 trace 事件协议。它们共同构成 SDD 中可实现、可测试的接口规格。

## 1. Message Contract

### `InboundMessage`

```python
InboundMessage(
    channel: str,
    sender_id: str,
    chat_id: str,
    content: str,
    attachments: list[Attachment],
    metadata: dict[str, Any],
)
```

约束：

- `session_key = f"{channel}:{chat_id}"`。
- Channel 必须在发布消息前完成附件下载与落盘。
- 空文本且无附件的消息不得进入 Agent Runtime。

### `OutboundMessage`

```python
OutboundMessage(
    channel: str,
    chat_id: str,
    content: str,
    attachments: list[Attachment],
    metadata: dict[str, Any],
)
```

约束：

- `channel` 必须对应已注册 Channel。
- `chat_id` 必须能够定位原会话。

## 2. Turn Intent Contract

```python
infer_turn_intent(
    user_text: str,
    attachments: Iterable[Attachment] | None,
    has_visible_attachments: bool,
    script_skill_names: Iterable[str] | None,
) -> TurnIntent
```

`TurnIntent`：

```yaml
operation: answer | answer_from_file | create_output | modify_file | transform_file | list_outputs
requires_file_grounding: boolean
requires_output_file: boolean
requires_script: boolean
target_format: string
source_format: string
is_outbox_listing: boolean
confidence: number
reasons: string[]
```

## 3. Tool Contract

所有工具实现：

```python
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]

    async def execute(self, **kwargs) -> str:
        ...
```

工具通过 `to_schema()` 转换为 OpenAI-compatible function schema。

执行入口：

```python
await ToolRegistry.execute(name: str, params: dict[str, Any]) -> str
```

通用结果约束：

- 成功结果必须返回可供模型理解的真实信息。
- 失败结果必须以 `Error:` 开头，或由 runtime failure classifier 明确识别。
- 文件输出工具必须返回真实输出路径。

### `run_skill_script`

```yaml
name: run_skill_script
input:
  skill_name: string
  script_path: string
  arguments: string[]
  timeout_seconds: integer
success:
  result_contains: "Return code: 0"
failure_types:
  - skill_script_not_found
  - skill_script_nonzero
  - timeout
security:
  - script_path 必须为相对路径
  - 脚本必须位于对应 Skill 的 scripts 目录
  - 禁止路径逃逸
```

## 4. Verification Contract

```python
verify_final_reply(
    latest_user: str,
    reply: str,
    state: RuntimeVerificationState,
    grounding_detector: GroundingDetector | None,
) -> VerificationResult
```

校验顺序：

1. 必需脚本是否成功。
2. 必需文件证据是否存在。
3. 必需输出产物是否存在。
4. 回复是否声称不存在的产物。
5. 回复是否声称读取了未读取文件。
6. 操作型请求是否存在无工具支持的完成声明。
7. 工具执行后是否只返回进度信息。

`VerificationResult`：

```yaml
ok: boolean
violation_type: string
trace_kind: string
finish_reason: string
recovery_kind: string
payload: object
```

## 5. Recovery Contract

```python
RuntimeRecoveryController(max_retries=2).plan(
    verification: VerificationResult
) -> RecoveryPlan
```

`RecoveryPlan`：

```yaml
action: pass | retry | fail
finish_reason: string
error_message: string
system_message: string
recovery_kind: string
forced_tools: string[]
retry_count: integer
max_retries: integer
```

恢复策略：

| `recovery_kind` | Forced Tools |
| --- | --- |
| `force_skill_script` | `run_skill_script` |
| `force_file_grounding` | `read_uploaded_file`, `list_uploaded_files`, `run_skill_script` |
| `force_output_file` | `save_outbox_file`, `run_skill_script`, `write_file` |
| `correct_output_claim` | 无固定工具，要求真实保存或承认未保存 |
| `force_real_tool_or_disclaim` | 无固定工具，必须真实执行或声明未执行 |
| `force_completion` | 无固定工具，继续完成或返回真实失败 |

## 6. Harness Contract

### `RuntimeContext`

```yaml
run_id: string
mode: live | agent_eval
project_workspace: path
state_workspace: path
session_key: string
isolated: boolean
```

### `AgentRuntimeSession.run_turn`

```python
await run_turn(
    prompt: str,
    attachments: list[Attachment] | None,
    max_iterations: int = 10,
) -> AgentTurnResult
```

`AgentTurnResult`：

```yaml
reply: string
metrics: object
tool_events: ToolEvent[]
outbox_files: string[]
new_outbox_files: string[]
loop_error: string
```

## 7. Trace Event Contract

每行 JSONL 至少包含：

```yaml
timestamp: ISO-8601 string
kind: string
run_id: string
session_key: string
mode: string
```

关键事件：

| Event | 必要字段 |
| --- | --- |
| `turn_intent` | operation、requires_*、source/target format |
| `llm_request` | iteration、model、forced_tool |
| `llm_response` | iteration、content、tool_calls |
| `tool_call` | iteration、tool、params |
| `tool_result` | tool、failed、failure_type、result_preview |
| `evidence_collected` | evidence_type、source_tool |
| `output_artifact` | source_tool、result_preview |
| `recovery_plan` | violation_type、action、forced_tools、retry_count |
| `file_created` | path |
| `turn_completed` | finish_reason、reply_preview、tool_calls |
| `judge_result` | task_id、success、failure_types |

## 8. Benchmark Assertion Contract

端到端任务可声明：

```yaml
expected_reply_contains: string[]
expected_reply_contains_any: string[]
expected_tools_all: string[]
expected_tools_any: string[]
expected_outbox_suffixes: string[]
expected_outbox_files: object[]
expected_max_tool_calls: integer
```

测试通过必须同时满足所有已声明断言，且不存在阻断性工具失败。

