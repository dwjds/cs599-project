# MiniAgent Architecture

MiniAgent 是一个本地任务型 Agent Runtime。它的核心不是单次聊天，而是把多渠道消息、文件上下文、长期记忆、Skill、工具调用、运行校验、trace 和评测统一到一条可观察的执行链路里。

## System Overview

```mermaid
flowchart TD
    User["User / QQ / CLI"] --> Channels["Channel Adapters<br/>CLIChannel / QQChannel"]
    Channels --> Bus["MessageBus<br/>InboundMessage / OutboundMessage"]
    Bus --> App["MiniAgentApp<br/>handle_inbound"]

    App --> Session["SessionManager<br/>workspace/sessions/*.jsonl"]
    App --> Attachments["AttachmentStore<br/>workspace/inbox<br/>workspace/outbox"]
    App --> Intent["TurnIntent<br/>file grounding / output artifact / script need"]
    App --> Memory["MemoryStore<br/>memory_store.jsonl<br/>embedding retrieval"]
    App --> Skills["SkillLoader / SkillRouter<br/>workspace/skills/*/SKILL.md"]

    Intent --> RuntimeContext["Runtime Context<br/>prompt + memory + skill note + attachments"]
    Memory --> RuntimeContext
    Skills --> RuntimeContext
    Attachments --> RuntimeContext

    RuntimeContext --> ActionPlanner["Skill Action Planner<br/>actions.json"]
    ActionPlanner -->|matched deterministic action| Tools["ToolRegistry<br/>local tools + run_skill_script"]
    ActionPlanner -->|no action match| AgentLoop["Agent Loop<br/>ReAct tool loop"]

    AgentLoop --> LLM["LLM<br/>OpenAI-compatible API"]
    LLM -->|tool_calls| Tools
    Tools -->|tool_result| AgentLoop
    LLM -->|final answer| Verifier["RuntimeVerifier<br/>evidence / artifact / script checks"]

    Verifier -->|ok| Reply["Final Reply"]
    Verifier -->|violation| Recovery["RuntimeRecovery<br/>forced tools / retry / trusted error"]
    Recovery --> AgentLoop

    Tools --> Artifacts["Output Artifacts<br/>workspace/outbox"]
    Reply --> Bus
    Bus --> Channels

    App --> Trace["TraceSink<br/>runtime_trace.jsonl"]
    AgentLoop --> Trace
    Tools --> Trace
    Verifier --> Trace
    Recovery --> Trace

    Harness["MiniAgentHarness<br/>live / eval / replay / regression"] --> App
    Harness --> Eval["Benchmark Suites<br/>tasks.json / memory_retrieval_tasks.json"]
    Eval --> Trace
```

## Runtime Turn Flow

```mermaid
sequenceDiagram
    participant C as Channel
    participant B as MessageBus
    participant A as MiniAgentApp
    participant I as TurnIntent
    participant M as Memory/Skills/Attachments
    participant P as Action Planner
    participant L as Agent Loop
    participant T as Tools
    participant V as Verifier
    participant R as Recovery
    participant S as TraceSink

    C->>B: InboundMessage
    B->>A: handle_inbound
    A->>I: infer_turn_intent
    I-->>S: turn_intent
    A->>M: load session, memory, visible files, skill notes
    A->>P: try actions.json deterministic action

    alt action matched
        P->>T: execute planned tool
        T-->>S: tool_call / tool_result / output_artifact
        T-->>A: real output path or failure
    else normal ReAct loop
        A->>L: messages + tools + runtime gates
        loop until final answer or max_iterations
            L->>S: llm_request
            L->>L: call LLM
            L->>S: llm_response
            alt LLM returns tool_calls
                L->>T: execute tool
                T-->>S: tool_call / tool_result
                T-->>L: observation
            else LLM returns final text
                L->>V: verify_final_reply
                alt verified
                    V-->>L: ok
                else violation
                    V-->>S: grounding/output/script/completion violation
                    V->>R: plan recovery
                    R-->>S: recovery_plan
                    R-->>L: retry with forced tool or fail safely
                end
            end
        end
    end

    A->>S: file_created / turn_completed
    A->>B: OutboundMessage
    B->>C: reply user
```

## Data Flow

```mermaid
flowchart TD
    UserInput["Input Data<br/>QQ text / CLI text / uploaded files<br/>user_id / session_id / recent history"]

    UserInput --> Inbound["InboundMessage<br/>normalized channel payload"]
    Inbound --> SessionHistory["Session History<br/>workspace/sessions/*.jsonl"]
    Inbound --> Intent["TurnIntent<br/>file grounding / output file / script need"]
    Inbound --> VisibleFiles["Visible Attachments<br/>workspace/inbox/*"]

    SessionHistory --> RuntimeContext["Runtime Context<br/>messages + relevant memory + skill notes + visible files"]
    Intent --> RuntimeContext
    VisibleFiles --> RuntimeContext
    MemoryResult["Memory Retrieval Result<br/>retrieved facts / preferences / workflow memory"] --> RuntimeContext
    SelectedSkills["Selected Skills<br/>matched SKILL.md + actions.json plan"] --> RuntimeContext
    ToolDefs["Tool Definitions<br/>ToolRegistry schemas / run_skill_script"] --> RuntimeContext

    RuntimeContext --> LLMRequest["LLM Messages<br/>system + history + user + tool schemas"]
    RuntimeContext --> DeterministicAction["Deterministic Action<br/>planned script/tool execution"]

    LLMRequest --> ToolCall["Execution Data<br/>tool_call / script arguments / tool_choice"]
    DeterministicAction --> ToolCall
    ToolCall --> ToolResult["Tool Result<br/>stdout / stderr / return code / extracted text"]
    ToolResult --> Evidence["Runtime Evidence<br/>file evidence / output artifact / file_created"]
    ToolResult --> Violations["Runtime Control Data<br/>violation / recovery_plan / retry state"]

    Evidence --> FinalReply["Output Data<br/>QQ reply / CLI reply / saved outbox file / trusted error"]
    Violations --> FinalReply
    FinalReply --> Outbox["Output Files<br/>workspace/outbox/*"]

    Inbound --> Trace["Trace / Reports<br/>runtime_trace.jsonl / replay / benchmark report"]
    Intent --> Trace
    MemoryResult --> Trace
    SelectedSkills --> Trace
    ToolCall --> Trace
    ToolResult --> Trace
    Evidence --> Trace
    Violations --> Trace
    FinalReply --> Trace
```

## Key Design Points

- **Channel 解耦**：CLI 和 QQBot 都转换成统一消息对象，核心 runtime 不绑定具体渠道。
- **工具协议层**：Agent loop 只依赖 `ToolRegistry.get_definitions()` 和 `ToolRegistry.execute()`，后续可以接 MCP-compatible adapter。
- **Skill 不等于执行**：`skill_activation` 只表示选中了 skill，真正执行必须看 `run_skill_script` 的 `tool_result`。
- **Verifier/Recovery 是控制面**：文件证据、输出产物、脚本执行和中间态回复都由 runtime 校验，不只靠模型自觉。
- **Harness 是工程入口**：live、eval、isolated workspace、replay、regression 复用同一套 assembly。
- **Trace 是事实来源**：调试时优先看 `turn_intent -> llm_request -> llm_response -> tool_call/tool_result -> violation/recovery_plan -> turn_completed`。
