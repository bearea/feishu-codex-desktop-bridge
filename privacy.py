"""Fail-closed checks before a Codex answer leaves the local machine."""

from __future__ import annotations

import re


OUT_OF_SCOPE = "福头目前只回答产品功能、使用、需求和故障相关的问题。请换一个产品问题再问我。"

# These are never suitable for an IM answer, even after a review click.
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret[_-]?key)\s*[:=]\s*[^\s,;]{6,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"(?i)\b(?:jdbc|mysql|postgres(?:ql)?|mongodb|redis)://[^\s]+"),
)


def export_decision(answer: str) -> str:
    """Return block, review, or auto. Unknown substantive text requires review."""
    if not answer or len(answer) > 9000:
        return "block"
    if any(pattern.search(answer) for pattern in SECRET_PATTERNS):
        return "block"
    if "```" in answer or re.search(r"(?m)^\s*(?:curl\s|SELECT\s|INSERT\s|UPDATE\s|def\s|class\s|import\s|from\s+\w+\s+import\s)", answer, re.I):
        return "block"
    if answer.strip() == OUT_OF_SCOPE:
        return "auto"
    return "review"


def has_long_verbatim_overlap(answer: str, tool_items: list[dict], window: int = 80) -> bool:
    """Block long verbatim spans copied from local files or other Codex chats."""
    normalized_answer = " ".join(answer.split())
    if len(normalized_answer) < window:
        return False

    def strings(value: object):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for nested in value.values():
                yield from strings(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from strings(nested)

    for item in tool_items:
        if item.get("type") == "commandExecution":
            outputs = [item.get("aggregatedOutput")]
        elif item.get("type") == "mcpToolCall":
            outputs = [item.get("result")]
        else:
            continue
        for output in outputs:
            for source in strings(output):
                normalized_source = " ".join(source.split())
                if len(normalized_source) < window:
                    continue
                for offset in range(len(normalized_answer) - window + 1):
                    if normalized_answer[offset:offset + window] in normalized_source:
                        return True
    return False
