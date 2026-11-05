"""Validate bot questions and bound automatic conversations in Feishu groups."""

from __future__ import annotations

import copy
import html
import re
import subprocess
import time
from dataclasses import dataclass


_OPEN_ID = re.compile(r"ou_[A-Za-z0-9_-]+\Z")
_CHAT_ID = re.compile(r"oc_[A-Za-z0-9_-]+\Z")
_MESSAGE_ID = re.compile(r"om_[A-Za-z0-9_-]+\Z")
_END_WORDS = {"收到", "好的", "好", "明白", "了解", "谢谢", "感谢", "多谢", "不用回复", "无需回复",
              "已解决", "问题已解决", "已完成", "结束", "再见", "ok", "thanks", "thank you", "done"}


def own_bot_id(profile: str) -> str:
    """Get the bot identity through the existing CLI; never read application secrets."""
    import json

    result = subprocess.run(["lark-cli", "--profile", profile, "auth", "status", "--json", "--verify"],
                            capture_output=True, text=True, timeout=25, check=False)
    if result.returncode:
        raise RuntimeError("could not verify the configured Feishu bot identity")
    try:
        bot = json.loads(result.stdout).get("identities", {}).get("bot", {})
        open_id = bot.get("openId")
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("Feishu bot identity response is invalid") from exc
    if (bot.get("status") != "ready" or bot.get("verified") is not True
            or not isinstance(open_id, str) or not _OPEN_ID.fullmatch(open_id)):
        raise RuntimeError("Feishu bot identity is not ready and verified")
    return open_id


def _ids(config: dict, key: str, pattern: re.Pattern) -> frozenset[str]:
    values = config.get(key) or []
    if not isinstance(values, list) or any(not isinstance(value, str) or not pattern.fullmatch(value)
                                           for value in values):
        raise ValueError(f"{key} must be a list of Feishu IDs")
    return frozenset(values)


