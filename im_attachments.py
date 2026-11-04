"""Read supported Feishu IM attachments with the bot's own permissions."""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path


FILE_TAG = re.compile(r'<file\s+key="(file_[A-Za-z0-9_-]+)"\s+name="([^"]+)"\s*/?>')
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 120_000
SUPPORTED_SUFFIXES = {".csv", ".tsv", ".txt", ".md", ".json"}


def file_refs(content: str, message_type: str) -> list[dict[str, str]]:
    refs = [{"key": key, "name": name} for key, name in FILE_TAG.findall(content)]
    if refs:
        return refs
    if message_type != "file":
        return []
    try:
        payload = json.loads(content)
    except ValueError:
        return []
    key = payload.get("file_key") or payload.get("fileKey")
    name = payload.get("file_name") or payload.get("fileName") or "附件"
    if isinstance(key, str) and re.fullmatch(r"file_[A-Za-z0-9_-]+", key):
        return [{"key": key, "name": str(name)}]
    return []


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文件不是可识别的 UTF-8 或 GB18030 文本")


def _csv_text(data: bytes, *, delimiter: str) -> str:
    source = _decode(data)
    rows = csv.reader(io.StringIO(source), delimiter=delimiter)
    rendered: list[str] = []
    size = 0
    for number, row in enumerate(rows, 1):
        line = f"第 {number} 行：" + "；".join(
            f"第 {index} 列={value.strip()}" for index, value in enumerate(row, 1))
        if size + len(line) > MAX_TEXT:
            rendered.append("[文件较长，后续行未读取；答复须说明仅依据已读取部分。]")
            break
        rendered.append(line)
        size += len(line) + 1
    return "\n".join(rendered)


def _file_text(name: str, data: bytes) -> str:
    suffix = Path(name).suffix.lower()
    if suffix == ".csv":
        return _csv_text(data, delimiter=",")
    if suffix == ".tsv":
        return _csv_text(data, delimiter="\t")
    if suffix in {".txt", ".md", ".json"}:
        content = _decode(data)
        return content[:MAX_TEXT] + (
            "\n[文件较长，后续内容未读取；答复须说明仅依据已读取部分。]"
            if len(content) > MAX_TEXT else "")
    raise ValueError(f"暂不支持读取 {suffix or '未知格式'} 文件正文")


def load_file(message_id: str, ref: dict[str, str], profile: str, state_dir: Path) -> str:
    """Download into a private temporary directory, then return extracted text."""
    suffix = Path(ref["name"]).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"暂不支持读取 {suffix or '未知格式'} 文件正文")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="im-file-", dir=state_dir) as directory:
        target = Path(directory) / f"attachment{suffix}"
        result = subprocess.run(
            ["lark-cli", "--profile", profile, "im", "+messages-resources-download",
             "--message-id", message_id, "--file-key", ref["key"], "--type", "file",
             "--as", "bot", "--output", target.name, "--json"],
            cwd=directory, text=True, capture_output=True, timeout=60, check=False,
        )
        if result.returncode:
            try:
                payload = json.loads(result.stderr or result.stdout)
                error = payload.get("error") or {}
                detail = str(error.get("message") or error.get("subtype") or "下载失败")
            except ValueError:
                detail = f"下载接口返回状态 {result.returncode}"
            if "im:message" in detail or "99991672" in detail:
                raise PermissionError("机器人缺少应用身份消息只读权限，暂时无法下载飞书附件")
            raise RuntimeError(f"飞书附件下载失败：{detail[:250]}")
        if not target.is_file():
            raise RuntimeError("飞书报告下载成功，但本机未收到附件文件")
        if target.stat().st_size > MAX_BYTES:
            raise ValueError("附件超过 8 MB 的本机读取上限")
        return _file_text(ref["name"], target.read_bytes())
