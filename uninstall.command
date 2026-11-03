#!/bin/zsh
# Run from the stable bridge checkout installed by the setup guide.
set -euo pipefail

bridge_dir=${0:A:h}
bridge_config=${FEISHU_CODEX_CONFIG:-$bridge_dir/config.json}

if [[ -f "$bridge_config" ]]; then
  python3 "$bridge_dir/scripts/setup.py" --uninstall --config "$bridge_config"
else
  print "未找到桥接配置：$bridge_config；如曾使用其他配置，请先指定 FEISHU_CODEX_CONFIG。"
  print "暂不移除插件，避免留下仍在运行的服务。"
  exit 1
fi

if ! command -v codex >/dev/null 2>&1; then
  print "桥接服务已卸载，但找不到 Codex CLI。请稍后手动移除插件。"
  exit 1
fi

codex plugin remove feishu-codex-desktop-bridge@feishu-codex-bridge
print "插件与桥接服务均已卸载；本地配置、问答记录和日志已保留。"
