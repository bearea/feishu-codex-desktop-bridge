# Feishu to Codex Desktop Bridge

Experimental macOS bridge that routes Feishu bot messages into **native Codex Desktop chats**, keeps questions and answers in a local Codex project, and replies in Feishu. Follow-up messages reuse the same chat; `新会话：` starts a new one.

This repository implements the Feishu adapter only. The core pattern can be adapted to Slack, Teams, and other messaging platforms, but those adapters are **not implemented**. It uses an undocumented Codex Desktop IPC protocol and requires an app environment with `list_projects` and `move_thread_to_project` tools for automatic sidebar grouping. Cloning and filling in Feishu credentials alone does not guarantee the exact desktop experience.

See the [Chinese setup guide](README.md) for architecture, prerequisites, installation, validation, limitations, and troubleshooting.
