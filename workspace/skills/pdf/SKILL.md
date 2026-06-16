---
name: pdf
description: Use this skill for PDF files: reading, extracting text/tables, creating simple PDF reports, merging, splitting, extracting pages, rotating, cropping, encrypting/decrypting, and form-related inspection/filling.
triggers:
- .pdf
- pdf
- PDF
- PDF文件
- 读取PDF
- 总结PDF
- 提取PDF
- PDF表格
- 合并PDF
- 拆分PDF
- 旋转PDF
- 生成PDF
- 转PDF
license: Proprietary. LICENSE.txt has complete terms
---

# PDF Skill

## Workflow

1. Determine whether the user wants to read, extract, create, transform, or fill a PDF.
2. For uploaded PDFs, use `read_uploaded_file` first for simple reading/summary.
3. Use `run_skill_script` only with one of the allowed scripts listed below.
4. For generated or modified PDFs, write the output to the current session outbox.
5. After a tool succeeds, reply with the real output path and a short summary.
6. If extraction is incomplete, say so clearly; do not infer missing content from filenames.

## Policy

- Do not invent script paths. `script_path` is a closed set.
- Do not claim a PDF was created or modified unless a tool returned a real output path.
- Do not use OCR, `pdf2image`, Poppler, `qpdf`, or `pdftk` unless the user explicitly asks and the dependency is confirmed.
- For simple PDF reports, prefer `scripts/create_report.py` or `save_outbox_file`.
- For simple PDF reading, prefer `read_uploaded_file`; use extraction scripts only when precision or tables matter.

## Allowed Scripts

Use only these script paths with `run_skill_script(skill_name="pdf", ...)`:

- `scripts/create_report.py`
- `scripts/extract_text.py`
- `scripts/extract_tables.py`
- `scripts/pdf_ops.py`
- `scripts/check_fillable_fields.py`
- `scripts/extract_form_field_info.py`
- `scripts/extract_form_structure.py`
- `scripts/fill_fillable_fields.py`
- `scripts/fill_pdf_form_with_annotations.py`

Do not call sample files, cached files, or invented script paths.

## Common Workflows

### Read Or Summarize PDF

For ordinary PDF questions:

1. call `read_uploaded_file`
2. answer from the extracted text only

For more precise extraction:

```text
run_skill_script(
  skill_name="pdf",
  script_path="scripts/extract_text.py",
  arguments=["<input.pdf>", "--max-chars", "12000"],
  timeout_seconds=60
)
```

### Create PDF Report

Use either `save_outbox_file` with a `.pdf` filename or:

```text
run_skill_script(
  skill_name="pdf",
  script_path="scripts/create_report.py",
  arguments=["<output.pdf>", "--title", "<title>", "--content", "<content>"],
  timeout_seconds=60
)
```

The output path must be in the current session outbox.

### Extract Tables

```text
run_skill_script(
  skill_name="pdf",
  script_path="scripts/extract_tables.py",
  arguments=["<input.pdf>", "--output-xlsx", "<output.xlsx>"],
  timeout_seconds=60
)
```

If the script returns no tables, say no structured tables were detected. Do not fabricate tables.

### PDF Operations

Use `scripts/pdf_ops.py`:

- inspect: `--operation inspect --input <input.pdf>`
- merge: `--operation merge --inputs <a.pdf> <b.pdf> --output <merged.pdf>`
- extract pages: `--operation extract-pages --input <input.pdf> --pages 1-3 --output <output.pdf>`
- split: `--operation split --input <input.pdf> --pages all --output-dir <outbox_dir>`
- rotate: `--operation rotate --input <input.pdf> --pages 1 --angle 90 --output <output.pdf>`
- crop: `--operation crop --input <input.pdf> --crop 50,50,550,750 --output <output.pdf>`
- encrypt/decrypt: use `--password` and output to outbox

### PDF Forms

Use form scripts only when the user explicitly asks about form fields or filling a form:

1. inspect with `scripts/check_fillable_fields.py`
2. extract field details with `scripts/extract_form_field_info.py`
3. fill with `scripts/fill_fillable_fields.py` when field values are known

## Failure Rules

- If `Skill script not found` occurs, do not try another guessed path.
- If an output script succeeds, use the returned JSON `output` path as the truth.
- If a script fails because of dependency or malformed PDF issues, report the exact failure and do not claim success.
