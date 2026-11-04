"""Check the routing and duplicate-turn rules without using live services."""

import json
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

    def test_sender_name_in_event_is_kept_for_title(self):
        event = {"type": "im.message.receive_v1", "message_id": "om_named123",
                 "sender_type": "user", "chat_type": "p2p", "message_type": "text",
                 "chat_id": "oc_named123", "sender_id": "ou_named123",
                 "sender_name": "同事乙", "content": "如何使用？"}
        outbox = {}
        with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
            bridge.handle(event, set(), outbox, {})
        self.assertIn("同事乙", bridge.thread_title(outbox["om_named123"]))

    def test_message_detail_supplies_name_outside_contact_range(self):
        record = {"sender_id": "ou_named123", "message_id": "om_named123"}
        contact = type("Result", (), {"returncode": 1,
            "stderr": json.dumps({"error": {"code": 41050}}), "stdout": ""})()
        message = type("Result", (), {"returncode": 0, "stderr": "",
            "stdout": json.dumps({"ok": True, "data": {"messages": [{
                "message_id": "om_named123", "sender": {"id": "ou_named123", "name": "同事丙"}}]}})})()
        with patch.object(bridge.subprocess, "run", side_effect=[contact, message]) as run:
            self.assertEqual(bridge.lookup_sender_name(record), "同事丙")
        self.assertIn("+messages-mget", run.call_args.args[0])

    def test_reply_idempotency_key_fits_cli_limit(self):
        key = bridge.idempotency_key("om_example", "reply")
        self.assertLessEqual(len(key), 50)
        self.assertEqual(key, bridge.idempotency_key("om_example", "reply"))

    def test_reused_thread_is_loaded_into_desktop_before_recovery(self):
        message_id = "om_recover"
        record = {"chat_type": "p2p", "chat_id": "oc_person", "sender_id": "ou_person",
                  "message_id": message_id, "question": "进度如何？", "create_time": "1793707895348",
                  "codex_thread_id": "thread-1", "project_bound": True}
        turns = [{"id": "turn-1", "items": [{"type": "userMessage", "content": [
            {"type": "text", "text": f"飞书消息 ID：{message_id}\n\n进度如何？"}]}]}]
        with patch.object(bridge, "open_in_desktop") as open_desktop, \
             patch.object(bridge, "read_history", return_value=turns), \
             patch.object(bridge, "wait_for_completed", return_value="请提供项目名称。"), \
             patch.object(bridge, "assert_turn_security"), \
             patch.object(bridge, "save_outbox"):
            answer = bridge.ask_codex(message_id, record, {}, {message_id: record})
        open_desktop.assert_called_once_with("thread-1")
        self.assertEqual(answer, "请提供项目名称。")

    def test_new_event_does_not_retry_an_unrelated_stale_record_first(self):
        stale = {"answer": None, "codex_thread_id": "stale-thread"}
        current = {"answer": "请补充页面截图。", "question": "如何排查？",
                   "chat_id": "oc_person", "chat_type": "p2p", "sender_id": "ou_person",
                   "message_id": "om_current", "codex_thread_id": "current-thread", "sent": False}
        outbox = {"om_stale": stale, "om_current": current}
        done = set()
        with patch.object(bridge, "ask_codex", side_effect=AssertionError("stale retried")), \
             patch.object(bridge, "review_answer", return_value=current["answer"]), \
             patch.object(bridge, "send_text"), patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, done, {}, target_message_id="om_current")
        self.assertEqual(done, {"om_current"})
        self.assertIn("om_stale", outbox)


if __name__ == "__main__":
    unittest.main()
