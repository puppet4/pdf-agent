"""Error codes used across the application."""
from __future__ import annotations


class ErrorCode:
    INVALID_INPUT_FILE = "INVALID_INPUT_FILE"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    INVALID_PARAMS = "INVALID_PARAMS"
    INVALID_PAGE_RANGE = "INVALID_PAGE_RANGE"
    ENGINE_NOT_INSTALLED = "ENGINE_NOT_INSTALLED"
    ENGINE_EXEC_TIMEOUT = "ENGINE_EXEC_TIMEOUT"
    ENGINE_EXEC_FAILED = "ENGINE_EXEC_FAILED"
    OUTPUT_GENERATION_FAILED = "OUTPUT_GENERATION_FAILED"
    JOB_CANCELED = "JOB_CANCELED"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PAGE_COUNT_EXCEEDED = "PAGE_COUNT_EXCEEDED"
    STORAGE_LIMIT_EXCEEDED = "STORAGE_LIMIT_EXCEEDED"


# Localized error message templates keyed by error code
_ERROR_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        ErrorCode.INVALID_INPUT_FILE: "Invalid input file",
        ErrorCode.UNSUPPORTED_FORMAT: "Unsupported file format",
        ErrorCode.INVALID_PARAMS: "Invalid parameters",
        ErrorCode.INVALID_PAGE_RANGE: "Invalid page range",
        ErrorCode.ENGINE_NOT_INSTALLED: "Required engine is not installed",
        ErrorCode.ENGINE_EXEC_TIMEOUT: "Processing timed out",
        ErrorCode.ENGINE_EXEC_FAILED: "Processing engine failed",
        ErrorCode.OUTPUT_GENERATION_FAILED: "Failed to generate output",
        ErrorCode.FILE_NOT_FOUND: "File not found",
        ErrorCode.FILE_TOO_LARGE: "File exceeds size limit",
        ErrorCode.PAGE_COUNT_EXCEEDED: "Page count exceeds limit",
        ErrorCode.STORAGE_LIMIT_EXCEEDED: "Storage limit exceeded",
    },
    "zh": {
        ErrorCode.INVALID_INPUT_FILE: "无效的输入文件",
        ErrorCode.UNSUPPORTED_FORMAT: "不支持的文件格式",
        ErrorCode.INVALID_PARAMS: "参数无效",
        ErrorCode.INVALID_PAGE_RANGE: "页面范围无效",
        ErrorCode.ENGINE_NOT_INSTALLED: "所需引擎未安装",
        ErrorCode.ENGINE_EXEC_TIMEOUT: "处理超时",
        ErrorCode.ENGINE_EXEC_FAILED: "处理引擎执行失败",
        ErrorCode.OUTPUT_GENERATION_FAILED: "生成输出文件失败",
        ErrorCode.FILE_NOT_FOUND: "文件不存在",
        ErrorCode.FILE_TOO_LARGE: "文件超过大小限制",
        ErrorCode.PAGE_COUNT_EXCEEDED: "页数超过限制",
        ErrorCode.STORAGE_LIMIT_EXCEEDED: "存储空间不足",
    },
}


def localized_error(code: str, detail: str = "", locale: str | None = None) -> str:
    """Return a localized error message for the given error code."""
    from pdf_agent.config import settings
    loc = locale or settings.default_locale
    messages = _ERROR_MESSAGES.get(loc, _ERROR_MESSAGES["en"])
    base = messages.get(code, code)
    return f"{base}: {detail}" if detail else base


class PDFAgentError(Exception):
    def __init__(self, code: str, message: str, locale: str | None = None) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


_ERROR_HTTP_STATUS: dict[str, int] = {
    ErrorCode.FILE_NOT_FOUND: 404,
    ErrorCode.JOB_NOT_FOUND: 404,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.PAGE_COUNT_EXCEEDED: 413,
    ErrorCode.STORAGE_LIMIT_EXCEEDED: 507,
    ErrorCode.UNSUPPORTED_FORMAT: 415,
    ErrorCode.INVALID_INPUT_FILE: 422,
    ErrorCode.INVALID_PARAMS: 422,
    ErrorCode.INVALID_PAGE_RANGE: 422,
    ErrorCode.ENGINE_NOT_INSTALLED: 503,
    ErrorCode.ENGINE_EXEC_TIMEOUT: 504,
    ErrorCode.ENGINE_EXEC_FAILED: 500,
    ErrorCode.OUTPUT_GENERATION_FAILED: 500,
    ErrorCode.JOB_CANCELED: 499,
}


def error_http_status(code: str) -> int:
    return _ERROR_HTTP_STATUS.get(code, 400)


class ToolError(PDFAgentError):
    pass
