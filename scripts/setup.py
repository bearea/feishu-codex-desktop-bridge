#!/usr/bin/env python3
"""Prepare, validate, and install the macOS LaunchAgent for this bridge."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "config.example.json"


def config_path(raw: str | None) -> Path:
    return Path(raw or os.environ.get("FEISHU_CODEX_CONFIG") or ROOT / "config.json").expanduser().resolve()


def load_config(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config must be a JSON object")
    return data


def project_path(data: dict) -> Path:
    raw = str(data.get("project_dir") or "./feishu-message")
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def prepare(path: Path) -> None:
    if not path.exists():
        shutil.copy2(EXAMPLE, path)
        print(f"已生成配置模板：{path}")
    data = load_config(path)
    project = project_path(data)
    project.mkdir(parents=True, exist_ok=True)
    target = project / "AGENTS.md"
    if not target.exists():
        shutil.copy2(ROOT / "project" / "AGENTS.md", target)
    print(f"Codex 项目目录：{project}")
    print("请在 Codex Desktop 将此目录添加为本地项目，再填写 config.json。")


def check_registered_project(project: Path, config: Path) -> None:
    sys.path.insert(0, str(ROOT))
    os.environ["FEISHU_CODEX_CONFIG"] = str(config)
    import bridge
    if bridge.SESSION_CWD != project:
        raise RuntimeError("Codex 项目目录与配置不一致")
    with bridge.app_server() as process:
        bridge.find_project_id(process)


def check(path: Path) -> tuple[dict, Path, dict[str, str]]:
    data = load_config(path)
    project = project_path(data)
    if not project.is_dir() or not (project / "AGENTS.md").is_file():
        raise RuntimeError(f"项目目录或 AGENTS.md 不存在：{project}；先运行 --prepare")
    profile = str(data.get("lark_profile") or "")
    if not profile:
        raise RuntimeError("lark_profile 不能为空")
    label = str(data.get("launchd_label") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", label):
        raise RuntimeError("launchd_label 只能包含字母、数字、点、横线和下划线")
    paths = {name: shutil.which(name) or "" for name in ("codex", "lark-cli", "node")}
    if not all(paths.values()):
        raise RuntimeError(f"找不到命令：{[name for name, value in paths.items() if not value]}")
    if not (Path.home() / ".codex" / "ipc" / "ipc.sock").is_socket():
        raise RuntimeError("Codex Desktop IPC socket 不存在；请先打开并登录桌面版")
    check_registered_project(project, path)
    permission_profile = str(data.get("permission_profile") or "")
    product_read_path = str(data.get("product_read_path") or "")
    if product_read_path and not permission_profile:
        raise RuntimeError("启用产品目录读取时必须配置 permission_profile")
    if permission_profile:
        codex_config = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text())
        if permission_profile not in codex_config.get("permissions", {}):
            raise RuntimeError(f"Codex 权限配置不存在：{permission_profile}")
        if not product_read_path or not Path(product_read_path).is_dir():
            raise RuntimeError("product_read_path 必须是已存在的绝对目录")
    status = subprocess.run([paths["lark-cli"], "--profile", profile,
                             "auth", "status", "--json", "--verify"],
                            capture_output=True, text=True, timeout=20, check=False)
    if status.returncode:
        raise RuntimeError("飞书 CLI profile 验证失败；请检查应用配置")
    identity = json.loads(status.stdout)
    bot = identity.get("identities", {}).get("bot", {})
    if bot.get("status") != "ready" or bot.get("verified") is not True:
        raise RuntimeError("飞书 bot 身份尚未 ready/verified")
    print(f"检查通过：项目={project}；飞书 App ID={identity.get('appId') or '未知'}；Codex Desktop IPC 可用")
    print("飞书事件订阅和应用发布状态仍需按 README 做真实消息验收。")
    return data, project, paths


def plist_path(label: str) -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"


def build_plist(data: dict, paths: dict[str, str], config: Path, logs: Path) -> dict:
    python_path = shutil.which("python3") or sys.executable
    binary_dirs = [str(Path(python_path).parent)]
    for value in paths.values():
        binary_dirs.extend((str(Path(value).parent), str(Path(value).resolve().parent)))
    binary_dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    env = {"PATH": ":".join(dict.fromkeys(binary_dirs)),
           "FEISHU_CODEX_CONFIG": str(config)}
    return {"Label": str(data["launchd_label"]),
             "ProgramArguments": [python_path, str(ROOT / "bridge.py")],
             "WorkingDirectory": str(ROOT), "EnvironmentVariables": env,
             "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 10,
             "StandardOutPath": str(logs / "bridge.stdout.log"),
             "StandardErrorPath": str(logs / "bridge.stderr.log")}


def install(path: Path) -> None:
    data, _project, paths = check(path)
    label = str(data["launchd_label"])
    logs = ROOT / "logs"
    logs.mkdir(mode=0o700, exist_ok=True)
    path_to_plist = plist_path(label)
    path_to_plist.parent.mkdir(parents=True, exist_ok=True)
    plist = build_plist(data, paths, path, logs)
    if path_to_plist.exists():
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path_to_plist)],
                       capture_output=True, text=True, check=False)
    with path_to_plist.open("wb") as stream:
        plistlib.dump(plist, stream)
    path_to_plist.chmod(0o644)
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path_to_plist)],
                   check=True)
    print(f"已安装并启动：{label}")
    print(f"运行日志：{logs / 'bridge.stderr.log'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true", help="生成配置和 Codex 项目目录")
    group.add_argument("--check", action="store_true", help="检查本机配置但不安装服务")
    group.add_argument("--install", action="store_true", help="安装并启动 LaunchAgent")
    parser.add_argument("--config", help="配置文件路径，默认仓库根目录 config.json")
    args = parser.parse_args()
    path = config_path(args.config)
    try:
        if args.prepare:
            prepare(path)
        elif args.check:
            check(path)
        else:
            install(path)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"配置失败：{exc}\n")


if __name__ == "__main__":
    main()
