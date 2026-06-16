# MiniAgent Memory System

Memory 系统用于管理短期会话、历史摘要、长期可检索事实和人类可读视图。

核心原则：

- `workspace/sessions/*.jsonl` 保存短期原始上下文。
- `workspace/memory/memory_store.jsonl` 是长期记忆检索的唯一主库。
- `MEMORY.md` 是由 store 渲染出来的人类可读视图，不是权威事实源。
- 每轮只注入与当前 query 相关的 top_k memory，不注入整份 `MEMORY.md`。
- memory retrieval 事件写入 runtime trace。

## Files

| File | Role | Prompt Injection |
| --- | --- | --- |
| `workspace/sessions/*.jsonl` | 当前 session 的短期原始消息 | 最近消息会进入 prompt |
| `history.jsonl` | consolidation 历史摘要日志 | 不默认注入 |
| `memory_store.jsonl` | 长期记忆主库 | 检索 top_k 后注入 |
| `MEMORY.md` | 人类可读长期记忆视图 | 不整份注入 |
| `HISTORY.md` | 人类可读历史摘要 | 不默认注入 |
| `consolidation_trace.jsonl` | consolidation 调试日志 | 不注入 |

重要约定：

- 如果 `MEMORY.md` 和 `memory_store.jsonl` 不一致，以 `memory_store.jsonl` 为准。
- 手工改 `MEMORY.md` 不会自动影响检索。
- `history.jsonl` 记录“发生过什么”，`memory_store.jsonl` 记录“以后还值得记住什么”。

## Runtime Flow

```text
Inbound message
    |
    v
Load session
    |
    v
Append user message
    |
    v
If session too long:
    consolidate old messages
    write history.jsonl / HISTORY.md
    upsert memory_store.jsonl
    render MEMORY.md
    trim session
    |
    v
Retrieve relevant memory for current query
    |
    v
Inject # Relevant Memory
    |
    v
LLM + tools
    |
    v
Append assistant reply
```

## Session Layer

Session 文件示例：

```json
{"role": "user", "content": "总结这个文件", "timestamp": "...", "attachments": [...]}
{"role": "assistant", "content": "文件摘要如下...", "timestamp": "..."}
```

session key 示例：

| Channel | Session key | File |
| --- | --- | --- |
| CLI | `cli:direct` | `cli__direct.jsonl` |
| QQ private | `qq:private:<openid>` | `qq__private__<openid>.jsonl` |

当前长期 memory 是全局共享，尚未按 QQ 用户隔离。

## Consolidation

相关配置在 `src/miniagent_core/config.py`：

```python
MEMORY_CONSOLIDATE_TRIGGER = 30
MEMORY_KEEP_RECENT = 15
MEMORY_RETRIEVAL_TOP_K = 4
MEMORY_RETRIEVAL_CANDIDATES = 8
```

当 session 消息数超过阈值时：

- 旧消息被压缩成 history summary。
- 稳定、可复用的信息写入 `memory_store.jsonl`。
- 近期消息保留在 session 中，保证对话连续性。

consolidation 期望模型返回 JSON：

```json
{
  "history_summary": "短摘要",
  "history_topic": "主题",
  "history_keywords": ["keyword"],
  "memory_items": [
    {
      "type": "preference",
      "topic": "reply style",
      "summary": "用户偏好简洁中文回复。",
      "keywords": ["简洁", "中文"],
      "tags": ["preference"],
      "confidence": 0.9
    }
  ]
}
```

解析失败、LLM 失败、embedding 失败会写入 `consolidation_trace.jsonl`。

## Memory Item Schema

`memory_store.jsonl` 每条 item 常见字段：

| Field | Meaning |
| --- | --- |
| `id` | 由 `type + topic + summary` 生成的稳定 ID |
| `timestamp` | 首次写入时间 |
| `updated_at` | 最近更新时间 |
| `source` | 来源 session |
| `type` | `profile / preference / project / fact / workflow / tooling` |
| `topic` | 稳定主题 |
| `summary` | 可独立理解的长期事实 |
| `keywords` | 检索关键词 |
| `tags` | 调试/过滤标签 |
| `confidence` | 置信度 |
| `active` | 是否参与检索 |
| `embedding` | 向量表示 |

## What Should Be Remembered

适合长期记忆：

- 用户稳定偏好。
- 用户身份与长期背景。
- 项目环境和工具约束。
- 已验证的长期事实。
- 可复用 workflow。

不适合长期记忆：

- 一次性临时消息。
- 未解决的报错中间态。
- 工具失败但尚未确认的推测。
- 冗长文件原文。
- “刚刚上传了什么”这种附件短期状态。

## Upsert and Conflict

写入使用 upsert：

```text
key = type + topic + summary
```

同 key：

- 刷新 `updated_at`。
- 不重复追加。

同 `type + topic` 但 summary 不同：

- 置信度高或更新时间新的 item 保持 active。
- 旧 item 标记 `active=false`，保留审计痕迹。

## Retrieval

检索流程：

```text
Current query
    |
    v
Generate query embedding
    |
    v
Read active memory items
    |
    v
cosine similarity + lexical overlap
    |
    v
candidate_pool
    |
    v
rerank by semantic score, lexical score, topic bonus, confidence
    |
    v
top_k -> # Relevant Memory
```

如果 embedding 不可用，会退回 lexical rerank。

注入格式：

```markdown
# Relevant Memory
- [preference] (reply style) 用户偏好简洁中文回复。
```

## Observability

Consolidation trace：

```text
workspace/memory/consolidation_trace.jsonl
```

Runtime memory retrieval trace：

```text
workspace/traces/runtime_trace.jsonl
```

查找事件：

```text
kind = memory_retrieval
```

常见字段：

- `query`
- `top_k`
- `candidate_pool`
- `retrieved_ids`
- `hit`

## Debug Guide

### 为什么 MEMORY.md 被覆盖？

这是预期行为。`MEMORY.md` 是 `memory_store.jsonl` 中 active items 的派生视图。

### 为什么问记忆时没看到某条？

可能原因：

- 该 item 没有进入 `memory_store.jsonl`。
- item 被标记为 `active=false`。
- query 与 item 相似度不够。
- item 缺 embedding，走 lexical fallback 时没命中关键词。

先查：

```text
runtime_trace.jsonl -> memory_retrieval
memory_store.jsonl -> active item
```

### 为什么旧附件找不到？

附件可见性不等于 memory。session consolidation 可能裁剪旧附件记录，长期 memory 只保存语义事实，不能恢复工具可访问的附件对象。

未来需要独立 attachment index。

### 手工改 MEMORY.md 为什么无效？

检索读取 `memory_store.jsonl`，不是 `MEMORY.md`。如果要影响检索，应修改 store，并补齐合适的 `topic/summary/keywords/embedding`。

## Current Boundaries

- 长期 memory 尚未按用户隔离。
- 旧 item embedding 缺失时没有自动后台补齐。
- 附件索引尚未完成。
- 冲突识别主要基于 `type + topic`。
- `MEMORY.md` 不支持反向同步到 store。

## Next Improvements

- User-scoped memory store。
- Attachment index。
- Embedding backfill 命令。
- Memory conflict review command。
- 更完整的 retrieval report，展示 score 和 injected memory。
