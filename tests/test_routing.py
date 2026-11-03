"""Check the routing and duplicate-turn rules without using live services."""

import unittest
from unittest.mock import patch

import bridge


class RoutingTests(unittest.TestCase):
    def test_natural_new_session_commands(self):
        self.assertEqual(bridge.new_session_request("重新开始"), (True, ""))
        self.assertEqual(bridge.new_session_request("换个话题：报告怎么更新？"),
                         (True, "报告怎么更新？"))
        self.assertEqual(bridge.new_session_request("报告怎么更新？"),
                         (False, "报告怎么更新？"))

    def test_reset_command_clears_mapping_without_asking_codex(self):
        event = {"type": "im.message.receive_v1", "message_id": "om_reset1234",
                 "sender_type": "user", "chat_type": "p2p", "message_type": "text",
                 "chat_id": "oc_person", "sender_id": "ou_person", "content": "重新开始"}
        threads = {"oc_person": "old-thread"}
        outbox = {}
        with patch.object(bridge, "save_private_json"), \
             patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "flush_outbox") as flush, \
             patch.object(bridge, "ask_codex") as ask:
            bridge.handle(event, set(), outbox, threads)
        self.assertFalse(threads)
        self.assertTrue(outbox["om_reset1234"]["control_ack"])
        self.assertIn("新会话", outbox["om_reset1234"]["answer"])
        flush.assert_called_once()
        ask.assert_not_called()

    def test_private_chat_reuses_chat_id(self):
        first = {"chat_type": "p2p", "chat_id": "oc_person", "sender_id": "ou_person",
                 "message_id": "om_first"}
        second = {**first, "message_id": "om_second"}
        self.assertEqual(bridge.thread_key(first), bridge.thread_key(second))

    def test_group_topics_and_senders_stay_separate(self):
        base = {"chat_type": "group", "chat_id": "oc_group", "root_id": "om_topic",
                "sender_id": "ou_one", "message_id": "om_reply"}
        self.assertNotEqual(bridge.thread_key(base), bridge.thread_key({**base, "sender_id": "ou_two"}))
        self.assertNotEqual(bridge.thread_key(base), bridge.thread_key({**base, "root_id": "om_other"}))

    def test_message_id_matches_only_original_user_turn(self):
        turns = [
            {"id": "t1", "items": [{"type": "userMessage", "content": [
                {"type": "text", "text": "飞书消息 ID：om_first\n\n问题"}]}]},
            {"id": "t2", "items": [{"type": "userMessage", "content": [
                {"type": "text", "text": "飞书消息 ID：om_second\n\n追问"}]}]},
        ]
        self.assertEqual(bridge.find_message_turn(turns, "om_first")["id"], "t1")
        self.assertIsNone(bridge.find_message_turn(turns, "om_missing"))

    def test_new_chat_marker_does_not_become_title(self):
        record = {"chat_type": "p2p", "chat_id": "oc_person", "sender_id": "ou_person",
                  "question": "新会话：审批状态为何不同？", "sender_name": "同事甲"}
        title = bridge.thread_title(record)
        self.assertIn("同事甲", title)
        self.assertIn("审批状态为何不同", title)
        self.assertNotIn("新会话：", title)

    def test_reply_idempotency_key_fits_cli_limit(self):
        key = bridge.idempotency_key("om_example", "reply")
        self.assertLessEqual(len(key), 50)
        self.assertEqual(key, bridge.idempotency_key("om_example", "reply"))


if __name__ == "__main__":
    unittest.main()
