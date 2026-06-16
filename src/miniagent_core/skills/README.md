# MiniAgent Skill System

Skill 系统把通用 Agent 扩展成项目级任务 Agent。它负责扫描 `workspace/skills/*`，路由用户请求，按需注入 `SKILL.md`，用 `actions.json` 生成确定性 action，并通过统一工具 `run_skill_script` 执行脚本。

核心原则：

- `skill_activation` 只表示“skill 被选中”，不表示脚本已经执行。
- 脚本只通过 `run_skill_script` 执行，不把每个脚本注册成独立工具。
- `script_path` 是闭集，必须来自对应 `SKILL.md` 的 Allowed Scripts，不能让模型猜路径。
- 明确的单步任务优先由 `actions.json` 自动执行。
- 需要脚本但模型不调用时，Agent loop 会设置 `tool_choice=run_skill_script` 并用 `script_tool_violation` 拦截空回复。
- 所有脚本执行写入 `workspace/skills/skill_trace.jsonl`。

## Module Layout

```text
src/miniagent_core/skills/
├── actions.py       # 读取 actions.json，生成 SkillActionPlan
├── doctor.py        # skill health check
├── loader.py        # SkillLoader facade
├── policy.py        # runtime policy note
├── registry.py      # SkillRegistry
├── router.py        # rule / LLM / hybrid router
├── runtime.py       # SkillRuntime and SkillTraceLogger
├── scanner.py       # scan workspace/skills
└── README.md
```

Workspace skills：

```text
workspace/skills/
├── code_navigation/
├── docx/
├── pdf/
├── weather/
├── xlsx/
└── skill_trace.jsonl
```

## Runtime Flow

```text
User message + visible attachments
    |
    v
SkillRouter selects skills
    |
    v
SkillLoader injects matched SKILL.md
    |
    v
Action planner checks actions.json
    |
    +--> if deterministic action matches: run tool directly and verify output
    |
    v
LLM receives tools + skill instructions
    |
    +--> run_skill_script(...)
    +--> save_outbox_file(...)
    +--> read_uploaded_file(...)
    |
    v
Agent loop verifies evidence / output / script success
```

## Skill Package Contract

Minimum:

```text
workspace/skills/<skill_name>/
└── SKILL.md
```

Recommended:

```text
workspace/skills/<skill_name>/
├── SKILL.md
├── actions.json       # optional runtime action contract
├── reference.md       # optional, loaded only when needed
├── forms.md           # optional
└── scripts/           # optional Python scripts
```

`SKILL.md` front matter:

```markdown
---
name: xlsx
description: "Use this skill for spreadsheet files."
triggers:
- .xlsx
- excel
- 公式
---
```

## actions.json

`actions.json` 是 runtime 读取的 action contract，不是给模型阅读的普通说明。它适合声明确定、可复用、输入输出清晰的动作。

当前已实现：

| Skill | Action | Script |
| --- | --- | --- |
| `xlsx` | `export_pdf` | `scripts/convert_to_pdf.py` |
| `pdf` | `merge_pdfs` | `scripts/pdf_ops.py` |
| `pdf` | `extract_pages` | `scripts/pdf_ops.py` |
| `pdf` | `rotate_pages` | `scripts/pdf_ops.py` |
| `docx` | `accept_tracked_changes` | `scripts/accept_changes.py` |

action planner 支持：

- 单文件输入：按完整文件名、stem、片段和扩展名选择可见附件。
- 多文件输入：例如合并全部可见 PDF。
- 输出路径：自动写入当前 session outbox。
- 简单变量：例如页码、旋转角度。
- 参数模板：`{input_path}`、`{input_paths}`、`{output_path}`、`{input_stem}`、`{pages}`、`{angle}`。

适合 action 的任务：

- 输入文件能确定。
- 输出文件能确定。
- 操作是通用模板，不依赖一次性业务文件名。

不适合 action 的任务：

- 需要复杂业务理解。
- 需要和用户确认目标。
- 多 skill 多步骤 workflow。
- 高风险改写。

Action 没有覆盖的复杂任务仍交给模型规划，但 runtime 会用 verifier 检查：如果需要脚本却没有成功执行、需要产物却没有真实文件，就不会直接放行最终回复。

## Script Gate

模型可以判断是否调用脚本，但工程上不能只依赖模型自觉。当前 Agent loop 有脚本门禁：

```text
skill selected
    |
    v
runtime 判断本轮需要脚本
    |
    v
LLM request 带 tool_choice=run_skill_script
    |
    v
如果 tool_calls=[]
    |
    v
记录 script_tool_violation，追加 system retry
```

