# Feishu → Codex Desktop Bridge / 飞书机器人接入 Codex 桌面会话

把飞书机器人收到的提问送进**本机 Codex Desktop 的原生会话**，在桌面项目中保留提问与答复，再由机器人回复飞书。私聊追问续接同一会话；`新会话：问题`、`重新开始：问题` 等明确表达会新开会话。文档评论中明确 @ 机器人时，可选的评论通道会把评论和机器人有权读取的 Docx 正文送入独立 Codex 会话。机主也可以在对应聊天会话中明确要求把一条回复发给飞书提问人。

> **当前状态：实验性 macOS 方案。** 已在一台 Mac 上用真实飞书私聊验证“新会话 → Codex 项目 → 桌面版生成 → 飞书回复”和同一会话追问。Codex Desktop 的本地 IPC 不是公开稳定接口；自动归入左侧项目依赖当前 Codex app-server 的 `project/list`、`thread/start.projectId` 和 `thread/metadata/update`。仅克隆代码、填写飞书密钥，**不足以保证**在任何 Codex 安装上获得相同的项目显示效果。安装检查会验证已知本机前提，最后必须做真实消息验收。

## 向导式安装预览

仓库现已包含一个 **Codex 本地插件的安装 Skill**。它会逐步检查本机、调用飞书官方 CLI 的新应用引导、在可用的浏览器自动化环境中配置机器人和消息权限、安装后台桥接，并做真实问答验收。插件是安装向导；消息到来时持续运行的仍是本机桥接服务。

在 Mac 终端执行一条命令安装插件：

```bash
curl -fsSL https://raw.githubusercontent.com/bearea/feishu-codex-desktop-bridge/main/install.command | zsh
```

该脚本只调用 Codex CLI 安装本仓库插件并打开 Codex；飞书应用创建、授权和首次验收由随后打开的设置向导继续完成。向导会把常驻桥接程序放到稳定的本机目录，不让服务依赖可能随插件升级变化的缓存路径。也可以手动执行等价的两条命令：

```bash
codex plugin marketplace add bearea/feishu-codex-desktop-bridge
codex plugin add feishu-codex-desktop-bridge@feishu-codex-bridge
```

然后在 Codex 桌面版运行插件的“设置”向导。若当前桌面版未自动显示新插件，重新打开 Codex。这个向导**尚未在全新 Mac 和新飞书企业中完成端到端验收**，目前不能承诺单击安装即完全可用。飞书扫码登录、应用创建页面的本人确认、企业管理员审批以及首次授权必须由相应的人完成；向导会在这些节点继续等待或给出下一步。`lark-cli config init --new --name <profile>` 可引导创建新应用并安全保存凭证，但它不代替机器人能力、事件订阅和应用发布的后台配置。以下手动步骤仍可作为排障与回退路径。

## 这是什么项目

