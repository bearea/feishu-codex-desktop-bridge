import unittest
from unittest.mock import patch

import bridge
from privacy import (OUT_OF_SCOPE, PERSONAL_REFUSAL, export_decision,
                     has_long_verbatim_overlap, is_personal_question,
                     normalize_answer, unsupported_tool_used)


MESSAGE_ID = "om_1234567890abcdef"


def pending_record():
    return {"answer": "先检查展示规则。", "codex_thread_id": "original-chat",
            "review_turn_id": "request", "question": "内部接口状态为何不同？",
            "unsupported_tool": False, "source_excerpt_detected": False}


class ExportPolicyTests(unittest.TestCase):
    def test_general_answer_auto_sensitive_project_answer_review(self):
        answer = "该状态先检查前端展示规则。"
        self.assertEqual(export_decision(answer, question="产品状态显示不一致怎么排查？"), "auto")
        self.assertEqual(export_decision(answer, question="内部接口地址和配置是什么？"), "review")
        self.assertEqual(export_decision("README 概括了产品模块。", question="概括项目 README 的用途"), "auto")
        self.assertEqual(export_decision("先确认页面版本与操作步骤。",
                                         question="线上页面显示不一致，怎么排查？"), "auto")
        self.assertEqual(export_decision("请提供 README 内容，我会省略内部地址。",
                                         question="概括项目 README；不要给出具体实现、内部地址"), "auto")
        self.assertEqual(export_decision("按创建任务、访谈、生成报告三步核对。",
                                         question="说明产品流程，不展示源码、文件原文或内部地址"), "auto")
        self.assertEqual(export_decision(OUT_OF_SCOPE), "auto")

    def test_writing_block_is_plain_text_for_feishu(self):
        raw = '前言\n\n:::writing{variant="document" id="48317"}\n完整的业务流程。\n:::'
        self.assertEqual(normalize_answer(raw), "完整的业务流程。")

    def test_secret_and_raw_code_never_leave(self):
        self.assertEqual(export_decision("password=example-secret-value"), "block")
        self.assertEqual(export_decision("```python\nprint(1)\n```"), "block")
        self.assertEqual(export_decision("具体实现见 src/nova_be/service.py"), "block")

    def test_personal_requests_and_output_are_blocked(self):
        self.assertTrue(is_personal_question("请查我家人的生日和住址"))
        self.assertTrue(is_personal_question("总结我的私人项目和其他 Codex 会话"))
        self.assertFalse(is_personal_question("只读检索其他 Codex 会话中的飞书产品讨论"))
        self.assertFalse(is_personal_question("产品列表筛选为空，怎么排查？"))
        self.assertEqual(export_decision("联系电话是 13800138000"), "block")
        self.assertEqual(export_decision("查看项目记录", unsupported_tool=True), "block")

    def test_only_read_only_codex_history_tools_are_allowed(self):
        history = [{"type": "mcpToolCall", "server": "codex_app", "tool": "read_thread"}]
        self.assertFalse(unsupported_tool_used(history, allow_history=True))
        self.assertTrue(unsupported_tool_used(history, allow_history=False))
        self.assertTrue(unsupported_tool_used([{"type": "mcpToolCall", "server": "codex_app",
                                                "tool": "send_message_to_thread"}], allow_history=True))

    def test_personal_request_never_reaches_codex(self):
        question = "请总结我的私人项目里的家庭记录"
        event = {"type": "im.message.receive_v1", "message_id": MESSAGE_ID,
                 "sender_type": "user", "chat_type": "p2p", "message_type": "text",
                 "chat_id": "oc_test", "sender_id": "ou_test", "content": question}
        outbox = {}
        with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox") as flush, \
             patch.object(bridge, "ask_codex") as ask:
            bridge.handle(event, set(), outbox, {})
        ask.assert_not_called()
        flush.assert_called_once()
        self.assertEqual(outbox[MESSAGE_ID]["answer"], PERSONAL_REFUSAL)
        self.assertNotIn(question, str(outbox[MESSAGE_ID]))

    def test_long_source_excerpt_is_blocked(self):
        excerpt = "这个产品的完整原始描述包含内部流程和实现细节，需要在本机处理后才能形成可公开的概括结论。"
        item = {"type": "mcpToolCall", "result": {"content": [{"text": excerpt}]}}
        self.assertTrue(has_long_verbatim_overlap(excerpt, [item], window=30))
        self.assertTrue(has_long_verbatim_overlap("文档原话是“完整原始描述包含内部流程”", [item]))
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

    def test_new_review_does_not_navigate_to_chat(self):
        record = pending_record()
        del record["review_turn_id"]
        class FakeDesktop:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            def start_turn(self, thread_id, *_args, **_kwargs):
                self.thread_id = thread_id
                return "review-turn"
        desktop = FakeDesktop()
        with patch.object(bridge, "open_in_desktop") as open_chat, \
             patch.object(bridge, "DesktopIpc", return_value=desktop), \
             patch.object(bridge, "save_outbox"):
            with self.assertRaises(bridge.ReviewPending):
                bridge.review_answer(MESSAGE_ID, record, {MESSAGE_ID: record})
            open_chat.assert_not_called()
            self.assertEqual(desktop.thread_id, "original-chat")

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