成功条件：

```text
run_skill_script returns "Return code: 0"
```

如果重试后仍未成功执行脚本，runtime 不会放行模型的自然语言承诺。

## Skill Runtime

统一工具参数：

```json
{
  "skill_name": "xlsx",
  "script_path": "scripts/recalc.py",
  "arguments": ["output.xlsx"],
  "timeout_seconds": 60
}
```

`SkillRuntime` 校验：

- `skill_name` 必须存在。
- `script_path` 必须是相对路径。
- 脚本必须在该 skill 的 `scripts/` 下。
- 禁止路径逃逸。
- 只执行 `.py`。
- timeout 限制在安全范围内。

如果返回 `Skill script not found`，模型不应继续换一堆猜测路径；应回到 `SKILL.md` 的 Allowed Scripts，或明确当前 skill 没有合适脚本。

结果格式：

```text
Skill script: xlsx/scripts/recalc.py
Return code: 0
STDOUT:
{...}
```

## Trace

Skill trace：

```text
workspace/skills/skill_trace.jsonl
```

事件：

| kind | status | Meaning |
| --- | --- | --- |
| `skill_activation` | `selected` | router 命中 skill |
| `skill_script` | `started` | 开始执行脚本 |
| `skill_script` | `success` | return code 为 0 |
| `skill_script` | `error` | return code 非 0 或异常 |
| `skill_script` | `timeout` | 执行超时 |

完整 runtime 诊断看：

```text
workspace/traces/runtime_trace.jsonl
```

尤其看 `skill_action_plan`、`tool_call`、`tool_result`、`script_tool_violation`、`output_violation`。

## Built-In Workspace Skills

### weather

用途：天气查询。

核心脚本：

```text
workspace/skills/weather/scripts/query_weather.py
```

天气类请求即使没有附件，也应通过 `run_skill_script` 查询，不应直接编造天气。

### xlsx

用途：

- Excel 读取、摘要、筛选。
- 新增/修改行列。
- 公式、汇总、重算。
- 导出 PDF。

核心脚本：

```text
scripts/edit_workbook.py
scripts/filter_workbook.py
scripts/recalc.py
scripts/convert_to_pdf.py
```

公式任务交付前应运行 `scripts/recalc.py`。如果返回 `errors_found`，不能声称文件完全成功。

### pdf

用途：

- 文本提取。
- 表格提取。
- PDF 合并、抽页、旋转。
- 简单 PDF 报告。

核心脚本：

```text
scripts/extract_text.py
scripts/extract_tables.py
scripts/pdf_ops.py
scripts/create_report.py
```

### docx

用途：

- Word 读取、生成、修订处理。
- Office XML unpack/pack/validate。

核心脚本：

```text
scripts/accept_changes.py
scripts/comment.py
scripts/office/unpack.py
scripts/office/pack.py
scripts/office/validate.py
```

简单 `.docx` 生成可由 `save_outbox_file` 完成，但不会产生 `docx skill_script` trace。

### code_navigation

用途：代码路径、项目文件、配置定位辅助。

## Health Check

```powershell
python miniagent.py skills doctor
```

Doctor 会检查：

- skill 目录和 `SKILL.md`。
- front matter。
- 脚本引用是否存在。
- Python 脚本语法。
- 常用依赖是否可导入。
- `soffice` / `pandoc` 等外部命令是否可用。

退出码：

- `0`：无 ERROR。
- `1`：存在 ERROR。

## Debug Guide

### 只看到 skill selected，没有 script success

说明命中了 skill，但没有执行脚本。继续看 `runtime_trace.jsonl`：

- `llm_request.forced_tool` 是否为 `run_skill_script`。
- `llm_response.tool_calls` 是否为空。
- 是否有 `script_tool_violation`。
- 是否被 `actions.json` 直接执行为 `skill_action_plan`。

### 模型说保存了，但找不到文件

看 runtime trace 是否有：

- `output_artifact`
- `file_created`
- `tool_result` 且工具成功

没有这些事件，就是自然语言声明，不是真实产物。

### 脚本失败

看 `tool_result.result` 或 `skill_trace.jsonl`：

- `Return code`
- `STDOUT`
- `STDERR`
- `timeout`

模型应基于真实错误修正参数或停止说明，不应继续声称成功。

## Current Boundaries

- `actions.json` 只覆盖单步明确动作。
- 多 skill workflow 还没有结构化 planner。
- 附件独立索引还未完成。
- `save_outbox_file` 生成的文件写 runtime trace，不写 skill trace。
- 触发是否需要脚本仍有一部分 heuristic，后续应逐步沉淀为 action contract。
