# Feishu to Codex Desktop Bridge

Experimental macOS bridge that routes Feishu bot messages into **native Codex Desktop chats**, keeps questions and answers in a local Codex project, and replies in Feishu. Follow-up messages reuse the same chat; `新会话：` starts a new one.

This repository implements the Feishu adapter only. The core pattern can be adapted to Slack, Teams, and other messaging platforms, but those adapters are **not implemented**. It uses an undocumented Codex Desktop IPC protocol and the current app-server's `project/list`, `thread/start.projectId`, and `thread/metadata/update` methods for automatic sidebar grouping. It no longer needs a separate controller chat. Cloning and filling in Feishu credentials alone does not guarantee the exact desktop experience.

The optional local read profile can let bot chats inspect a specified product repository. General answers without private-source access can reply automatically after local checks. Answers backed by local files or other Codex chats wait for owner approval **in the same native Codex chat** before the bridge sends them to Feishu. Detected secrets and raw code are blocked outright. Automated filtering cannot guarantee that an answer contains no confidential facts. See the Chinese guide for the profile installer and test steps.

See the [Chinese setup guide](README.md) for architecture, prerequisites, installation, validation, limitations, and troubleshooting.
