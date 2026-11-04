"""Capability-scoped manifest for Codex-initiated Feishu attachment downloads."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from im_attachments import download_original


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / ".state" / "attachment_handles.json"
MAX_AGE = 30 * 86400


def _load() -> dict[str, dict]:
    try:
        value = json.loads(MANIFEST.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def register(message_id: str, refs: list[dict[str, str]]) -> list[dict[str, str]]:
    """Register only references that arrived in a bot event; no download occurs."""
    MANIFEST.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {key: value for key, value in _load().items()
            if isinstance(value, dict) and time.time() - value.get("created", 0) < MAX_AGE}
    handles = []
    for ref in refs[:3]:
        token = secrets.token_urlsafe(24)
        name = " ".join(str(ref["name"]).split())[:120] or "附件"
        data[token] = {"message_id": message_id, "key": ref["key"],
                       "name": name, "created": time.time()}
        handles.append({"handle": token, "name": name, "key": ref["key"]})
    temporary = MANIFEST.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False)
    os.replace(temporary, MANIFEST)
    return handles


def fetch(handle: str, *, project_dir: Path, profile: str) -> dict[str, object]:
    """Resolve an issued handle, then download original bytes into the Codex project."""
    entry = _load().get(handle)
    if not isinstance(entry, dict) or time.time() - entry.get("created", 0) > MAX_AGE:
        raise ValueError("附件凭据不存在或已过期；请在飞书重新发送文件")
    suffix = Path(entry["name"]).suffix.lower()
    if not suffix or not suffix[1:].isalnum() or len(suffix) > 12:
        suffix = ".bin"
    basename = hashlib.sha256(handle.encode()).hexdigest()[:24] + suffix
    destination = project_dir / ".feishu-attachments" / basename
    result = download_original(entry["message_id"],
                               {"key": entry["key"], "name": entry["name"]},
                               profile, destination)
    return result
