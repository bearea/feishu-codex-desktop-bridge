# Feishu to Codex Desktop Bridge

Experimental macOS bridge that routes Feishu bot messages into **native Codex Desktop chats**, keeps questions and answers in a local Codex project, and replies in Feishu. Follow-up messages reuse the same chat; `新会话：` starts a new one.

This repository implements the Feishu adapter only. The core pattern can be adapted to Slack, Teams, and other messaging platforms, but those adapters are **not implemented**. It uses an undocumented Codex Desktop IPC protocol and the current app-server's `project/list`, `thread/start.projectId`, and `thread/metadata/update` methods for automatic sidebar grouping. It no longer needs a separate controller chat. Cloning and filling in Feishu credentials alone does not guarantee the exact desktop experience.

The optional local read profile can let bot chats inspect a specified product repository. Read-only Codex chat history access can also be enabled. General product answers, including safe summaries of authorized sources, reply automatically after local checks. Sensitive project details wait for owner approval **in the same native Codex chat**. Personal/private-project requests are refused before reaching Codex; detected personal identifiers, secrets, and raw code are blocked outright. Automated filtering cannot guarantee that an answer contains no confidential facts. See the Chinese guide for the profile installer and test steps.

When an answer really needs review, the bot sends one waiting message to the originating Feishu chat. The owner can also write a clear natural-language request in the matching Codex chat to send a manual reply back to Feishu. An ephemeral Codex classification pass extracts only an explicit outgoing message; ambiguous requests and draft requests remain local. Clearing Feishu's visible chat history does not reset the bridge's chat ID mapping; send `重新开始` or `重新开始：新问题` to start a new Codex chat.

Answers may be as detailed as the question requires, but must not include source code, exact source paths, or verbatim file/chat excerpts. The default model is `gpt-6.1-sol` at `high` reasoning effort. Desktop loads each new chat once to acquire ownership; follow-up turns and reviews reuse that chat without repeated navigation.

See the [Chinese setup guide](README.md) for architecture, prerequisites, installation, validation, limitations, and troubleshooting.
