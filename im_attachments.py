"""Extract Feishu file references and fetch original bytes on explicit request."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


FILE_TAG = re.compile(r'<file\s+key="(file_[A-Za-z0-9_-]+)"\s+name="([^"]+)"\s*/?>')
MAX_BYTES = 512 * 1024 * 1024


def file_refs(content: str, message_type: str) -> list[dict[str, str]]:
    refs = [{"key": key, "name": name} for key, name in FILE_TAG.findall(content)]
    if refs:
        return refs
    if message_type not in {"file", "audio", "video", "media", "image"}:
        return []
    try:
        payload = json.loads(content)
    except ValueError:
        return []
    key = (payload.get("file_key") or payload.get("fileKey") or
           payload.get("image_key") or payload.get("imageKey"))
    name = payload.get("file_name") or payload.get("fileName") or "附件"
    if isinstance(key, str) and re.fullmatch(r"(?:file_|img_)[A-Za-z0-9_-]+", key):
        return [{"key": key, "name": str(name)}]
    return []


def download_original(message_id: str, ref: dict[str, str], profile: str,
                      destination: Path) -> dict[str, object]:
    """Download the untouched Feishu resource; never decode or insert it in a prompt."""
    if not re.fullmatch(r"om_[A-Za-z0-9_-]+", message_id):
        raise ValueError("无效的飞书消息 ID")
    if not re.fullmatch(r"(?:file_|img_)[A-Za-z0-9_-]+", ref["key"]):
        raise ValueError("无效的飞书附件 ID")
    resource_type = "image" if ref["key"].startswith("img_") else "file"
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if destination.is_symlink():
        raise RuntimeError("附件缓存路径异常")
    if destination.is_file():
        return {"path": str(destination), "bytes": destination.stat().st_size,
                "name": ref["name"]}
    temporary = destination.with_name(destination.name + ".download")
    temporary.unlink(missing_ok=True)
    result = subprocess.run(
        ["lark-cli", "--profile", profile, "im", "+messages-resources-download",
         "--message-id", message_id, "--file-key", ref["key"], "--type", resource_type,
         "--as", "bot", "--output", temporary.name, "--json"],
        cwd=destination.parent, text=True, capture_output=True, timeout=180, check=False,
    )
    if result.returncode:
        try:
            payload = json.loads(result.stderr or result.stdout)
            error = payload.get("error") or {}
            detail = str(error.get("message") or error.get("subtype") or "下载失败")
        except ValueError:
            detail = f"下载接口返回状态 {result.returncode}"
        temporary.unlink(missing_ok=True)
        if "im:message" in detail or "99991672" in detail:
            raise PermissionError("机器人缺少应用身份消息只读权限，无法下载飞书附件")
        raise RuntimeError(f"飞书附件下载失败：{detail[:250]}")
    if not temporary.is_file():
        raise RuntimeError("飞书报告下载成功，但本机未收到原文件")
    size = temporary.stat().st_size
    if size > MAX_BYTES:
        temporary.unlink()
        raise ValueError("附件超过 512 MB 的本机取件上限")
    temporary.replace(destination)
    destination.chmod(0o600)
    return {"path": str(destination), "bytes": size, "name": ref["name"]}