这是独立编写的 Python 桥接程序，**不是某个开源项目的直接安装或改名**。实现桌面 IPC 时参考了 [codex-web 的桌面 IPC 客户端](https://github.com/i-am-BT/codex-web/blob/main/desktop-ipc-client.mjs)所公开的协议线索；飞书桥接方案也对照了 [lark-codex-desktop-bridge](https://github.com/ezra-y/lark-codex-desktop-bridge) 等项目。代码仅使用 Python 标准库，运行时需要 Codex Desktop/CLI 与飞书官方 `lark-cli`。

本仓库先实现**飞书适配器**。核心思路适用于 Slack、Teams 等能接收消息并发回复的平台，但每个平台还需要单独实现事件接收、认证、用户/会话 ID、回信与权限处理；目前没有这些平台的适配器。

## 链路

```text
飞书私聊/群聊 → im.message.receive_v1 ─┐
云文档评论 @机器人 → drive.notice.comment_add_v1 ├→ lark-cli event consume → bridge.py
                                     ┘
  → 按飞书 chat_id / 群话题 / 发送者查找 Codex 会话
  → 新会话：Codex app-server 直接在本地项目中创建空会话并退出，Desktop 加载并取得写入权
  → desktop_ipc.py 向 Desktop 提交原始提问（真正的 userMessage）
  → Codex Desktop 生成答复，bridge.py 只读等待完成
  → 个人信息和私人项目问题在进入 Codex 前直接拒答
  → 本机出站检查：凭证/个人信息/原始代码直接拦截；一般产品问答自动回复
  → 答复涉及敏感项目细节时，在原 Codex 会话内等待机主审核
  → 确需审核时先向飞书发送一次等待提示；自动通过或机主选择允许后发送答复
机主在对应 Codex 会话明确要求回复飞书
  → 本机语义识别 → 出站检查/必要时审核 → lark-cli 以 bot 身份发送到原飞书聊天
```

评论通道只处理 `is_mentioned=true` 且由用户发出的新评论或回复。普通编辑、协作者变化、机器人自身评论不会生成问答。局部评论直接在原评论下回复；飞书不允许给全文评论添加回复，桥接会另建一条标明原评论 ID、并 @ 提问人的全文评论；已解决评论等不能原地回复的情况改为私聊提问人。文档评论按“文档 + 评论 ID”建立独立 Codex 会话，同一评论下的后续 @ 续接此会话。

`bridge.py` 把飞书消息 ID、提问人、时间和问题原文写入 Codex 输入。桌面版自己生成答复；外部 `app-server` **不执行答复轮次**，避免它和桌面版争夺会话写入权。会话/消息映射与待发答复保存在本机 `.state/`（已被 Git 忽略），机器人回信带幂等键。没有额外的问答抄送。机主手动要求发给飞书时，`manual_reply.py` 使用一次不留侧边栏会话的 Codex 语义判断；明确发送意图和正文才会进入待发队列。普通讨论、草稿和含糊的表达不会外发。

## 前提

- macOS 上安装并登录 Codex Desktop，`codex` CLI 可从终端运行；桌面版可由 `com.openai.codex` bundle ID 启动（若不同，改 `codex_bundle_id`）。本机实测组合是 Python 3.12、Node 24、`lark-cli` 1.0.96 和 Codex CLI 0.166.0-alpha.5.1；这些是实测版本，非兼容性承诺。
- 当前 Codex app-server 支持 `project/list`、`thread/start.projectId` 和 `thread/metadata/update`。安装检查会验证项目已添加，真实消息验收会验证新会话归属；旧版本可能不支持。
- 一台保持用户登录会话、网络和本机后台服务运行的 Mac。无需公网 webhook 域名。
- 飞书企业自建应用，开启机器人能力，允许目标用户访问。

## 飞书应用配置

1. 在[飞书开放平台](https://open.feishu.cn/app)创建或选择企业自建应用，开启**机器人**。
2. 在“事件与回调”选择**长连接接收事件**，订阅 `im.message.receive_v1`。
3. 开启与当前实现相符的应用身份权限：私聊接收 `im:message.p2p_msg:readonly`、机器人发送 `im:message:send_as_bot`。如需群里 @ 机器人，再开启 `im:message.group_at_msg.include_bot:readonly`；如需标题显示提问人姓名，开启 `contact:user.base:readonly` 和按飞书后台要求的通讯录基础信息权限 `contact:contact.base:readonly`，并设置可访问的数据范围。
4. 按飞书的版本审核/发布流程让权限与可用人员生效。**后台勾选权限不等于已对所有员工生效。** 群聊及其他员工的端到端效果尚未在当前实例验证。
5. 安装飞书 CLI 并为应用创建独立 profile。App Secret 由 CLI 安全配置保存，**不要**写进本仓库的 `config.json`、启动脚本或命令行参数。

### 文档评论 @ 机器人（可选）

这部分**尚未在当前飞书应用做端到端验收**。应用目前仅有消息事件和权限；版本审核中的配置也会锁住后台新增权限与事件。要启用时，在开发者后台额外开通应用身份的 `docs:document.comment:read`、`docs:document.comment:create`、`docx:document:readonly`，订阅 `drive.notice.comment_add_v1`，按后台流程发布并让机器人取得测试文档的访问权。读取整篇文档是为了回答“总结一下”等问题；若仅需处理评论文字，可在代码里关闭 Docx 正文读取并去掉对应权限。CLI 或 API 报错时以实际 `missing_scopes` 和文档授权状态为准。权限范围由飞书应用与文档共同控制，桥接不会借用机主的用户身份读取机器人无权访问的文档。

当前稳定 `lark-cli` 1.0.96 还不认识此事件。本仓库提供临时构建脚本，从[飞书 CLI 的评论事件 PR](https://github.com/larksuite/cli/pull/2846)固定提交构建到仓库内 `.tools/`，不会覆盖系统 CLI。需安装 Go 与 Git；该 PR 更新后脚本会要求先审查新提交。上游正式发布支持该事件的 CLI 后，可直接改用正式版本。

```bash
bash scripts/build_comment_cli.sh
```

在私有 `config.json` 里设置 `"comment_events_enabled": true`，并将 `"event_cli"` 设为构建输出的**绝对路径**。然后执行 `python3 scripts/setup.py --check` 并重启桥接。评论事件消费会向飞书登记订阅；测试时在有权访问的 Docx 文档评论中明确 `@福头 ✨ 总结一下`，确认 `feishu-message` 项目先出现新会话，再检查文档里出现回复。旧评论事件不会因之后开通权限而自动补发，请重新 @ 一次。若评论读取权限暂不可用，该评论会保留在待处理队列并定时重试，私聊问答继续运行。

| 飞书动作 | 当前处理 |
| --- | --- |
| 私聊发文字、群里 @ 机器人 | 进入 Codex 原生会话并回到原聊天；群聊仍需对应权限和实测 |
| 私聊/群聊发图片、文件、音视频 | 建立 Codex 会话并说明附件正文尚不可读，请补文字；不猜测附件 |
| 云文档评论明确 @ 机器人 | 可选评论通道；Docx 可读正文，其它文档类型先只读取评论文字 |
| 点击自定义机器人菜单、操作交互卡片 | 目前机器人未配置菜单或发出卡片；若以后增加，需要按动作语义接入 `application.bot.menu_v6` 或 `card.action.trigger` |
| 文档修改、协作者增删、已读、群成员变化 | 不当作提问，不进入 Codex |

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

`--prepare` 生成未跟踪的 `config.json` 和 `feishu-message/AGENTS.md`。接着：

1. 在 Codex Desktop 把打印出的 `feishu-message` 绝对路径添加为**本地项目**。
2. 检查 `config.json` 的 `lark_profile`、`project_dir`、`bot_display_name`、`model`、`codex_bundle_id` 与自己的环境一致。`owner_open_id`、`owner_display_name` 仅用于姓名查询失败时的标题回退；它们**不是**发信人白名单。
3. 验证配置并安装 macOS LaunchAgent：

```bash
python3 scripts/setup.py --check
python3 scripts/setup.py --install
```

`--check` 验证 CLI、飞书 bot profile、本地项目登记与 Desktop IPC socket；它无法代替真实消息验收。`--install` 会生成 `~/Library/LaunchAgents/<launchd_label>.plist`，后台启动桥接，并将日志写到仓库 `logs/`。同一飞书应用只运行**一个**桥接进程。修改代码后可执行 `launchctl kickstart -k "gui/$(id -u)/<launchd_label>"` 重启。

## 重启与卸载

安装插件本身不会启动桥接；完成向导的 `--install` 后，macOS LaunchAgent 才会负责常驻运行。它设置了 `RunAtLoad` 和 `KeepAlive`：Mac 重启后，**用户登录图形会话时**自动启动，进程退出时由系统尝试拉起。关机期间或重启后尚未登录时不会处理飞书消息；登录后仍需网络、有效的飞书应用配置和可用的 Codex Desktop。请用真实飞书消息核对恢复，不把进程存在当成问答验收。

只在 Codex 里点击“卸载插件”或执行 `codex plugin remove`，**不会**自动停止独立安装的 LaunchAgent。请在向导使用的稳定桥接目录运行下面的统一卸载入口，它先停止并删除本项目所属的 LaunchAgent，再卸载插件：

```bash
./uninstall.command
```

也可以只停止桥接服务：`python3 scripts/setup.py --uninstall`。卸载会保留本机 `config.json`、`.state/`、`logs/`、Codex 项目和已有会话，便于恢复或单独清理；它不会删除飞书开发者后台的应用。若安装时使用了其他配置文件，运行前设置 `FEISHU_CODEX_CONFIG` 指向该文件。卸载脚本会核对 LaunchAgent 确实指向当前仓库，避免误删同名的其他服务。卸载完成后再手动删除稳定桥接目录，前提是已经备份或不再需要其中的数据。

## 试点读取本地产品资料

默认 `config.json` 中 `product_read_path` 与 `permission_profile` 为空，没有配置项目专用的读取范围。若要试点读取一个已有项目（目录需含 `README.md` 与 `src/`），先只验证权限配置，再安装。脚本只添加一个**显式选择**的命名权限配置，不改变普通 Codex 会话的默认模型、权限或批准策略；会先验证 Codex 配置与实际文件访问，并备份原配置，失败时恢复。

```bash
python3 scripts/install_read_profile.py --project /绝对路径/产品项目 --bot-project /绝对路径/feishu-message
python3 scripts/install_read_profile.py --project /绝对路径/产品项目 --bot-project /绝对路径/feishu-message --install
```

然后在私有 `config.json` 填入 `"permission_profile": "futou_nova_read"`、`"product_read_path": "/绝对路径/产品项目"`。如需让机器人检索其他 Codex 会话的**只读内容**，再设置 `"codex_history_read": true`。调整 `feishu-message/AGENTS.md`，明确只读资料和会话的用途，不允许把原始文件或会话内容直接放进答复。桥接会在每轮提问中显式提交命名权限，并在答复前核对 Desktop 实际执行记录；若 Desktop 未使用受限权限，就停止发送。设置后在飞书用 `新会话：...` 验证。只有 `list_threads`、`read_thread` 两种 Codex 会话只读工具被允许；其他 MCP、连接器和浏览器工具的调用会阻止答复发送。

试点期间，一般产品问答和不含敏感细节的项目概括通过安全检查后自动回复，即使这一轮读取了授权资料或其他 Codex 会话。问题或答复涉及具体内部地址、账号、连接串、配置值、表名或字段名等敏感项目细节时，在**同一条飞书问答对应的 Codex 会话**中请机主选择“允许发送”或“拒绝发送”；未选择时不回飞书。讨论看板指标、排查方法，或说明尚未核验数据库数据，不应仅因出现“数据库”一词而要求审核。个人信息和私人项目问题在进入 Codex 前直接拒答，不保存问题原文；带凭证、密钥、连接串、联系方式、证件号、整段代码或与工具读取结果较长逐字重合的答复直接拦截，批准也不能放行。审核弹框只在发起新审核时打开，等待选择时不抢桌面焦点。自动识别不能证明所有敏感信息已被发现；如果要求严格防止任何私人信息外发，应让人工审核覆盖更广的范围。Codex 读取本地文件或其他会话时，相关片段可能进入模型上下文；“不发回飞书”不等于模型完全离线运行。

答复可以按问题复杂度正常展开，但不发送源码、具体源码路径或文件和会话原文。默认使用 `gpt-6.1-sol`、`high` 思考档位，可在私有配置中调整 `model` 和 `reasoning_effort`。新会话首次由 Desktop 加载以取得写入权；同一会话的后续提问与审核直接送到已有 Desktop 会话，不再反复打开窗口。

机主可以在飞书对应的 Codex 会话里说“帮我回复他：请先提供页面截图和发生时间”或“把下面这段发给对方……”。程序只在该会话完成后识别机主消息，以“【机主回复】”标识发送到原飞书聊天；仍执行敏感内容检查，必要时在原 Codex 会话审核。语义识别可能判断错误，因此模糊表达会保留在 Codex，不自动发送。飞书客户端的“清空聊天记录”不会给本桥接提供重置会话的信号；单独发“重新开始”“换个话题”会重置，下一条问题新建 Codex 会话，也可以直接发“重新开始：新问题”。

## 使用与验收

1. 飞书发 `新会话：某审批单列表和详情状态不一致，应该先核对什么？`。
2. 在 Codex Desktop 的 `feishu-message` 项目里找到新会话，确认提问原文先于最终答复出现；答复完成后飞书收到同一内容。
3. 再发一条普通追问，确认它追加到**同一个 Codex 会话**，且飞书收到第二次答复。
4. 用另一名员工及群聊重复测试（如果你打算开放这些场景）。目前仓库所依据的实测只覆盖应用所有者私聊。
5. 在对应的 Codex 会话中明确要求回复飞书，并核对飞书收到带“机主回复”标识的消息；只请求草稿时不应发送。

新会话需要创建并由 Desktop 加载，飞书发出到 Codex 开始生成并非瞬间。生成中的后续追问已在 Codex 状态中观察到 `inProgress`；不同桌面版本的可见动画仍应由使用者验收。

## 故障定位

| 现象 | 先检查 |
| --- | --- |
| 飞书发出后无反应 | `lark-cli --profile <profile> event status --json`、`logs/bridge.stderr.log`、长连接、事件订阅、应用发布与可用人员 |
| 收到事件但没有桌面会话 | Codex Desktop 是否启动、`~/.codex/ipc/ipc.sock` 是否存在、`python3 scripts/setup.py --check` 是否通过 |
| 会话在“最近”，不在项目 | 项目是否已在 Desktop 添加、`project/list` 是否找到相同绝对路径、`thread/start` 返回的 projectId 是否正确 |
| Codex 提示“已在另一个应用中打开” | 是否有其他 CLI/app-server 进程在写同一会话；本程序只让外部 app-server 建空会话和只读结果 |
| Codex 有答复但飞书没收到 | `logs/bridge.stderr.log`、bot 发消息权限、`.state/pending_audits.json`；服务重启后会尝试重发 |
| Codex 已答复但飞书等待 | 打开该条飞书问答对应的 Codex 会话，检查待处理的“允许发送/拒绝发送”弹框；敏感项目细节需要人工选择 |

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
