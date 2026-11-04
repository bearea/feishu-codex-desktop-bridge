#!/usr/bin/env python3
"""Minimal stdio MCP server exposing one capability-scoped Feishu download tool."""

from __future__ import annotations

import json
import os
import base64
import shutil
import subprocess
import sys
from pathlib import Path

from attachment_store import fetch


TOOL = "fetch_feishu_attachment"


def video_frames(path: Path, seconds: list[float]) -> list[dict]:
    """Return small JPEG previews through MCP without writing extracted frames."""
    if path.suffix.lower() not in {".mp4", ".mov", ".m4v", ".webm"}:
        raise ValueError("画面预览只支持视频文件")
    ffmpeg = next((str(path) for path in
                   (Path("/opt/homebrew/bin/ffmpeg"), Path("/usr/local/bin/ffmpeg"))
                   if path.is_file()), None) or shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("本机尚未安装 ffmpeg，无法解码视频画面")
    content: list[dict] = []
    for second in seconds:
        if not isinstance(second, (int, float)) or not 0 <= second <= 3600:
            raise ValueError("预览时间必须在 0～3600 秒之间")
        result = subprocess.run(
            [ffmpeg, "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
             "-ss", str(second), "-i", str(path),
             "-frames:v", "1", "-vf", "scale=720:-2", "-f", "image2pipe",
             "-vcodec", "mjpeg", "pipe:1"],
            capture_output=True, timeout=30, check=False,
        )
        if result.returncode or not result.stdout:
            content.append({"type": "text", "text": f"{second} 秒画面无法解码：{result.stderr.decode(errors='replace')[:200]}"})
            continue
        if len(result.stdout) > 1024 * 1024:
            raise ValueError("预览画面超过 1 MB，已停止返回")
        content.append({"type": "text", "text": f"视频 {second} 秒处的画面："})
        content.append({"type": "image", "data": base64.b64encode(result.stdout).decode(),
                        "mimeType": "image/jpeg"})
    return content


def dispatch(request: dict) -> dict | None:
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                "serverInfo": {"name": "futou_attachment", "version": "1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": TOOL,
                          "description": "按本条飞书消息提供的附件凭据，按需下载原始二进制文件。默认仅返回本机路径和大小；视频可选 preview_seconds，返回少量画面供 Codex 观察，不返回文件原文或音频转写。",
                          "inputSchema": {"type": "object", "properties": {
                              "handle": {"type": "string", "description": "消息里给出的附件凭据"},
                              "preview_seconds": {"type": "array", "description": "可选：最多 4 个视频画面时间点（秒）",
                                                  "items": {"type": "number"}, "maxItems": 4}},
                              "required": ["handle"], "additionalProperties": False}}]}
    if method == "tools/call":
        params = request.get("params") or {}
        if params.get("name") != TOOL:
            raise ValueError("未知取件工具")
        handle = (params.get("arguments") or {}).get("handle")
        if not isinstance(handle, str):
            raise ValueError("缺少附件凭据")
        project = Path(os.environ["FEISHU_CODEX_PROJECT_DIR"]).resolve()
        profile = os.environ.get("FEISHU_CODEX_LARK_PROFILE", "futou_bot")
        value = fetch(handle, project_dir=project, profile=profile)
        content = [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]
        seconds = (params.get("arguments") or {}).get("preview_seconds")
        if seconds is not None:
            if not isinstance(seconds, list) or len(seconds) > 4:
                raise ValueError("最多预览 4 个视频时间点")
            content.extend(video_frames(Path(value["path"]), seconds))
        return {"content": content}
    raise ValueError("不支持的 MCP 方法")


def main() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            result = dispatch(request)
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (ValueError, KeyError, OSError, RuntimeError, PermissionError) as exc:
            response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                        "error": {"code": -32000, "message": str(exc)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
