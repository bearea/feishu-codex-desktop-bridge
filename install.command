#!/bin/zsh
# Install the setup plugin; its onboarding skill finishes Feishu and local setup.
set -euo pipefail

marketplace="feishu-codex-bridge"
plugin="feishu-codex-desktop-bridge@${marketplace}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "当前桥接只支持 macOS Codex 桌面版。"
  exit 1
fi
codex_bin="$(command -v codex || true)"
if [[ -z "${codex_bin}" ]]; then
  for candidate in \
    "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex" \
    "/Applications/Codex.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"; do
    if [[ -x "${candidate}" ]]; then
      codex_bin="${candidate}"
      break
    fi
  done
fi
if [[ -z "${codex_bin}" ]]; then
  print -u2 "请先安装并登录 Codex 桌面版，再重新执行安装。"
  exit 1
fi

if ! "${codex_bin}" plugin marketplace list | grep -E '^feishu-codex-bridge[[:space:]]' >/dev/null; then
  "${codex_bin}" plugin marketplace add bearea/feishu-codex-desktop-bridge
fi

if ! "${codex_bin}" plugin list | grep -E '^feishu-codex-desktop-bridge@feishu-codex-bridge[[:space:]]+installed' >/dev/null; then
  "${codex_bin}" plugin add "${plugin}"
fi

if ! open -b com.openai.codex; then
  print "插件已安装，但未能自动打开 Codex 桌面版。请手动打开应用。"
fi
print "插件已安装。请在 Codex 桌面版打开‘飞书接入 Codex 桌面版’，运行‘设置’向导。"
print "向导会带你完成飞书登录、机器人配置、后台服务安装和真实问答验收。"
