---
name: feishu-codex-setup
description: Install, verify, diagnose, or uninstall a Feishu bot that routes questions to native Codex Desktop chats on the user's Mac.
---

# 飞书机器人接入 Codex 桌面版

目标是完成可验收的链路：飞书私聊提问 → 本机 Codex 项目立即出现原生会话和提问 → 桌面版生成答案 → 飞书收到答复。安装不等于验收；最后必须使用安装者自己的飞书账号发送真实问题，并核对两端。

本 Skill 随插件分发，但插件缓存目录可能在更新时改变。首次安装时把公开仓库克隆到稳定的本机目录（默认 `~/Library/Application Support/FeishuCodexDesktopBridge`，用户已有合适工作区时沿用），并只从稳定目录执行 `scripts/setup.py`、读取 `README.md`、安装 LaunchAgent。不要把常驻服务指向插件缓存目录。始终使用中文沟通。

## 先识别当前状态

确认 macOS、Codex Desktop 已安装并登录、`codex`、Python 3.11+、Node/npm 可用。缺少 Python 或 Node 时，优先用已安装的 Homebrew 补齐；若系统需要管理员密码或安装器确认，让用户完成该系统步骤。检查现有 `config.json`、飞书 CLI profile、Codex 项目和 LaunchAgent。若已有运行中的桥接，沿用它并补齐缺失步骤；不得覆盖已有飞书应用配置或另建同名服务。

读取项目 README 的安装、权限、验收和版本风险说明。项目配置、日志与 `.state/` 含私有数据，保持在本机且不提交仓库。App Secret 仅交给飞书 CLI 的安全存储，勿放入聊天、命令行参数、项目配置或日志。

## 引导安装

1. 若没有可沿用的桥接仓库，将 `https://github.com/bearea/feishu-codex-desktop-bridge.git` 克隆到上述稳定目录。若目录已存在，先确认它属于该项目；保留本地私有配置和运行状态，不执行覆盖或清理。若缺少飞书 CLI，使用官方安装方法 `npx @larksuite/cli@latest install`，确认 `lark-cli` 可用。机器人只需要应用身份；不要为此请求额外的用户 OAuth 权限。
2. 若用户要新建机器人，使用不会覆盖现有配置的**新 profile 名**运行 `lark-cli config init --new --name <profile>`。在后台运行并读取其验证链接；原样展示链接，并用 `lark-cli auth qrcode <原样链接> --output <相对路径>` 生成二维码给用户。使用者完成扫码登录、企业选择和必须由本人确认的步骤。等命令完成后，以 `lark-cli --profile <profile> auth status --json --verify` 核对 App ID 与 bot 身份。若用户已有应用，用 `lark-cli config init --name <profile>` 的现有应用流程，不新建应用。
3. 使用可用的浏览器自动化进入该 App ID 的飞书开发者后台，打开机器人能力，设置长连接接收事件并订阅 `im.message.receive_v1`，申请私聊接收 `im:message.p2p_msg:readonly` 和以机器人身份发送 `im:message:send_as_bot`。只有用户要求群聊时才加群消息权限；只有需要显示提问人姓名时才加所需通讯录只读权限。每次页面变化后读取实际状态，不凭旧截图或硬编码坐标操作。若没有浏览器自动化能力，逐项给出可直接打开的后台页面并继续完成其余自动步骤。
4. 创建版本并提交必要的企业审核；如企业要求扫码、管理员审批或选择可用范围，由用户或企业管理员完成。不能绕过审核，也不能把“已提交”说成“已发布”。审核期间记录 App ID 与版本状态，允许稍后从本步骤继续。
5. 运行 `python3 scripts/setup.py --prepare`，以该 App 的 profile 和用户选择的机器人名称写入本机 `config.json`；不要修改其他 Codex 会话的默认权限或配置。将生成的 `feishu-message` 路径加入 Codex Desktop 本地项目，优先通过可用的 Codex 项目工具或桌面操作完成。
6. 运行 `python3 scripts/setup.py --check`；检查通过后运行 `python3 scripts/setup.py --install`，核对 LaunchAgent 正在运行、日志显示飞书事件监听已就绪。若检查失败，按具体错误修复并重试，不跳过检查。
7. 让安装者在飞书给新机器人发一条产品测试问题。核对 Codex 项目中的新会话、原始提问、生成状态、最终答复以及飞书回信。再发追问验证续接；用“重新开始”验证新建会话。未完成这些观察时如实说明尚未验收。

本机资料读取和跨 Codex 会话读取是可选的高级配置，默认关闭。只有用户明确选择本地资料目录后才运行 `scripts/install_read_profile.py`，先只检查再安装，随后复核实际只读范围和敏感答复审核。私人项目、凭证和原文外发限制不得因安装自动化而关闭。

## 可靠性边界

飞书后台 UI 和 Codex Desktop 本地 IPC 可能变化；遇到不匹配时停止对应自动操作，读取当前页面或错误并定位。保留已完成的安装状态，避免重复创建应用、重复提交版本或启动两个监听同一应用的服务。macOS 用户会话、网络与本机桥接需要持续运行，Skill 本身不是常驻服务。

## 卸载

若用户要求卸载整个方案，在稳定桥接目录运行 `./uninstall.command`，先停止并移除 LaunchAgent，再卸载 Codex 插件。只移除插件不会自动移除独立服务。卸载前检查当前配置路径和 LaunchAgent 归属；失败时说明残留状态，不要声称已完全卸载。默认保留本机配置、消息状态、日志、Codex 会话与飞书应用；只有用户明确要求清理这些数据时才另行处理。
