# Feishu → Codex Desktop Bridge / 飞书机器人接入 Codex 桌面会话

把飞书机器人收到的文字问题送进**本机 Codex Desktop 的原生会话**，在桌面项目中保留提问与答复，再由机器人回复飞书。私聊追问续接同一会话；`新会话：` 或 `/新会话 ` 新开会话。

> **当前状态：实验性 macOS 方案。** 已在一台 Mac 上用真实飞书私聊验证“新会话 → Codex 项目 → 桌面版生成 → 飞书回复”和同一会话追问。Codex Desktop 的本地 IPC 不是公开稳定接口；自动归入左侧项目还依赖该桌面环境提供 `list_projects` / `move_thread_to_project` 工具，并由一个 Codex 控制会话调用。仅克隆代码、填写飞书密钥，**不足以保证**在任何 Codex 安装上获得相同的项目显示效果。安装检查会验证已知本机前提，最后必须做真实消息验收。

## 这是什么项目

这是独立编写的 Python 桥接程序，**不是某个开源项目的直接安装或改名**。实现桌面 IPC 时参考了 [codex-web 的桌面 IPC 客户端](https://github.com/i-am-BT/codex-web/blob/main/desktop-ipc-client.mjs)所公开的协议线索；飞书桥接方案也对照了 [lark-codex-desktop-bridge](https://github.com/ezra-y/lark-codex-desktop-bridge) 等项目。代码仅使用 Python 标准库，运行时需要 Codex Desktop/CLI 与飞书官方 `lark-cli`。

本仓库先实现**飞书适配器**。核心思路适用于 Slack、Teams 等能接收消息并发回复的平台，但每个平台还需要单独实现事件接收、认证、用户/会话 ID、回信与权限处理；目前没有这些平台的适配器。

## 链路

```text
飞书用户 → im.message.receive_v1 → lark-cli event consume → bridge.py
  → 按飞书 chat_id / 群话题 / 发送者查找 Codex 会话
  → 新会话：Codex app-server 创建空会话并退出，Desktop 加载并取得写入权
  → 控制会话把新会话移入本地项目
  → desktop_ipc.py 向 Desktop 提交原始提问（真正的 userMessage）
  → Codex Desktop 生成答复，bridge.py 只读等待完成
  → lark-cli 以 bot 身份回复原飞书聊天
```

`bridge.py` 把飞书消息 ID、提问人、时间和问题原文写入 Codex 输入。桌面版自己生成答复；外部 `app-server` **不执行答复轮次**，避免它和桌面版争夺会话写入权。会话/消息映射与待发答复保存在本机 `.state/`（已被 Git 忽略），机器人回信带幂等键。没有额外的问答抄送。

## 前提

- macOS 上安装并登录 Codex Desktop，`codex` CLI 可从终端运行；桌面版可由 `com.openai.codex` bundle ID 启动（若不同，改 `codex_bundle_id`）。本机实测组合是 Python 3.12、Node 24、`lark-cli` 1.0.96 和 Codex CLI 0.166.0-alpha.5.1；这些是实测版本，非兼容性承诺。
- 当前 Codex 桌面环境能使用 `list_projects`、`move_thread_to_project` 会话工具。普通 CLI 或独立 `app-server` 本身不能完成这里的**桌面左侧项目实时归属**。如果没有这两个工具，当前版本不能复现完整体验。
- 一台保持用户登录会话、网络和本机后台服务运行的 Mac。无需公网 webhook 域名。
- 飞书企业自建应用，开启机器人能力，允许目标用户访问。

## 飞书应用配置

1. 在[飞书开放平台](https://open.feishu.cn/app)创建或选择企业自建应用，开启**机器人**。
2. 在“事件与回调”选择**长连接接收事件**，订阅 `im.message.receive_v1`。
3. 开启与当前实现相符的应用身份权限：私聊接收 `im:message.p2p_msg:readonly`、机器人发送 `im:message:send_as_bot`。如需群里 @ 机器人，再开启 `im:message.group_at_msg.include_bot:readonly`；如需标题显示提问人姓名，开启 `contact:user.base:readonly` 和按飞书后台要求的通讯录基础信息权限 `contact:contact.base:readonly`，并设置可访问的数据范围。
4. 按飞书的版本审核/发布流程让权限与可用人员生效。**后台勾选权限不等于已对所有员工生效。** 群聊及其他员工的端到端效果尚未在当前实例验证。
5. 安装飞书 CLI 并为应用创建独立 profile。App Secret 由 CLI 安全配置保存，**不要**写进本仓库的 `config.json`、启动脚本或命令行参数。

```bash
npm install -g @larksuite/cli
lark-cli config init --name futou_bot
lark-cli --profile futou_bot auth status --json --verify
```

最后一条应显示目标 `appId`，bot 身份 `status: ready` 且 `verified: true`。桥接用 `--as bot`；用户身份 `auth login` 并非必需。若复用已有应用，可查看 `lark-cli config init --help` 的 `--app-id`、`--app-secret-stdin` 选项。

## 本机安装

```bash
git clone https://github.com/bearea/feishu-codex-desktop-bridge.git
cd feishu-codex-desktop-bridge
python3 scripts/setup.py --prepare
```

`--prepare` 生成未跟踪的 `config.json` 和 `feishu-message/AGENTS.md`，并打印**归项目控制会话**的提示词。接着：

1. 在 Codex Desktop 把打印出的 `feishu-message` 绝对路径添加为**本地项目**。
2. 新建一个独立的 Codex 控制会话，把脚本打印的提示词发给它。该会话只处理 `bind_project` JSON，不回答飞书问题。取得此会话的 thread ID，填入 `config.json` 的 `router_thread_id`。这是每台 Mac 自己的 ID，不能复制他人的。
3. 检查 `config.json` 的 `lark_profile`、`project_dir`、`bot_display_name`、`model`、`codex_bundle_id` 与自己的环境一致。`owner_open_id`、`owner_display_name` 仅用于姓名查询失败时的标题回退；它们**不是**发信人白名单。
4. 验证配置并安装 macOS LaunchAgent：

```bash
python3 scripts/setup.py --check
python3 scripts/setup.py --install
```

`--check` 验证 CLI、飞书 bot profile、项目目录与 Desktop IPC socket；它无法代替第 2 步的工具权限确认或真实消息验收。`--install` 会生成 `~/Library/LaunchAgents/<launchd_label>.plist`，后台启动桥接，并将日志写到仓库 `logs/`。同一飞书应用只运行**一个**桥接进程。修改代码后可执行 `launchctl kickstart -k "gui/$(id -u)/<launchd_label>"` 重启。

## 使用与验收

1. 飞书发 `新会话：某审批单列表和详情状态不一致，应该先核对什么？`。
2. 在 Codex Desktop 的 `feishu-message` 项目里找到新会话，确认提问原文先于最终答复出现；答复完成后飞书收到同一内容。
3. 再发一条普通追问，确认它追加到**同一个 Codex 会话**，且飞书收到第二次答复。
4. 用另一名员工及群聊重复测试（如果你打算开放这些场景）。目前仓库所依据的实测只覆盖应用所有者私聊。

本机实测新会话从飞书发出到 Codex 开始生成约 **15 秒**，并非瞬间。控制会话与项目归属步骤是主要新增延时。生成中的后续追问已在 Codex 状态中观察到 `inProgress`；不同桌面版本的可见动画仍应由使用者验收。

## 故障定位

| 现象 | 先检查 |
| --- | --- |
| 飞书发出后无反应 | `lark-cli --profile <profile> event status --json`、`logs/bridge.stderr.log`、长连接、事件订阅、应用发布与可用人员 |
| 收到事件但没有桌面会话 | Codex Desktop 是否启动、`~/.codex/ipc/ipc.sock` 是否存在、控制会话 ID 是否正确 |
| 会话在“最近”，不在项目 | 控制会话是否能调用 `list_projects` / `move_thread_to_project`，项目是否是本地项目，是否实际读回正确 projectId |
| Codex 提示“已在另一个应用中打开” | 是否有其他 CLI/app-server 进程在写同一会话；本程序只让外部 app-server 建空会话和只读结果 |
| Codex 有答复但飞书没收到 | `logs/bridge.stderr.log`、bot 发消息权限、`.state/pending_audits.json`；服务重启后会尝试重发 |

请勿把 `config.json`、`.state/` 或 `logs/` 提交到公开仓库。`project/AGENTS.md` 是模型行为约束，**不是严格的权限边界**；飞书应用可用人员应由管理员控制，项目目录不要放凭证。

## 官方接口与版本风险

官方 [Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)提供外部客户端能力，但桌面版会启动自己的 app-server。[写入权问题](https://github.com/openai/codex/issues/37403)和[桌面项目归属问题](https://github.com/openai/codex/issues/54482)解释了为什么单纯使用独立 app-server 不能保证当前体验。这里使用的 `~/.codex/ipc/ipc.sock` 及 `thread-follower-start-turn` 是**未公开、可能随桌面版更新而变化**的协议。每次升级 Codex Desktop 后，都应重新执行上面的真实消息验收。

## 开发与许可

本项目代码使用 Python 标准库。运行本地检查：

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q bridge.py desktop_ipc.py scripts/setup.py
```

MIT License。欢迎为其他消息平台实现适配器；目前发行版仅支持飞书。
