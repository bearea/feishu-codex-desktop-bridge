#!/usr/bin/env python3
"""Minimal stdio MCP server exposing one capability-scoped Feishu download tool."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from attachment_store import fetch


TOOL = "fetch_feishu_attachment"


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
                          "description": "按本条飞书消息提供的附件凭据，下载原始二进制文件到当前 Codex 项目。仅返回本机路径、文件名和字节数，不返回文件正文。",
                          "inputSchema": {"type": "object", "properties": {
                              "handle": {"type": "string", "description": "消息里给出的附件凭据"}},
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
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
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
