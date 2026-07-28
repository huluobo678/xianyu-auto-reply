from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(cookie|authorization|device[_-]?token|access[_-]?token|refresh[_-]?token|"
    r"xianyu[_-]?token|token|password|passwd|secret(?:_key)?)"
    r"(\s*[=:]\s*|[\"']\s*:\s*[\"'])([^\s,;&}\]]+|[^\"']*)"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_COOKIE_HEADER = re.compile(r"(?i)\bCookie\s*:\s*[^\r\n]+")
_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_VERIFICATION_CONTEXT = re.compile(
    r"(?i)(captcha|slider|verify|verification|risk|face|"
    r"\u4e8c\u7ef4\u7801|\u9a8c\u8bc1\u7801|\u6ed1\u5757|"
    r"\u4eba\u8138|\u98ce\u63a7)"
)


def redact_sensitive_text(value: Any) -> str:
    text = str(value)
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _COOKIE_HEADER.sub(f"Cookie: {REDACTED}", text)
    text = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", text
    )
    if _VERIFICATION_CONTEXT.search(text):
        text = _URL.sub(REDACTED, text)
    return text


def redact_log_record(record: dict[str, Any]) -> bool:
    record["message"] = redact_sensitive_text(record.get("message", ""))
    exception = record.get("exception")
    if exception is not None:
        exception_text = str(exception.value)
        if redact_sensitive_text(exception_text) != exception_text:
            record["exception"] = None
    return True
