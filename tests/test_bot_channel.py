"""Bot routing and loop protection without Feishu or Codex calls."""

import unittest
import json
from unittest.mock import patch

from bot_channel import BotChannel, BotPolicy, is_terminal, own_bot_id, reply_text


def event(**changes):
    value = {"type": "im.message.receive_v1", "sender_type": "bot", "chat_type": "group",
             "message_type": "text", "chat_id": "oc_group", "sender_id": "ou_peer",
             "sender_name": "同事助手", "message_id": "om_first", "content": "@福头 如何使用审批查询？",
             "create_time": "1000000", "mentions": [{"id": "ou_self", "name": "福头", "key": "@_user_1"}]}
    value.update(changes)
    return value


def channel(**config):
    return BotChannel(BotPolicy.from_config({"bot_messages_enabled": True, **config}, "ou_self"))


class BotChannelTests(unittest.TestCase):
    def test_own_identity_is_obtained_as_bot_and_requires_verified_result(self):
        output = {"identities": {"bot": {"status": "ready", "verified": True, "openId": "ou_self"}}}
        result = type("Result", (), {"returncode": 0, "stdout": json.dumps(output)})()
        with patch("bot_channel.subprocess.run", return_value=result) as run:
            self.assertEqual(own_bot_id("my_bot"), "ou_self")
        self.assertIn("--verify", run.call_args.args[0])
        self.assertIn("my_bot", run.call_args.args[0])
        output["identities"]["bot"]["verified"] = False
        result.stdout = json.dumps(output)
        with patch("bot_channel.subprocess.run", return_value=result):
            with self.assertRaises(RuntimeError):
                own_bot_id("my_bot")

    def test_accepts_actual_bot_mention_and_removes_display_prefix(self):
        record, reason = channel().prepare(event(), now=1000)
        self.assertEqual(reason, "accepted")
        self.assertEqual(record["question"], "如何使用审批查询？")
        self.assertEqual(record["source_type"], "bot")

    def test_textual_mention_cannot_substitute_for_platform_mention(self):
        for mentions in ([], [{"id": "ou_someone_else", "name": "福头"}], "ou_self"):
            with self.subTest(mentions=mentions):
                self.assertIsNone(channel().prepare(event(mentions=mentions))[0])

    def test_own_bot_humans_direct_messages_and_files_are_not_bot_questions(self):
        for change in ({"sender_id": "ou_self"}, {"sender_type": "user"}, {"chat_type": "p2p"},
                       {"message_type": "file"}, {"sender_id": "peer"}, {"chat_id": "not_a_chat"}):
            with self.subTest(change=change):
                self.assertIsNone(channel().prepare(event(**change))[0])

    def test_disabled_or_unresolved_identity_never_processes_bot_messages(self):
        self.assertIsNone(BotChannel(BotPolicy()).prepare(event())[0])
        self.assertIsNone(BotChannel(BotPolicy(enabled=True)).prepare(event())[0])

    def test_group_and_bot_allowlists_both_apply(self):
        receiver = channel(bot_allowed_chat_ids=["oc_group"], bot_allowed_sender_ids=["ou_peer"])
        self.assertIsNotNone(receiver.prepare(event())[0])
        self.assertIsNone(receiver.prepare(event(chat_id="oc_other"))[0])
        self.assertIsNone(receiver.prepare(event(sender_id="ou_other"))[0])

    def test_reply_to_our_outgoing_message_reuses_same_topic(self):
        receiver = channel()
        first, _ = receiver.prepare(event(), now=1000)
        receiver.register(first, now=1000)
        receiver.delivered(first, "om_our_reply", now=1001)
        second, _ = receiver.prepare(event(message_id="om_second", reply_to="om_our_reply"), now=1002)
        self.assertEqual(first["bot_topic_key"], second["bot_topic_key"])

    def test_identical_reply_reference_does_not_mix_different_bots_or_groups(self):
        receiver = channel()
        first, _ = receiver.prepare(event(), now=1000)
        receiver.delivered(first, "om_our_reply", now=1001)
        for change in ({"sender_id": "ou_other"}, {"chat_id": "oc_other"}):
            record, _ = receiver.prepare(event(message_id="om_second", reply_to="om_our_reply", **change), now=1002)
            self.assertNotEqual(first["bot_topic_key"], record["bot_topic_key"])

    def test_duplicate_and_recovery_do_not_consume_additional_turns(self):
        receiver = channel()
        record, _ = receiver.prepare(event(), now=1000)
        receiver.register(record, now=1000)
        receiver.register(record, now=1001)
        self.assertEqual(receiver.topics[record["bot_topic_key"]]["turns"], 1)
        restored = BotChannel(receiver.policy, receiver.snapshot())
        self.assertIsNone(restored.prepare(event(), now=1002)[0])

    def test_automatic_conversation_stops_after_configured_number_of_turns(self):
        receiver = channel(bot_max_turns_per_topic=2)
        for i in range(2):
            record, _ = receiver.prepare(event(message_id=f"om_{i}", root_id="om_root"), now=1000 + i)
            self.assertIsNotNone(record)
            receiver.register(record, now=1000 + i)
        self.assertIsNone(receiver.prepare(event(message_id="om_last", root_id="om_root"), now=1003)[0])
        self.assertIsNotNone(receiver.prepare(event(message_id="om_new_topic"), now=1003)[0])

    def test_rate_limit_covers_different_topics_from_the_same_bot(self):
        receiver = channel(bot_max_messages_per_minute=2)
        for i in range(2):
            record, _ = receiver.prepare(event(message_id=f"om_{i}"), now=1000 + i)
            receiver.register(record, now=1000 + i)
        self.assertIsNone(receiver.prepare(event(message_id="om_third"), now=1003)[0])
        self.assertIsNotNone(receiver.prepare(event(message_id="om_later"), now=1061)[0])

    def test_terminal_message_closes_topic_without_answering(self):
        receiver = channel()
        record, _ = receiver.prepare(event(), now=1000)
        receiver.register(record, now=1000)
        receiver.delivered(record, "om_reply", now=1001)
        ending = event(message_id="om_end", reply_to="om_reply", content="@福头 谢谢，问题已解决。")
        self.assertIsNone(receiver.prepare(ending, now=1002)[0])
        self.assertIsNone(receiver.prepare(event(message_id="om_again", root_id="om_first"), now=1003)[0])

    def test_terminal_words_embedded_in_a_question_do_not_close_topic(self):
        self.assertFalse(is_terminal("状态显示已解决，为什么还在列表里？"))
        self.assertIsNotNone(channel().prepare(event(content="@福头 状态显示已解决，为什么还在列表里？"))[0])

    def test_bot_reply_mentions_only_verified_sender(self):
        record, _ = channel().prepare(event(sender_name='<at user_id="all">所有人</at>'))
        text = reply_text(record, '<at user_id="all">所有人</at> 请查看说明')
        self.assertEqual(text.count('<at user_id="'), 1)
        self.assertTrue(text.startswith('<at user_id="ou_peer">'))
        self.assertNotIn('<at user_id="all">', text)

    def test_state_does_not_keep_question_or_reply_body(self):
        receiver = channel()
        record, _ = receiver.prepare(event(), now=1000)
        receiver.register(record, now=1000)
        self.assertNotIn(record["question"], str(receiver.snapshot()))

    def test_manual_reply_preserves_routing_without_consuming_automatic_turn(self):
        receiver = channel()
        record, _ = receiver.prepare(event(), now=1000)
        receiver.register(record, now=1000)
        manual = {**record, "message_id": "manual:local_turn", "manual_reply": True}
        receiver.delivered(manual, "om_manual", now=1001)
        self.assertEqual(receiver.topics[record["bot_topic_key"]]["turns"], 1)
        self.assertEqual(receiver.topics[record["bot_topic_key"]]["last_incoming_id"], "om_first")

    def test_invalid_configuration_fails_instead_of_disabling_limits(self):
        for config in ({"bot_messages_enabled": "false"}, {"bot_max_turns_per_topic": 0},
                       {"bot_max_turns_per_topic": True}, {"bot_allowed_sender_ids": "ou_peer"},
                       {"bot_allowed_chat_ids": ["wrong"]}):
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    BotPolicy.from_config(config, "ou_self")


if __name__ == "__main__":
    unittest.main()
