import unittest
from unittest.mock import patch

import bridge
from privacy import OUT_OF_SCOPE, export_decision, has_long_verbatim_overlap


MESSAGE_ID = "om_1234567890abcdef"


def pending_record():
    return {"answer": "先检查展示规则。", "codex_thread_id": "original-chat",
            "review_turn_id": "request", "question": "为何不同？",
            "private_context": True, "source_excerpt_detected": False}


class ExportPolicyTests(unittest.TestCase):
    def test_general_answer_auto_private_answer_review(self):
        answer = "该状态先检查前端展示规则。"
        self.assertEqual(export_decision(answer, private_context=False), "auto")
        self.assertEqual(export_decision(answer, private_context=True), "review")
        self.assertEqual(export_decision(OUT_OF_SCOPE), "auto")

    def test_secret_and_raw_code_never_leave(self):
        self.assertEqual(export_decision("password=example-secret-value", private_context=False), "block")
        self.assertEqual(export_decision("```python\nprint(1)\n```", private_context=False), "block")

    def test_long_source_excerpt_is_blocked(self):
        excerpt = "这个产品的完整原始描述包含内部流程和实现细节，需要在本机处理后才能形成可公开的概括结论。"
        item = {"type": "mcpToolCall", "result": {"content": [{"text": excerpt}]}}
        self.assertTrue(has_long_verbatim_overlap(excerpt, [item], window=30))
        self.assertFalse(has_long_verbatim_overlap("只概括主题类别。", [item], window=30))

    def test_review_poll_stays_in_original_chat_without_reopening(self):
        record = pending_record()
        turns = [{"id": "request", "items": [{"type": "userMessage", "content": [{"text": "prompt"}]}]}]
        with patch.object(bridge, "open_in_desktop") as open_chat, \
             patch.object(bridge, "read_history", return_value=turns) as read:
            for _ in range(3):
                with self.assertRaises(bridge.ReviewPending):
                    bridge.review_answer(MESSAGE_ID, record, {MESSAGE_ID: record})
            open_chat.assert_not_called()
            self.assertEqual(read.call_args.args[0], "original-chat")

    def test_native_async_choice_in_same_chat_approves(self):
        record = pending_record()
        native_reply = ('<send_user_message_question_reply>\n'
            '[{"question":"审核编号 567890abcdef：是否允许发送？",'
            '"answer":"允许发送"}]\n</send_user_message_question_reply>')
        turns = [{"id": "request", "items": [
            {"type": "userMessage", "content": [{"text": "bridge prompt"}]},
            {"type": "agentMessage", "phase": "final_answer", "delivery": "async"},
            {"type": "userMessage", "content": [{"text": native_reply}]},
        ]}]
        with patch.object(bridge, "read_history", return_value=turns), \
             patch.object(bridge, "save_outbox"):
            self.assertEqual(bridge.review_answer(MESSAGE_ID, record, {MESSAGE_ID: record}), record["answer"])
        self.assertEqual(record["review_decision"], "allow")

    def test_wrong_review_number_cannot_approve(self):
        record = pending_record()
        turns = [{"id": "request", "items": []},
                 {"id": "reply", "items": [{"type": "userMessage", "content": [
                     {"text": "**审核编号 wrong**\n\n允许发送"}]}]}]
        with patch.object(bridge, "read_history", return_value=turns):
            with self.assertRaises(bridge.ReviewPending):
                bridge.review_answer(MESSAGE_ID, record, {MESSAGE_ID: record})


if __name__ == "__main__":
    unittest.main()
