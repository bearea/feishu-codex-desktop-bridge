"""Check the routing and duplicate-turn rules without using live services."""

import json
import unittest
from unittest.mock import patch

import bridge
from privacy import is_prohibited_operation_request


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

    def test_group_mention_does_not_hide_a_restart_request(self):
        event = {"mentions": [{"id": "ou_bot", "key": "@_user_1", "name": "福头 ✨ 🤖"}]}
        question = "@福头 ✨ 🤖 能帮我重启pod吗？"
        clean = bridge.question_for_policy(question, event)
        self.assertEqual(clean, "能帮我重启pod吗？")
        self.assertTrue(is_prohibited_operation_request(clean))
        self.assertFalse(is_prohibited_operation_request("能否帮我分析重启Pod有什么风险？"))

    def test_reply_to_bot_answer_reuses_the_original_group_chat(self):
        chat_id, sender_id = "oc_group", "ou_owner"
        for parent_field in ("parent_id", "reply_to", "root_id"):
            with self.subTest(parent_field=parent_field):
                event = {"type": "im.message.receive_v1", "message_id": "om_followup",
                         "sender_type": "user", "chat_type": "group", "message_type": "text",
                         "chat_id": chat_id, "sender_id": sender_id,
                         "content": "@福头 ✨ 🤖 能帮我重启pod吗？",
                         parent_field: "om_bot_answer"}
                threads = {bridge.reply_route_key(chat_id, "om_bot_answer", sender_id): "original-thread"}
                outbox = {}
                with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
                    bridge.handle(event, set(), outbox, threads)
                record = outbox["om_followup"]
                self.assertEqual(record["codex_thread_id"], "original-thread")
                self.assertTrue(record["operation_request"])
                self.assertTrue(record["input_review_reason"])
                self.assertEqual(record["question"], event["content"])

                event["sender_id"] = "ou_someone_else"
                outbox = {}
                with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
                    bridge.handle(event, set(), outbox, threads)
                self.assertIsNone(outbox["om_followup"]["codex_thread_id"])

    def test_reply_route_repairs_an_older_wrong_group_binding(self):
        event = {"type": "im.message.receive_v1", "message_id": "om_new_followup",
                 "sender_type": "user", "chat_type": "group", "message_type": "text",
                 "chat_id": "oc_group", "sender_id": "ou_owner", "root_id": "om_bot_answer",
                 "content": "@福头 ✨ 🤖 下一步怎么排查？"}
        key = bridge.thread_key(event)
        threads = {key: "wrong-thread",
                   bridge.reply_route_key("oc_group", "om_bot_answer", "ou_owner"): "original-thread"}
        outbox = {}
        with patch.object(bridge, "save_private_json") as save_threads, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
            bridge.handle(event, set(), outbox, threads)
        self.assertEqual(outbox["om_new_followup"]["codex_thread_id"], "original-thread")
        self.assertEqual(threads[key], "original-thread")
        save_threads.assert_called_once()

    def test_group_answer_registers_a_followup_route(self):
        record = {"answer": "请补充目标环境。", "question": "帮我看下", "chat_id": "oc_group",
                  "chat_type": "group", "sender_id": "ou_owner", "message_id": "om_question",
                  "codex_thread_id": "original-thread", "sent": False}
        outbox = {"om_question": record}
        threads = {}
        with patch.object(bridge, "review_answer", return_value=record["answer"]), \
             patch.object(bridge, "send_text", return_value="om_bot_answer"), \
             patch.object(bridge, "save_private_json"), patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, set(), threads)
        self.assertEqual(threads[bridge.reply_route_key("oc_group", "om_bot_answer", "ou_owner")],
                         "original-thread")

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
