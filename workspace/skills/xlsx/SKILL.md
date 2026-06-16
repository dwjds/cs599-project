---
name: xlsx
description: Use this skill when a spreadsheet is the primary input or output: reading, summarizing, filtering, editing, recalculating, creating, or exporting .xlsx/.xlsm/.csv/.tsv data.
triggers:
- .xlsx
- .xlsm
- .csv
- .tsv
- xlsx
- Excel
- excel
- 表格
- 电子表格
- 工作簿
- 工作表
- 新增列
- 新增行
- 公式
- 重算
- 汇总
- 筛选
- 数据清洗
license: Proprietary. LICENSE.txt has complete terms
---

# XLSX Skill

## Workflow

1. Determine whether the user wants to read, create, filter, edit, recalculate, or export a spreadsheet.
2. For uploaded spreadsheets, use `list_uploaded_files` / `read_uploaded_file` first unless the task is creating a new spreadsheet from scratch.
3. For common spreadsheet operations, use only the allowed scripts listed below.
4. Save every generated or modified file to the current session outbox; never overwrite inbox files.
5. If formulas are involved, run `scripts/recalc.py` on the output file and report any formula errors.
6. After a tool succeeds, reply with the real output path, row counts or changed cells, and key validation result.

## Policy

- Do not invent script paths. `script_path` is a closed set.
- Do not claim an Excel file was created, filtered, exported, recalculated, or fixed unless a tool returned a real output path/result.
- For simple new `.xlsx` files, prefer `save_outbox_file` with table data.
- For row filtering, prefer `scripts/filter_workbook.py`.
- For common cell/sheet edits, prefer `scripts/edit_workbook.py`.
- For Excel to PDF, use `scripts/convert_to_pdf.py`.
- Use `write_file` + `exec` only when the allowed scripts cannot express the requested operation.

## Allowed Scripts

Use only these script paths with `run_skill_script(skill_name="xlsx", ...)`:

- `scripts/filter_workbook.py`
- `scripts/edit_workbook.py`
- `scripts/convert_to_pdf.py`
- `scripts/recalc.py`
- `scripts/office/validate.py`
- `scripts/office/soffice.py`
- `scripts/office/unpack.py`
- `scripts/office/pack.py`

Do not call sample files, cached files, or invented script paths.

## Common Workflows

### Read Or Summarize Workbook

For ordinary questions:

1. call `read_uploaded_file`
2. answer from the returned workbook summary/content only

If exact rows, formulas, or sheet structure are needed, use `scripts/edit_workbook.py --operation inspect` or `--operation read-range`.

### Create Simple Spreadsheet

Use `save_outbox_file` with a `.xlsx` filename and table data. Example:

```json
{
  "filename": "task_status.xlsx",
  "title": "Task Status",
  "table_data": {
    "headers": ["Task", "Owner", "Status"],
    "rows": [
      ["Trace", "Runtime", "Done"],
      ["Skill", "Agent", "Done"],
      ["Memory", "Store", "Open"]
    ]
  },
  "sheet_name": "Tasks"
}
```

### Filter Rows

```text
run_skill_script(
  skill_name="xlsx",
  script_path="scripts/filter_workbook.py",
  arguments=[
    "<input.xlsx>",
    "<output.xlsx>",
    "--sheet", "Sheet1",
    "--criteria-json", "<json>"
  ],
  timeout_seconds=60
)
```

Criteria JSON:

```json
{
  "include": [
    {"columns": ["专业", "需求专业"], "keywords": ["软件工程"]},
    {"columns": ["项目名称", "研究内容", "具体项目需求工作描述", "现有研究基础与应用前景"], "keywords": ["AI", "人工智能", "大模型", "LLM"]}
  ],
  "exclude": []
}
```

Rules:

- Keywords inside one group are OR.
- Multiple include groups are AND.
- Exclude groups remove matching rows.
- Output must be a new `.xlsx` in outbox.

### Common Workbook Edits

Use:

```text
run_skill_script(
  skill_name="xlsx",
  script_path="scripts/edit_workbook.py",
  arguments=["<input.xlsx>", "<output.xlsx>", "--operation", "<operation>", ...],
  timeout_seconds=60
)
```

Supported operations:

- `inspect`
- `read-range`
- `set-cell`
- `append-row`
- `insert-row`
- `delete-row`
- `insert-column`
- `delete-column`
- `set-header`
- `add-formula-column`
- `add-sum-row`
- `rename-sheet`
- `copy-sheet`
- `delete-sheet`

Examples:

- add sum row: `--operation add-sum-row --sheet ValidModel --columns Revenue Cost Profit --label-column Month --label Total`
- append row: `--operation append-row --sheet Sheet1 --values Trace Runtime Done`
- set cell: `--operation set-cell --sheet Sheet1 --cell B2 --value 123`

### Export Excel To PDF

```text
run_skill_script(
  skill_name="xlsx",
  script_path="scripts/convert_to_pdf.py",
  arguments=["<input.xlsx>", "<output.pdf>"],
  timeout_seconds=60
)
```

Output must be a PDF path in outbox. Use the returned JSON `output` as the truth.

### Recalculate And Check Formulas

```text
run_skill_script(
  skill_name="xlsx",
  script_path="scripts/recalc.py",
  arguments=["<output.xlsx>"],
  timeout_seconds=60
)
```

If the result reports `#REF!`, `#DIV/0!`, `#VALUE!`, `#NAME?`, or nonzero `total_errors`, do not say the workbook is fully clean. Report the affected sheet/cells.

## Failure Rules

- If `Skill script not found` occurs, do not try another guessed path.
- If `openpyxl` or LibreOffice is unavailable, report the dependency failure instead of claiming success.
- If `edit_workbook.py` does not support the requested operation, write a small one-off Python script only when needed, save outputs to outbox, and execute it with `exec`.
- If a script succeeds and returns an output path, stop calling tools unless validation is still required.
