"""Read and answer an explicit @bot request in a Feishu document comment."""

from __future__ import annotations

import json
import re
import subprocess

from privacy import is_personal_question


COMMENT_EVENT = "drive.notice.comment_add_v1"
COMMENT_TYPES = {"doc", "docx", "sheet", "slides", "bitable", "file", "apps"}
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def comment_notice_id(event: dict) -> str | None:
    """Return a durable, source-specific key for a notice addressed to this bot."""
    if event.get("type") != COMMENT_EVENT or event.get("is_mentioned") is not True:
        return None
    if (event.get("from_user_type") == "bot"
            or (event.get("from_open_id") and event.get("from_open_id") == event.get("to_open_id"))):
        return None
    file_type = event.get("file_type")
    file_token = event.get("file_token")
    comment_id = event.get("comment_id")
    reply_id = event.get("reply_id")
    if (file_type not in COMMENT_TYPES or not isinstance(file_token, str)
            or not TOKEN_RE.fullmatch(file_token) or not isinstance(comment_id, str)
            or not TOKEN_RE.fullmatch(comment_id)):
        return None
    if reply_id and (not isinstance(reply_id, str) or not TOKEN_RE.fullmatch(reply_id)):
        return None
    return f"dc:{file_type}:{file_token}:{comment_id}:{reply_id or 'root'}"


def cli_json(profile: str, *args: str, timeout: int = 30) -> dict:
    result = subprocess.run(["lark-cli", "--profile", profile, *args],
                            capture_output=True, text=True, timeout=timeout, check=False)
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        payload = None
    if result.returncode or not isinstance(payload, dict) or payload.get("ok") is not True:
        error = (payload or {}).get("error") or {}
        if not error:
            try:
                error = (json.loads(result.stderr) or {}).get("error") or {}
            except ValueError:
                pass
        reason = error.get("subtype") or error.get("type") or f"exit {result.returncode}"
        raise RuntimeError(f"Feishu document operation failed: {reason}")
    return payload


def text_from_reply(reply: dict) -> str:
    parts = []
    for element in (reply.get("content") or {}).get("elements") or []:
        if element.get("type") == "text_run":
            value = (element.get("text_run") or {}).get("text")
            if isinstance(value, str):
                parts.append(value)
        elif element.get("type") == "docs_link":
            link = element.get("docs_link") or {}
            value = link.get("url") or link.get("link")
            if isinstance(value, str):
                parts.append(value)
    return "".join(parts).strip()


def load_comment_request(record: dict, profile: str) -> tuple[str, str, bool, bool]:
    """Fetch the mentioned reply and docx body with the bot's own access."""
    token, kind, comment_id = record["file_token"], record["file_type"], record["comment_id"]
    payload = cli_json(profile, "drive", "+batch-query-comments", "--token", token,
                       "--type", kind, "--comment-ids", comment_id, "--as", "bot", "--json")
    items = (payload.get("data") or {}).get("items") or []
    comment = next((item for item in items if item.get("comment_id") == comment_id), None)
    if comment is None:
        raise RuntimeError("Feishu comment is no longer available to the bot")
    replies = (comment.get("reply_list") or {}).get("replies") or []
    reply_id = record.get("reply_id")
    reply = next((item for item in replies if item.get("reply_id") == reply_id), None)
    if reply is None and not reply_id and replies:
        reply = replies[0]
    if reply is None:
        page = cli_json(profile, "drive", "+list-replies", "--token", token,
                        "--type", kind, "--comment-id", comment_id, "--as", "bot", "--json")
        listed = ((page.get("data") or {}).get("items") or [])
        reply = next((item for item in listed if item.get("reply_id") == reply_id), None)
        if reply is None and not reply_id and listed:
            reply = listed[0]
    if reply is None:
        raise RuntimeError("Mentioned Feishu comment reply is not available")
    question = text_from_reply(reply)
    if not question:
        raise RuntimeError("Mentioned Feishu comment has no supported text")
    document = ""
    if kind == "docx" and not is_personal_question(question):
        try:
            fetched = cli_json(profile, "docs", "+fetch", "--doc", token, "--as", "bot",
                               "--scope", "full", "--doc-format", "markdown", timeout=60)
            document = ((fetched.get("data") or {}).get("document") or {}).get("content") or ""
            if not isinstance(document, str):
                document = ""
        except (RuntimeError, subprocess.TimeoutExpired):
            # A comment can be visible while its parent document is not.
            # Preserve the request in Codex and explain the missing body there.
            document = ""
    return question, document, bool(comment.get("is_whole")), bool(comment.get("is_solved"))


def publish_comment_answer(record: dict, profile: str, text: str) -> None:
    """Reply in a local comment, or create a linked whole-doc comment."""
    token, kind, comment_id = record["file_token"], record["file_type"], record["comment_id"]
    if record.get("is_whole"):
        if kind not in {"doc", "docx", "file"}:
            raise RuntimeError("This Feishu comment type cannot accept a whole-document reply")
        elements = []
        author = record.get("sender_id")
        if isinstance(author, str) and author.startswith("ou_"):
            elements.append({"type": "mention_user", "mention_user": author})
        elements.append({"type": "text", "text": f"福头 ✨ 回复评论 {comment_id}：\n{text}"})
        cli_json(profile, "drive", "+add-comment", "--doc", token, "--type", kind,
                 "--full-comment", "--content", json.dumps(elements, ensure_ascii=False),
                 "--as", "bot", "--json")
    else:
        elements = [{"type": "text", "text": text}]
        cli_json(profile, "drive", "+add-reply", "--token", token, "--type", kind,
                 "--comment-id", comment_id, "--content", json.dumps(elements, ensure_ascii=False),
                 "--as", "bot", "--json")
