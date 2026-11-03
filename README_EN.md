# Feishu to Codex Desktop Bridge

Experimental macOS bridge that routes Feishu bot messages into **native Codex Desktop chats**, keeps questions and answers in a local Codex project, and replies in Feishu. Follow-up messages reuse the same chat; `新会话：` starts a new one.

This repository implements the Feishu adapter only. The core pattern can be adapted to Slack, Teams, and other messaging platforms, but those adapters are **not implemented**. It uses an undocumented Codex Desktop IPC protocol and requires an app environment with `list_projects` and `move_thread_to_project` tools for automatic sidebar grouping. Cloning and filling in Feishu credentials alone does not guarantee the exact desktop experience.

The optional local read profile can let new bot chats inspect a specified product repository. Substantive answers then wait for the owner to approve them in a Codex Desktop review chat before the bridge sends anything to Feishu. Detected secrets and raw code are blocked outright. This human review is required because automated filtering cannot guarantee that an answer contains no confidential facts. See the Chinese guide for the profile installer and test steps.

See the [Chinese setup guide](README.md) for architecture, prerequisites, installation, validation, limitations, and troubleshooting.
