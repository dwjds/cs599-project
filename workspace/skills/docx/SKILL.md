---
name: docx
description: Use this skill for Word/.docx files: reading, summarizing, creating simple Word deliverables, accepting tracked changes, comments, or high-fidelity DOCX XML workflows.
triggers:
- .docx
- .doc
- Word
- word文档
- Word文档
- 文档
- 生成文档
- 修改文档
- 接受修订
- 清除修订
- tracked changes
license: Proprietary. LICENSE.txt has complete terms
---

# DOCX Skill

## Workflow

1. Determine whether the user wants to read, create, edit, validate, or accept tracked changes.
2. For uploaded files, use `read_uploaded_file` first unless the task is only creating a new document from user-provided text.
3. For simple Word deliverables, use `save_outbox_file` with a `.docx` filename.
4. For tracked changes, comments, or XML-level edits, use only the allowed scripts listed below.
5. Save every generated or modified file to the current session outbox, never overwrite inbox files.
6. After a tool succeeds, reply with the real filename/path and a short summary.

## Policy

- Do not invent script paths. `script_path` is a closed set.
- Simple Word generation should use `save_outbox_file`, not `run_skill_script`.
- Never overwrite inbox files. Save generated or modified files to the current session outbox.
- If a requested script is not listed below, do not guess alternatives.
- If a tool creates the file successfully, stop calling tools and reply with the real path.

## Preferred Tools

- Read or summarize uploaded Word files: use `read_uploaded_file`.
- Create a simple `.docx` from text/table content: use `save_outbox_file` with a `.docx` filename.
- Accept tracked changes: use `run_skill_script` with `scripts/accept_changes.py`.
- Add comments or do XML-level edits: use the explicit XML workflow below.
- Validate generated/edited DOCX packages: use `scripts/office/validate.py`.

## Allowed Scripts

Use only these script paths with `run_skill_script(skill_name="docx", ...)`:

- `scripts/accept_changes.py`
- `scripts/comment.py`
- `scripts/office/unpack.py`
- `scripts/office/pack.py`
- `scripts/office/validate.py`
- `scripts/office/soffice.py`

Do not call invented script paths, cached files, schemas, helpers, or templates directly.

## Common Workflows

### Create Simple Word Document

Use `save_outbox_file`:

```json
{
  "filename": "MiniAgent_Notes.docx",
  "title": "MiniAgent Notes",
  "content": "Final document body...",
  "table_data": null
}
```

Use this for reports, checklists, memos, summaries, and simple formatted deliverables.

### Accept Tracked Changes

Use:

```text
run_skill_script(
  skill_name="docx",
  script_path="scripts/accept_changes.py",
  arguments=["<input_docx>", "<output_docx>"],
  timeout_seconds=60
)
```

The output path must be in the current session outbox.

### XML-Level Editing

Use this only for complex edits, comments, tracked changes, or package-level operations:

1. `scripts/office/unpack.py <input.docx> <work_dir>`
2. edit the unpacked XML with real tools
3. `scripts/office/pack.py <work_dir> <output.docx> --original <input.docx>`
4. `scripts/office/validate.py <output.docx>`

## Failure Rules

- If `run_skill_script` says `Skill script not found`, do not try another guessed path.
- For simple `.docx` generation, immediately fall back to `save_outbox_file`.
- If validation fails, report the failure and do not claim the document is valid.