def _number(config: dict, key: str, default: int, maximum: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(f"{key} must be an integer between 1 and {maximum}")
    return value


@dataclass(frozen=True)
class BotPolicy:
    enabled: bool = False
    self_open_id: str = ""
    allowed_chat_ids: frozenset[str] = frozenset()
    allowed_sender_ids: frozenset[str] = frozenset()
    max_turns: int = 3
    max_per_minute: int = 6
    topic_ttl: int = 1800

    @classmethod
    def from_config(cls, config: dict, self_open_id: str = "") -> BotPolicy:
        enabled = config.get("bot_messages_enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("bot_messages_enabled must be true or false")
        if self_open_id and not _OPEN_ID.fullmatch(self_open_id):
            raise ValueError("invalid bot open_id")
        return cls(enabled, self_open_id,
                   _ids(config, "bot_allowed_chat_ids", _CHAT_ID),
                   _ids(config, "bot_allowed_sender_ids", _OPEN_ID),
                   _number(config, "bot_max_turns_per_topic", 3, 20),
                   _number(config, "bot_max_messages_per_minute", 6, 60),
                   _number(config, "bot_topic_ttl_seconds", 1800, 86400))


def _body(event: dict) -> str:
    """Remove leading mentions using CLI metadata; never infer a real @ from text."""
    text = str(event.get("content") or "").strip()
    for _ in range(20):
        previous = text
        for mention in event.get("mentions") or []:
            if not isinstance(mention, dict):
                continue
            for prefix in (mention.get("key"), "@" + str(mention.get("name") or "")):
                if prefix and prefix != "@" and text.startswith(prefix):
                    rest = text[len(prefix):]
                    if not rest or rest[0].isspace() or rest[0] in "，,:：":
                        text = rest.lstrip(" \t\r\n，,:：")
        if previous == text:
            break
    return text


def is_terminal(text: str) -> bool:
    """Silence simple acknowledgements; a question containing these words still runs."""
    text = text.strip().lower()
    if text in ("[结束]", "[完成]"):
        return True
    parts = [part.strip() for part in re.split(r"[，,。.!！;；\n]+", text) if part.strip()]
    return bool(parts) and all(part in _END_WORDS for part in parts)


class BotChannel:
    def __init__(self, policy: BotPolicy, state: dict | None = None):
        self.policy = policy
        state = state or {}
        if not isinstance(state, dict) or state.get("version", 1) != 1:
            raise ValueError("invalid bot conversation state")
        self.topics = copy.deepcopy(state.get("topics", {}))
        self.messages = copy.deepcopy(state.get("messages", {}))
        self.recent = copy.deepcopy(state.get("recent", []))
        if not isinstance(self.topics, dict) or not isinstance(self.messages, dict) or not isinstance(self.recent, list):
            raise ValueError("invalid bot conversation state")

    def snapshot(self) -> dict:
        return {"version": 1, "topics": self.topics, "messages": self.messages, "recent": self.recent}

    def _prune(self, now: float) -> None:
        cutoff = now - self.policy.topic_ttl
        self.topics = {key: value for key, value in self.topics.items()
                       if isinstance(value, dict) and float(value.get("updated_at", 0)) >= cutoff}
        self.messages = {key: value for key, value in self.messages.items()
                         if isinstance(value, dict) and value.get("topic_key") in self.topics}
        self.recent = [item for item in self.recent if isinstance(item, dict)
                       and float(item.get("time", 0)) > now - 60]

    def prepare(self, event: dict, now: float | None = None) -> tuple[dict | None, str]:
        now = time.time() if now is None else now
        policy = self.policy
        if not policy.enabled or not policy.self_open_id:
            return None, "bot messages disabled or own bot identity unavailable"
        if event.get("type") != "im.message.receive_v1" or event.get("sender_type") != "bot":
            return None, "not a bot message"
        if event.get("chat_type") != "group" or event.get("message_type") not in ("text", "post"):
            return None, "only group text and post bot messages are supported"
        chat, sender, mid = (event.get(key) for key in ("chat_id", "sender_id", "message_id"))
        if not all(isinstance(value, str) and pattern.fullmatch(value)
                   for value, pattern in ((chat, _CHAT_ID), (sender, _OPEN_ID), (mid, _MESSAGE_ID))):
            return None, "invalid bot message identifiers"
        if sender == policy.self_open_id:
            return None, "own bot message"
        if policy.allowed_chat_ids and chat not in policy.allowed_chat_ids:
            return None, "group is not enabled for bot conversations"
        if policy.allowed_sender_ids and sender not in policy.allowed_sender_ids:
            return None, "bot is not enabled for conversations"
        mentions = event.get("mentions") or []
        if not isinstance(mentions, list) or not any(isinstance(item, dict) and item.get("id") == policy.self_open_id
                                                     for item in mentions):
            return None, "bot question must explicitly mention this bot"
        text = _body(event)
        if not text or len(text) > 20000:
            return None, "empty or oversized bot question"
        self._prune(now)
        if mid in self.messages:
            return None, "duplicate bot message"
        root = event.get("root_id") or event.get("reply_to") or mid
        if not isinstance(root, str) or not _MESSAGE_ID.fullmatch(root):
            return None, "invalid bot reply context"
        for reference in (event.get("root_id"), event.get("reply_to")):
            linked = self.messages.get(reference) if isinstance(reference, str) else None
            if linked and linked.get("chat_id") == chat and linked.get("sender_id") == sender:
                root = self.topics[linked["topic_key"]]["root_id"]
                break
        topic_key = f"bot:{chat}:{sender}:{root}"
        topic = self.topics.get(topic_key, {})
        if is_terminal(text):
            if topic:
                topic["closed"] = True
            return None, "bot acknowledgement or conversation ended"
        if topic.get("closed") or int(topic.get("turns", 0)) >= policy.max_turns:
            return None, "bot topic automatic reply limit reached"
        if sum(item.get("sender_id") == sender for item in self.recent) >= policy.max_per_minute:
            return None, "bot question rate limit reached"
        return {"source_type": "bot", "chat_type": "group", "message_type": event["message_type"],
                "chat_id": chat, "sender_id": sender,
                "sender_name": " ".join(str(event.get("sender_name") or "对方机器人").split())[:40],
                "message_id": mid, "bot_reply_to": mid, "root_id": root, "reply_to": event.get("reply_to"),
                "thread_id": event.get("thread_id"), "bot_topic_key": topic_key,
                "question": text, "answer": None, "create_time": str(event.get("create_time") or int(now * 1000)),
                "received_at": now, "sent": False}, "accepted"

    def register(self, record: dict, now: float | None = None) -> None:
        """Call after saving the outbox; replaying a pending record is idempotent."""
        now = time.time() if now is None else now
        self._prune(now)
        mid = record["message_id"]
        if mid in self.messages:
            return
        key = record["bot_topic_key"]
        topic = self.topics.setdefault(key, {"root_id": record["root_id"], "turns": 0})
        topic["turns"] += 1
        topic["updated_at"] = now
        topic.update(chat_id=record["chat_id"], sender_id=record["sender_id"], last_incoming_id=mid)
        self.messages[mid] = {"topic_key": key, "chat_id": record["chat_id"], "sender_id": record["sender_id"]}
        self.recent.append({"sender_id": record["sender_id"], "time": now})

    def delivered(self, record: dict, outgoing_id: str, now: float | None = None) -> None:
        if not record.get("manual_reply"):
            self.register(record, now)
        self.messages[outgoing_id] = {"topic_key": record["bot_topic_key"], "chat_id": record["chat_id"],
                                      "sender_id": record["sender_id"]}


def reply_text(record: dict, answer: str) -> str:
    sender = record["sender_id"]
    if not isinstance(sender, str) or not _OPEN_ID.fullmatch(sender):
        raise ValueError("invalid reply bot ID")
    name = html.escape(str(record.get("sender_name") or "对方机器人"), quote=True)
    # Only the configured recipient may be @mentioned by an automatic bot reply.
    answer = re.sub(r"<\s*/?\s*at\b[^>]*>", "", answer, flags=re.I)
    return f'<at user_id="{sender}">{name}</at> {answer}'
