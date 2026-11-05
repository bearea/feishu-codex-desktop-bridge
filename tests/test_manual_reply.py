"""Protect owner-authored Feishu replies from accidental forwarding."""

import json
import tempfile
import unittest
from contextlib import contextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

import bridge
from manual_reply import classify_owner_message, inline_reply


def user_turn(turn_id, text, status="completed"):
    return {"id": turn_id, "status": status,
            "items": [{"type": "userMessage", "content": [{"text": text}]}]}


class ManualReplyTests(unittest.TestCase):
    def test_classifier_respects_semantic_intent(self):
        def run_result(send, reply):
            def fake_run(argv, **_kwargs):
                Path(argv[argv.index("-o") + 1]).write_text(json.dumps({
                    "send": send, "reply_text": reply}), encoding="utf-8")
                return type("Result", (), {"returncode": 0})()
            return fake_run
        with patch("manual_reply.subprocess.run", side_effect=run_result(True, "请先提供截图。")):
            self.assertEqual(classify_owner_message("帮我回复他：请先提供截图。",
                                                    model="gpt-6.1-sol"), "请先提供截图。")
        with patch("manual_reply.subprocess.run", side_effect=run_result(False, "")):
            self.assertIsNone(classify_owner_message("我该怎么回复他？", model="gpt-6.1-sol"))
        with patch("manual_reply.subprocess.run", side_effect=run_result(True, "请先发页面截图。")):
            self.assertIsNone(classify_owner_message("帮我回复他：请先提供截图。",
                                                     model="gpt-6.1-sol"))

    def test_long_inline_reply_is_extracted_without_model_rewriting(self):
        body = "这是一份产品配置契约说明。" * 420
        message = body + "\n\n---\n\n把上面这段话发送给冯金福"
        self.assertEqual(inline_reply(message), body)
        with patch("manual_reply.subprocess.run") as run:
            self.assertEqual(classify_owner_message(message, model="gpt-6.1-sol"), body)
            run.assert_not_called()

    def test_quoted_verbatim_reply_is_extracted_without_model_rewriting(self):
        message = "好的，原文发送给冯金福，“可以的，现在测试一下补发消息”"
        with patch("manual_reply.subprocess.run") as run:
            self.assertEqual(classify_owner_message(message, model="gpt-6.1-sol"),
                             "可以的，现在测试一下补发消息")
            self.assertEqual(classify_owner_message(
                "发送到群聊，“好的，我正在下载文件并分析使用方法”", model="gpt-6.1-sol"),
                "好的，我正在下载文件并分析使用方法")
            run.assert_not_called()

    def test_rollout_reader_handles_long_completed_turn_without_app_server_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".codex" / "sessions" / "rollout-thread-1.jsonl"
            path.parent.mkdir(parents=True)
            events = [
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
                {"type": "response_item", "payload": {"role": "user", "content": [{"text": "<environment_context>skip</environment_context>"}]}},
                {"type": "response_item", "payload": {"role": "user", "content": [{"text": "# AGENTS.md instructions for /Users/example/project\n<INSTRUCTIONS>skip</INSTRUCTIONS>"}]}},
                {"type": "response_item", "payload": {"role": "user", "content": [{"text": "飞书提问人：冯金福（ou_123）\n飞书会话：oc_12345678\n飞书消息 ID：om_12345678\n\n问题"}]}},
                {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}},
                {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}},
                {"type": "response_item", "payload": {"role": "user", "content": [{"text": "正文\n---\n把上面这段话发送给冯金福"}]}},
                {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-2"}},
            ]
            path.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n")
            with patch.object(bridge.Path, "home", return_value=root):
                turns = bridge.rollout_user_turns(str(path), "thread-1")
        self.assertEqual(bridge.feishu_chat_from_history(turns), "oc_12345678")
        self.assertEqual(bridge.feishu_sender_from_history(turns), "冯金福")
        self.assertEqual(bridge.manual_turn_candidates(turns, set())[-1][0], "turn-2")
        self.assertEqual(bridge.explicit_named_recipient("把上面这段话发送给冯金福"), "冯金福")
        self.assertIsNone(bridge.explicit_named_recipient("告诉对方请提供截图"))
        self.assertIsNone(bridge.explicit_named_recipient("告诉他请发截图"))

    def test_only_owner_turn_is_candidate(self):
        turns = [
            user_turn("feishu", "飞书提问人：张三\n飞书会话：oc_12345678\n飞书消息 ID：om_12345678\n\n问题"),
            user_turn("agents", "# AGENTS.md instructions for /Users/example/project\n\n<INSTRUCTIONS>"),
            user_turn("review", "本机飞书答复审核，编号 abc。请审核"),
            user_turn("receipt", "本机飞书发送回执，编号 manual:owner\n飞书接口已确认发送"),
            user_turn("pending", "帮我回复他：请稍等。", "inProgress"),
            user_turn("owner", "帮我回复他：请提供截图。"),
        ]
        seen = set()
        self.assertEqual(bridge.feishu_chat_from_history(turns), "oc_12345678")
        self.assertEqual(bridge.manual_turn_candidates(turns, seen),
                         [("owner", "帮我回复他：请提供截图。")])
        self.assertEqual(seen, {"feishu", "agents", "review", "receipt"})

    def test_scan_queues_manual_reply_but_bootstrap_does_not_replay(self):
        turns = [user_turn("feishu", "飞书提问人：张三\n飞书会话：oc_12345678\n飞书消息 ID：om_12345678\n\n问题"),
                 user_turn("owner", "告诉他请发截图。")]
        page = {"data": [{"id": "thread-1", "originator": "feishu_codex_bridge"}]}
        history = {"thread": {"turns": turns}}

        @contextmanager
        def fake_app_server():
            yield object()

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bridge, "app_server", fake_app_server), \
             patch.object(bridge, "send_rpc"), \
             patch.object(bridge, "rpc_result", side_effect=[page, page]), \
             patch.object(bridge, "rollout_user_turns", return_value=turns), \
             patch.object(bridge, "classify_owner_message", return_value="请发截图。") as classify, \
             patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "MANUAL_SEEN", Path(directory) / "seen.json"):
            outbox = {}
            seen = set()
            bridge.scan_manual_replies(outbox, set(), seen, bootstrap=True)
            self.assertFalse(outbox)
            classify.assert_not_called()
            seen.remove("owner")
            bridge.scan_manual_replies(outbox, set(), seen, bootstrap=False)
            self.assertEqual(outbox["manual:owner"]["chat_id"], "oc_12345678")
            self.assertEqual(outbox["manual:owner"]["answer"], "请发截图。")

    def test_named_recipient_mismatch_is_withheld_before_classification(self):
        turns = [user_turn("feishu", "飞书提问人：冯金福（ou_123）\n飞书会话：oc_12345678\n飞书消息 ID：om_12345678\n\n问题"),
                 user_turn("owner", "把上面那段内容发给荣书浛")]
        page = {"data": [{"id": "thread-1", "originator": "feishu_codex_bridge"}]}
        @contextmanager
        def fake_app_server():
            yield object()
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bridge, "app_server", fake_app_server), \
             patch.object(bridge, "send_rpc"), \
             patch.object(bridge, "rpc_result", return_value=page), \
             patch.object(bridge, "rollout_user_turns", return_value=turns), \
             patch.object(bridge, "classify_owner_message") as classify, \
             patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "MANUAL_SEEN", Path(directory) / "seen.json"):
            outbox = {}
            bridge.scan_manual_replies(outbox, set(), set(), bootstrap=False)
        classify.assert_not_called()
        self.assertTrue(outbox["manual:owner"]["withheld"])
        self.assertIn("收件人", outbox["manual:owner"]["withheld_reason"])

    def test_scan_merges_split_rollouts_before_routing_owner_reply(self):
        old = [user_turn("feishu", "飞书提问人：冯金福（ou_123）\n飞书会话：oc_12345678\n飞书消息 ID：om_12345678\n\n问题")]
        new = [user_turn("owner", "好的，原文发送给冯金福，“可以的，现在测试一下补发消息”")]
        page = {"data": [
            {"id": "thread-1", "originator": "feishu_codex_bridge", "path": "new.jsonl",
             "createdAt": 2, "updatedAt": 3},
            {"id": "thread-1", "originator": "feishu_codex_bridge", "path": "old.jsonl",
             "createdAt": 1, "updatedAt": 2},
        ]}

        @contextmanager
        def fake_app_server():
            yield object()

        with tempfile.TemporaryDirectory() as directory, \
             patch.object(bridge, "app_server", fake_app_server), \
             patch.object(bridge, "send_rpc"), \
             patch.object(bridge, "rpc_result", return_value=page), \
             patch.object(bridge, "rollout_user_turns", side_effect=lambda path, _id: old if path == "old.jsonl" else new), \
             patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "MANUAL_SEEN", Path(directory) / "seen.json"):
            outbox = {}
            bridge.scan_manual_replies(outbox, set(), set(), bootstrap=False)
        self.assertEqual(outbox["manual:owner"]["chat_id"], "oc_12345678")
        self.assertEqual(outbox["manual:owner"]["answer"], "可以的，现在测试一下补发消息")

    def test_group_reply_uses_original_group_and_p2p_group_request_is_withheld(self):
        turns = [user_turn("feishu", "飞书提问人：冯金福（ou_123）\n飞书会话：oc_group123\n飞书消息 ID：om_12345678\n\n问题"),
                 user_turn("owner", "发送到群聊，“好的，我正在下载文件并分析使用方法”")]
        page = {"data": [{"id": "thread-1", "originator": "feishu_codex_bridge"}]}

        @contextmanager
        def fake_app_server():
            yield object()

        for routes, expected_withheld in [({"group:oc_group123:om_12345678:ou_123": "thread-1"}, False),
                                          ({}, True)]:
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(bridge, "app_server", fake_app_server), \
                 patch.object(bridge, "send_rpc"), \
                 patch.object(bridge, "rpc_result", return_value=page), \
                 patch.object(bridge, "rollout_user_turns", return_value=turns), \
                 patch.object(bridge, "load_threads", return_value=routes), \
                 patch.object(bridge, "save_outbox"), \
                 patch.object(bridge, "MANUAL_SEEN", Path(directory) / "seen.json"):
                outbox = {}
                bridge.scan_manual_replies(outbox, set(), set(), bootstrap=False)
            record = outbox["manual:owner"]
            self.assertEqual(record["chat_id"], "oc_group123")
            self.assertEqual(bool(record.get("withheld")), expected_withheld)
            if not expected_withheld:
                self.assertEqual(record["chat_type"], "group")
                self.assertEqual(record["answer"], "好的，我正在下载文件并分析使用方法")

    def test_review_notice_is_sent_once(self):
        record = {"answer": "内部接口地址需要审核。", "question": "内部接口地址是什么？",
                  "chat_id": "oc_12345678", "chat_type": "p2p", "sender_id": "ou_owner",
                  "message_id": "om_12345678", "codex_thread_id": "thread-1", "sent": False}
        outbox = {"om_12345678": record}
        with patch.object(bridge, "review_answer", side_effect=bridge.ReviewPending("waiting")), \
             patch.object(bridge, "send_text") as send, patch.object(bridge, "save_outbox"):
            bridge.flush_outbox(outbox, set(), {})
            bridge.flush_outbox(outbox, set(), {})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], "review-wait")

    def test_manual_send_uses_the_original_feishu_chat(self):
        record = {"manual_reply": True, "answer": "请先提供截图。",
                  "question": "请执行配置发布并告诉他请先提供截图。", "chat_id": "oc_12345678",
                  "chat_type": "p2p", "sender_id": "ou_owner", "message_id": "manual:turn-1",
                  "codex_thread_id": "thread-1", "source_excerpt_detected": False,
                  "unsupported_tool": False, "sent": False}
        outbox = {"manual:turn-1": record}
        done = set()
        with patch.object(bridge, "send_text") as send, \
             patch.object(bridge, "post_manual_delivery_receipt", return_value="receipt-turn") as receipt, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, done, {})
        self.assertEqual(send.call_args.args[1:4], ("manual", "--chat-id", "oc_12345678"))
        self.assertEqual(send.call_args.args[-1], "请先提供截图。")
        receipt.assert_called_once()
        self.assertIn("manual:turn-1", done)

    def test_manual_raw_code_waits_for_owner_review(self):
        record = {"manual_reply": True, "answer": "```python\nprint(1)\n```",
                  "question": "把代码发给他", "chat_id": "oc_12345678",
                  "chat_type": "p2p", "sender_id": "ou_owner", "message_id": "manual:turn-2",
                  "codex_thread_id": "thread-1", "source_excerpt_detected": False,
                  "unsupported_tool": False, "sent": False}
        outbox = {"manual:turn-2": record}
        with patch.object(bridge, "review_answer", side_effect=bridge.ReviewPending("waiting")), \
             patch.object(bridge, "send_text") as send, \
             patch.object(bridge, "post_manual_delivery_receipt", return_value="receipt-turn") as receipt, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, set(), {})
        self.assertEqual(send.call_count, 1)
        self.assertEqual(send.call_args.args[1], "review-wait")
        receipt.assert_not_called()
        self.assertFalse(record.get("withheld", False))

    def test_receipt_is_posted_in_original_chat_and_deduplicated(self):
        record = {"codex_thread_id": "thread-1", "delivery_message_id": "om_sent123"}
        class FakeDesktop:
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return None
            def start_turn(self, thread_id, prompt, client_id, **kwargs):
                self.call = (thread_id, prompt, client_id, kwargs)
                return "receipt-turn"
        desktop = FakeDesktop()
        with patch.object(bridge, "app_server", return_value=nullcontext(None)), \
             patch.object(bridge, "send_rpc"), \
             patch.object(bridge, "rpc_result", return_value={"thread": {"path": "rollout.jsonl"}}), \
             patch.object(bridge, "rollout_user_turns", return_value=[]), \
             patch.object(bridge, "DesktopIpc", return_value=desktop), \
             patch.object(bridge, "wait_for_completed", return_value="已记录") as wait:
            self.assertEqual(bridge.post_manual_delivery_receipt("manual:turn-1", record),
                             "receipt-turn")
            wait.assert_called_once_with("thread-1", "receipt-turn", 120)
        self.assertEqual(desktop.call[0], "thread-1")
        self.assertIn("飞书接口已确认发送", desktop.call[1])
        self.assertIn("飞书消息 ID：om_sent123", desktop.call[1])
        self.assertIn("这不代表对方已读", desktop.call[1])
        with patch.object(bridge, "app_server", return_value=nullcontext(None)), \
             patch.object(bridge, "send_rpc"), \
             patch.object(bridge, "rpc_result", return_value={"thread": {"path": "rollout.jsonl"}}), \
             patch.object(bridge, "rollout_user_turns", return_value=[user_turn("receipt-turn", desktop.call[1])]), \
             patch.object(bridge, "DesktopIpc") as new_desktop, \
             patch.object(bridge, "wait_for_completed", return_value="已记录") as wait:
            self.assertEqual(bridge.post_manual_delivery_receipt("manual:turn-1", record),
                             "receipt-turn")
            new_desktop.assert_not_called()
            wait.assert_called_once_with("thread-1", "receipt-turn", 120)

    def test_receipt_retry_never_resends_feishu_message(self):
        record = {"manual_reply": True, "answer": "请先提供截图。", "sent": True,
                  "chat_id": "oc_12345678", "codex_thread_id": "thread-1",
                  "delivery_message_id": "om_sent123"}
        outbox = {"manual:turn-1": record}
        done = set()
        with patch.object(bridge, "send_text") as send, \
             patch.object(bridge, "post_manual_delivery_receipt", return_value="receipt-turn") as receipt, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, done, {})
        send.assert_not_called()
        receipt.assert_called_once()
        self.assertEqual(done, {"manual:turn-1"})

    def test_failed_send_does_not_claim_a_receipt(self):
        record = {"manual_reply": True, "answer": "请先提供截图。", "question": "告诉他请先提供截图。",
                  "chat_id": "oc_12345678", "chat_type": "p2p", "sender_id": "ou_owner",
                  "message_id": "manual:turn-1", "codex_thread_id": "thread-1",
                  "source_excerpt_detected": False, "unsupported_tool": False, "sent": False}
        outbox = {"manual:turn-1": record}
        with patch.object(bridge, "send_text", side_effect=RuntimeError("network unavailable")), \
             patch.object(bridge, "post_manual_delivery_receipt") as receipt:
            with self.assertRaisesRegex(RuntimeError, "network unavailable"):
                bridge.flush_outbox(outbox, set(), {})
        receipt.assert_not_called()
        self.assertFalse(record["sent"])

    def test_bot_links_render_as_markdown_but_manual_reply_keeps_exact_text(self):
        link = "[对公机构日报](https://example.com/performance-analysis/corporate-branch)"
        result = type("Result", (), {"returncode": 0,
                                      "stdout": '{"ok":true,"data":{"message_id":"om_sent123"}}',
                                      "stderr": ""})()
        with patch.object(bridge.subprocess, "run", return_value=result) as run:
            self.assertEqual(bridge.send_text("om_test", "reply", "--chat-id", "oc_test", link),
                             "om_sent123")
            self.assertIn("--markdown", run.call_args.args[0])
            bridge.send_text("manual:test", "manual", "--chat-id", "oc_test", link)
            self.assertIn("--text", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][run.call_args.args[0].index("--text") + 1], link)


if __name__ == "__main__":
    unittest.main()
