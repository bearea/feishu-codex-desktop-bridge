#!/usr/bin/env python3
"""Install a narrowly scoped Codex profile without changing global defaults."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path


def quoted(path: Path) -> str:
    return json.dumps(str(path), ensure_ascii=False)


def fragment(project: Path, bot_project: Path, name: str,
             additional_projects: tuple[Path, ...] = ()) -> str:
    f = f"permissions.{name}"
    extra_roots = "".join(f"{quoted(path)} = true\n" for path in additional_projects)
    extra_files = "".join(
        f"{quoted(path / part)} = \"read\"\n"
        for path in additional_projects
        for part in ("README.md", "package.json", "pnpm-workspace.yaml", "knowledge", "src", "scripts", "tests")
    )
    return f'''\n# Feishu Codex bridge: selected only by its own threads.
[{f}.workspace_roots]
{quoted(project)} = true
{extra_roots}

[{f}.filesystem]
":minimal" = "read"
{quoted(bot_project)} = "read"
{quoted(project)} = "deny"
{quoted(project / 'README.md')} = "read"
{quoted(project / 'docs')} = "read"
{quoted(project / 'src')} = "read"
{extra_files}

[{f}.filesystem.":workspace_roots"]
"**/.env*" = "deny"
"**/*credential*" = "deny"
"**/*secret*" = "deny"
"**/*private*key*" = "deny"

[{f}.network]
enabled = false
'''


def validate(text: str, project: Path, bot_project: Path, name: str,
             additional_projects: tuple[Path, ...] = ()) -> None:
    tomllib.loads(text)
    with tempfile.TemporaryDirectory(prefix="futou-codex-config-") as directory:
        temp_home = Path(directory)
        (temp_home / "config.toml").write_text(text, encoding="utf-8")
        environment = dict(os.environ, CODEX_HOME=str(temp_home))
        check = subprocess.run(["codex", "app-server", "--strict-config", "--print-startup-config"],
            env=environment, text=True, capture_output=True, timeout=20)
        if check.returncode:
            raise RuntimeError(f"Codex rejected config: {check.stderr[-500:]}")
        for path, expected in ((project / "README.md", True),
                               (project / "pyproject.toml", False),
                               (project / ".env", False),
                               (bot_project / "AGENTS.md", True),
                               *((extra / "README.md", True) for extra in additional_projects)):
            if not path.exists():
                continue
            trial = subprocess.run(["codex", "sandbox", "-P", name, "--", "sh", "-c",
                'head -c 1 "$1" >/dev/null 2>&1', "x", str(path)],
                cwd=bot_project, env=environment, text=True, capture_output=True, timeout=20)
            if (trial.returncode == 0) != expected or "getcwd: cannot access" in trial.stderr:
                raise RuntimeError(f"Permission trial failed for {path.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--bot-project", required=True, type=Path)
    parser.add_argument("--name", default="futou_nova_read")
    parser.add_argument("--additional-project", action="append", type=Path, default=[])
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    project, bot_project = args.project.resolve(), args.bot_project.resolve()
    additional_projects = tuple(path.resolve() for path in args.additional_project)
    if not (project / "README.md").is_file() or not (project / "src").is_dir():
        raise SystemExit("project must have README.md and src/")
    if not (bot_project / "AGENTS.md").is_file():
        raise SystemExit("bot project must have AGENTS.md")
    if any(not (path / "README.md").is_file() or not (path / "src").is_dir()
           for path in additional_projects):
        raise SystemExit("each additional project must have README.md and src/")
    config = Path.home() / ".codex" / "config.toml"
    original = config.read_text(encoding="utf-8")
    parsed = tomllib.loads(original)
    if args.name in parsed.get("permissions", {}):
        raise SystemExit(f"profile already exists: {args.name}")
    merged = original.rstrip() + "\n" + fragment(project, bot_project, args.name, additional_projects)
    validate(merged, project, bot_project, args.name, additional_projects)
    if not args.install:
        print("独立配置验证通过；未改动 ~/.codex/config.toml")
        return
    backup = config.with_name("config.toml.futou-backup-" + dt.datetime.now().strftime("%Y%m%d%H%M%S"))
    shutil.copy2(config, backup)
    temporary = config.with_name("config.toml.futou-tmp")
    try:
        temporary.write_text(merged, encoding="utf-8")
        os.replace(temporary, config)
        validate(config.read_text(encoding="utf-8"), project, bot_project, args.name, additional_projects)
    except Exception:
        shutil.copy2(backup, config)
        raise
    print(f"已安装 {args.name}；原配置备份：{backup}")


if __name__ == "__main__":
    main()
