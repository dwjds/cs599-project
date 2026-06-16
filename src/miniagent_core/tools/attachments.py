from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..attachments import Attachment, AttachmentStore
from .base import Tool


class ListUploadedFilesTool(Tool):
    """列出当前回合上传文件。"""

    def __init__(self, attachments: list[Attachment]):
        self.attachments = attachments

    @property
    def name(self) -> str:
        return "list_uploaded_files"

    @property
    def description(self) -> str:
        return "List files uploaded in the current user turn."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        if not self.attachments:
            return "No uploaded files are available in the current turn."
        lines = ["Uploaded files in the current turn:"]
        for index, item in enumerate(self.attachments, start=1):
            lines.append(
                f"{index}. {item.name} | path={item.path} | type={item.content_type or 'unknown'} | size={item.size} bytes"
            )
        return "\n".join(lines)


class ReadUploadedFileTool(Tool):
    """读取当前回合上传文件文本。"""

    def __init__(self, store: AttachmentStore, attachments: list[Attachment]):
        self.store = store
        self.attachments = attachments

    @property
    def name(self) -> str:
        return "read_uploaded_file"

    @property
    def description(self) -> str:
        return "Read text from a file uploaded in the current user turn."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Uploaded filename to read"},
                "path": {"type": "string", "description": "Exact uploaded file path to read"},
                "max_chars": {"type": "integer", "description": "Maximum characters to return"},
            },
        }

    async def execute(
        self,
        filename: str | None = None,
        path: str | None = None,
        max_chars: int = 20000,
        **kwargs,
    ) -> str:
        attachment = self._find_attachment(filename=filename, path=path)
        if attachment is None:
            return "Error: uploaded file not found in the current turn."
        try:
            content = self.store.read_text(attachment.path, max_chars=max_chars)
        except Exception as exc:
            return f"Error: {exc}"
        return f"Uploaded file: {attachment.name}\nPath: {attachment.path}\n\n{content}"

    def _find_attachment(
        self,
        *,
        filename: str | None = None,
        path: str | None = None,
    ) -> Attachment | None:
        if path:
            target = str(Path(path).expanduser().resolve())
            for item in self.attachments:
                if str(Path(item.path).resolve()) == target:
                    return item
        if filename:
            query = str(filename).strip().lower()
            for item in self.attachments:
                if item.name.lower() == query:
                    return item
        if len(self.attachments) == 1:
            return self.attachments[0]
        return None


class SaveOutboxFileTool(Tool):
    """把结果保存到当前会话的 outbox。"""

    def __init__(self, store: AttachmentStore, session_key: str):
        self.store = store
        self.session_key = session_key

    @property
    def name(self) -> str:
        return "save_outbox_file"

    @property
    def description(self) -> str:
        return "Save a generated text file into workspace/outbox for the current session."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Output filename such as result.md"},
                "content": {"type": "string", "description": "Text content to save"},
                "title": {"type": "string", "description": "Optional document title"},
                "table_json": {
                    "type": "string",
                    "description": "Optional JSON table data. Supports list-of-lists, list-of-objects, or {headers, rows}.",
                },
                "sheet_name": {"type": "string", "description": "Optional worksheet name for XLSX output"},
            },
            "required": ["filename"],
        }

    async def execute(
        self,
        filename: str,
        content: str = "",
        title: str = "",
        table_json: str = "",
        sheet_name: str = "Sheet1",
        **kwargs,
    ) -> str:
        table_data = None
        if table_json:
            try:
                table_data = json.loads(table_json)
            except json.JSONDecodeError as exc:
                return f"Error: invalid table_json: {exc}"
        attachment = self.store.save_outbox_file(
            session_key=self.session_key,
            filename=filename,
            content=content,
            title=title,
            table_data=table_data,
            sheet_name=sheet_name,
        )
        return (
            f"Saved generated file to workspace/outbox.\n"
            f"Filename: {attachment.name}\n"
            f"Path: {attachment.path}"
        )


class ListOutboxFilesTool(Tool):
    """列出当前会话结果文件。"""

    def __init__(self, store: AttachmentStore, session_key: str):
        self.store = store
        self.session_key = session_key

    @property
    def name(self) -> str:
        return "list_outbox_files"

    @property
    def description(self) -> str:
        return "List generated files saved under workspace/outbox for the current session."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs) -> str:
        items = self.store.list_session_outbox(self.session_key)
        if not items:
            return "No generated files have been saved for the current session."
        lines = ["Generated files in workspace/outbox:"]
        for index, item in enumerate(items, start=1):
            lines.append(f"{index}. {item.name} | path={item.path} | size={item.size} bytes")
        return "\n".join(lines)
