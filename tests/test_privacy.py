import unittest
from unittest.mock import patch
from pathlib import Path
import tempfile
import json

import bridge
from privacy import OUT_OF_SCOPE, export_decision, has_long_verbatim_overlap


class ExportPolicyTests(unittest.TestCase):
    def test_review_poll_does_not_reopen_desktop_chat(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "review.json"
            state.write_text(json.dumps({"thread_id": "review", "project_bound": True}))
            message_id = "om_1234567890abcdef"
            record = {"answer": "先检查展示规则。", "review_turn_id": "request",
                "review_thread_id": "review", "question": "为何不同？",
                "source_excerpt_detected": False}
            with patch.object(bridge, "REVIEW_THREAD", state), \
                 patch.object(bridge, "open_in_desktop") as open_chat, \
                 patch.object(bridge, "read_history", return_value=[
                     {"id": "request", "items": [{"type": "userMessage", "content": [{"text": "prompt"}]}]}]):
                for _ in range(3):
                    with self.assertRaises(bridge.ReviewPending):
                        bridge.review_answer(message_id, record, {message_id: record})
                open_chat.assert_not_called()

    def test_general_product_answer_waits_for_owner(self):
        self.assertEqual(export_decision("该状态先检查前端展示规则。"), "review")

    def test_canned_unrelated_answer_is_safe_to_send(self):
        self.assertEqual(export_decision(OUT_OF_SCOPE), "auto")

    def test_secret_and_raw_code_never_leave(self):
        self.assertEqual(export_decision("password=example-secret-value"), "block")
        self.assertEqual(export_decision("```python\nprint(1)\n```"), "block")

    def test_long_source_excerpt_is_blocked(self):
        excerpt = "这个产品的完整原始描述包含内部流程和实现细节，需要在本机处理后才能形成可公开的概括结论。"
        item = {"type": "mcpToolCall", "result": {"content": [{"text": excerpt}]}}
        self.assertTrue(has_long_verbatim_overlap(excerpt, [item], window=30))
        self.assertFalse(has_long_verbatim_overlap("只概括主题类别。", [item], window=30))

    def test_approval_must_match_exact_review_id_and_option(self):
        message_id = "om_1234567890abcdef"
        record = {"answer": "先检查展示规则。", "review_turn_id": "request",
                  "review_thread_id": "review", "question": "为何不同？", "source_excerpt_detected": False}
        outbox = {message_id: record}
        turns = [
            {"id": "request", "items": []},
            {"id": "reply", "items": [{"type": "userMessage", "content": [
                {"type": "text", "text": "**审核编号 567890abcdef**\n\n允许发送"}]}]},
        ]
        with patch.object(bridge, "ensure_review_thread", return_value="review"), \
             patch.object(bridge, "read_history", return_value=turns), \
             patch.object(bridge, "save_outbox"):
            self.assertEqual(bridge.review_answer(message_id, record, outbox), record["answer"])
        self.assertEqual(record["review_decision"], "allow")

    def test_unrelated_click_does_not_approve(self):
        message_id = "om_1234567890abcdef"
        record = {"answer": "先检查展示规则。", "review_turn_id": "request",
                  "review_thread_id": "review", "question": "为何不同？", "source_excerpt_detected": False}
        turns = [{"id": "request", "items": []},
                 {"id": "reply", "items": [{"type": "userMessage", "content": [
                     {"type": "text", "text": "**审核编号 wrong**\n\n允许发送"}]}]}]
        with patch.object(bridge, "ensure_review_thread", return_value="review"), \
             patch.object(bridge, "read_history", return_value=turns):
            with self.assertRaises(bridge.ReviewPending):
                bridge.review_answer(message_id, record, {message_id: record})

    def test_native_async_question_reply_is_recognized(self):
        message_id = "om_1234567890abcdef"
        record = {"answer": "先检查展示规则。", "review_turn_id": "request",
                  "review_thread_id": "review", "question": "为何不同？", "source_excerpt_detected": False}
        native_reply = ('<send_user_message_question_reply>\n'
            '[{"question":"审核编号 567890abcdef：是否允许发送？",'
            '"answer":"允许发送"}]\n</send_user_message_question_reply>')
        turns = [{"id": "request", "items": []},
                 {"id": "reply", "items": [{"type": "userMessage", "content": [
                     {"type": "text", "text": native_reply}]}]}]
        with patch.object(bridge, "ensure_review_thread", return_value="review"), \
             patch.object(bridge, "read_history", return_value=turns), \
             patch.object(bridge, "save_outbox"):
            self.assertEqual(bridge.review_answer(message_id, record, {message_id: record}), record["answer"])

    def test_native_reply_in_same_turn_is_recognized(self):
        message_id = "om_1234567890abcdef"
        record = {"answer": "先检查展示规则。", "review_turn_id": "request",
                  "review_thread_id": "review", "question": "为何不同？", "source_excerpt_detected": False}
        native_reply = ('<send_user_message_question_reply>\n'
            '[{"question":"审核编号 567890abcdef：是否允许发送？",'
            '"answer":"允许发送"}]\n</send_user_message_question_reply>')
        turns = [{"id": "request", "items": [
            {"type": "userMessage", "content": [{"text": "bridge prompt"}]},
            {"type": "agentMessage", "phase": "final_answer", "delivery": "async"},
            {"type": "userMessage", "content": [{"text": native_reply}]},
        ]}]
        with patch.object(bridge, "ensure_review_thread", return_value="review"), \
             patch.object(bridge, "read_history", return_value=turns), \
             patch.object(bridge, "save_outbox"):
            self.assertEqual(bridge.review_answer(message_id, record, {message_id: record}), record["answer"])


if __name__ == "__main__":
    unittest.main()
