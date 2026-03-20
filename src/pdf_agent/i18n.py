"""Internationalization — system prompt and UI strings in en/zh."""
from __future__ import annotations

from pdf_agent.config import settings

PROMPTS = {
    "en": """\
You are **PDF Toolbox**, an assistant that helps a single user process PDF files.
You have access to a set of PDF tools (merge, split, rotate, watermark, compress, OCR, etc.).

## Workflow
1. The user describes what they want (e.g., "add a watermark", "merge these PDFs").
2. You choose the right tool and parameters, then call it.
3. After execution, report the result and ask if the user wants adjustments.
4. Repeat until the user is satisfied.

## Rules
- Call **one tool at a time** (parallel tool calls are disabled).
- If the user hasn't uploaded a file yet, ask them to upload first.
- When a tool fails, report the error and suggest alternatives or parameter changes.
- Always refer to files by their original name for clarity.
- For page_range parameters, use "all" to target every page, or expressions like "1-3,5", "odd", "even".
- Keep responses concise and helpful.
    """,
    "zh": """\
你是 **PDF Toolbox**，一个帮助单个用户处理 PDF 文件的工具助手。
你可以使用一系列 PDF 工具（合并、拆分、旋转、水印、压缩、OCR 等）。

## 工作流程
1. 用户描述需求（例如："加个水印"、"把这些 PDF 合并"）。
2. 你选择合适的工具和参数，然后调用。
3. 执行完成后，报告结果并询问用户是否需要调整。
4. 重复直到用户满意。

## 规则
- 每次只调用**一个工具**（并行调用已禁用）。
- 如果用户还没有上传文件，先提示上传。
- 工具执行失败时，报告错误并建议替代方案或参数修改。
- 始终使用文件原始名称来指代文件。
- page_range 参数使用 "all" 表示所有页面，或 "1-3,5"、"odd"、"even" 等表达式。
- 回复简洁有用。
""",
}

UI_STRINGS = {
    "en": {
        "app_title": "PDF Toolbox",
        "new_chat": "+ New",
        "empty_title": "PDF Toolbox",
        "empty_desc": "Upload a PDF and tell me what you'd like to do. I can rotate, merge, split, compress, watermark, OCR, and much more.",
        "input_placeholder": "Type a message...",
        "drop_hint": "Drop files here or",
        "browse": "browse",
        "delete_confirm": "Delete this thread?",
        "export_md": "Export Markdown",
        "export_json": "Export JSON",
        "search_placeholder": "Search messages...",
        "dark_mode": "Dark mode",
        "light_mode": "Light mode",
        "settings": "Settings",
        "running": "Running...",
        "done": "Done",
    },
    "zh": {
        "app_title": "PDF 工具箱",
        "new_chat": "+ 新对话",
        "empty_title": "PDF 工具箱",
        "empty_desc": "上传 PDF 文件，告诉我你想做什么。支持旋转、合并、拆分、压缩、水印、OCR 等操作。",
        "input_placeholder": "输入消息...",
        "drop_hint": "拖放文件到此处，或",
        "browse": "浏览",
        "delete_confirm": "确认删除此对话？",
        "export_md": "导出 Markdown",
        "export_json": "导出 JSON",
        "search_placeholder": "搜索消息...",
        "dark_mode": "深色模式",
        "light_mode": "浅色模式",
        "settings": "设置",
        "running": "执行中...",
        "done": "完成",
    },
}


def get_system_prompt(locale: str | None = None) -> str:
    loc = locale or settings.default_locale
    return PROMPTS.get(loc, PROMPTS["en"])


def get_ui_strings(locale: str | None = None) -> dict[str, str]:
    loc = locale or settings.default_locale
    return UI_STRINGS.get(loc, UI_STRINGS["en"])
