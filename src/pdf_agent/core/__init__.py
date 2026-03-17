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


class PDFAgentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


class ToolError(PDFAgentError):
    pass
