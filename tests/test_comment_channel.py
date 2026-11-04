"""Document comments are intentional requests; document edits are not."""

import unittest
from unittest.mock import patch

import bridge
from comment_channel import (comment_notice_id, load_comment_request,
                             publish_comment_answer, text_from_reply)


EVENT = {
    "type": "drive.notice.comment_add_v1", "event_id": "evt-1",
    "file_type": "docx", "file_token": "DocxTokenExample1234567890",
    "comment_id": "1111111111111111111", "reply_id": "2222222222222222222",
    "is_mentioned": True, "from_user_type": "user",
    "from_open_id": "ou_example_user_1234567890",
    "timestamp": "1793693832000",
}


class CommentChannelTests(unittest.TestCase):
    def test_only_explicit_bot_mention_is_a_request(self):
        self.assertEqual(comment_notice_id(EVENT),
                         "dc:docx:DocxTokenExample1234567890:1111111111111111111:2222222222222222222")
        self.assertIsNone(comment_notice_id({**EVENT, "is_mentioned": False}))
        self.assertIsNone(comment_notice_id({**EVENT, "from_user_type": "bot"}))
        self.assertIsNone(comment_notice_id({**EVENT, "type": "drive.file.edit_v1"}))
        self.assertIsNone(comment_notice_id({**EVENT, "file_token": "../escape"}))

    def test_text_excludes_the_bot_mention(self):
        reply = {"content": {"elements": [
            {"type": "person", "person": {"user_id": "ou_bot"}},
            {"type": "text_run", "text_run": {"text": " 总结一下"}},
        ]}}
        self.assertEqual(text_from_reply(reply), "总结一下")

    def test_loads_only_the_mentioned_reply_and_document_body(self):
        record = {"file_token": EVENT["file_token"], "file_type": "docx",
                  "comment_id": EVENT["comment_id"], "reply_id": EVENT["reply_id"]}
        comment = {"ok": True, "data": {"items": [{"comment_id": EVENT["comment_id"],
            "is_whole": True, "reply_list": {"replies": [
                {"reply_id": EVENT["reply_id"], "content": {"elements": [
                    {"type": "text_run", "text_run": {"text": "总结一下"}}]}}]}}]}}
        doc = {"ok": True, "data": {"document": {"content": "# 架构\n产品内容"}}}
        with patch("comment_channel.cli_json", side_effect=[comment, doc]) as cli:
            self.assertEqual(load_comment_request(record, "bot"),
                             ("总结一下", "# 架构\n产品内容", True, False))
        self.assertEqual(cli.call_count, 2)

    def test_missing_reply_id_uses_root_reply(self):
        record = {"file_token": EVENT["file_token"], "file_type": "sheet",
                  "comment_id": EVENT["comment_id"], "reply_id": None}
        comment = {"ok": True, "data": {"items": [{"comment_id": EVENT["comment_id"],
            "is_solved": True, "reply_list": {"replies": [
                {"reply_id": EVENT["reply_id"], "content": {"elements": [
                    {"type": "text_run", "text_run": {"text": "检查一下"}}]}}]}}]}}
        with patch("comment_channel.cli_json", return_value=comment):
            self.assertEqual(load_comment_request(record, "bot"),
                             ("检查一下", "", False, True))

    def test_comment_still_reaches_codex_when_document_body_is_forbidden(self):
        record = {"file_token": EVENT["file_token"], "file_type": "docx",
                  "comment_id": EVENT["comment_id"], "reply_id": EVENT["reply_id"]}
        comment = {"ok": True, "data": {"items": [{"comment_id": EVENT["comment_id"],
            "reply_list": {"replies": [{"reply_id": EVENT["reply_id"],
                "content": {"elements": [{"type": "text_run",
                                        "text_run": {"text": "总结一下"}}]}}]}}]}}
        with patch("comment_channel.cli_json", side_effect=[comment, RuntimeError("missing_scope")]):
            self.assertEqual(load_comment_request(record, "bot"),
                             ("总结一下", "", False, False))

    def test_whole_doc_answer_creates_a_new_mentioned_comment(self):
        record = {"file_token": EVENT["file_token"], "file_type": "docx",
                  "comment_id": EVENT["comment_id"], "sender_id": EVENT["from_open_id"],
                  "is_whole": True}
        with patch("comment_channel.cli_json") as cli:
            publish_comment_answer(record, "bot", "这是归纳结果。")
        self.assertIn("+add-comment", cli.call_args.args)
        self.assertIn('"mention_user": "ou_example_user_1234567890"',
                      cli.call_args.args[cli.call_args.args.index("--content") + 1])

    def test_local_comment_answer_replies_in_place(self):
        record = {"file_token": EVENT["file_token"], "file_type": "docx",
                  "comment_id": EVENT["comment_id"], "is_whole": False}
        with patch("comment_channel.cli_json") as cli:
            publish_comment_answer(record, "bot", "这是答复。")
        self.assertIn("+add-reply", cli.call_args.args)

    def test_bridge_queues_comment_separately_from_im(self):
        outbox = {}
        with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
            bridge.handle(EVENT, set(), outbox, {})
            bridge.handle({**EVENT, "type": "drive.file.edit_v1"}, set(), outbox, {})
        self.assertEqual(len(outbox), 1)
        record = next(iter(outbox.values()))
        self.assertEqual(record["chat_type"], "doc_comment")
        self.assertEqual(bridge.thread_key(record),
                         f"doc:docx:{EVENT['file_token']}:{EVENT['comment_id']}")

    def test_operation_requested_in_comment_does_not_reach_codex(self):
        message_id = comment_notice_id(EVENT)
        assert message_id
        record = {"source_type": "doc_comment", "chat_type": "doc_comment",
                  "chat_id": f"doc:docx:{EVENT['file_token']}:{EVENT['comment_id']}",
                  "file_token": EVENT["file_token"], "file_type": "docx",
                  "comment_id": EVENT["comment_id"], "reply_id": EVENT["reply_id"],
                  "sender_id": EVENT["from_open_id"], "create_time": EVENT["timestamp"],
                  "answer": None, "question": None, "sent": False}
        outbox = {message_id: record}
        with patch.object(bridge, "load_comment_request",
                          return_value=("帮我重启服务", "文档内容", False, False)), \
             patch.object(bridge, "ask_codex") as ask, \
             patch.object(bridge, "publish_comment_answer") as publish, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, set(), {})
        ask.assert_not_called()
        self.assertEqual(record["question"], "")
        self.assertTrue(record["policy_refusal"])
        self.assertIn("不能替你运行服务", publish.call_args.args[-1])

    def test_doc_answer_uses_comment_delivery_after_review(self):
        record = {"source_type": "doc_comment", "chat_type": "doc_comment",
                  "chat_id": "doc:docx:token123456:comment123456",
                  "file_token": "token123456", "file_type": "docx",
                  "comment_id": "comment123456", "reply_id": "reply123456",
                  "sender_id": "ou_owner123456", "create_time": "1793693832000",
                  "answer": None, "question": None, "sent": False}
        outbox = {"dc:docx:token123456:comment123456:reply123456": record}
        done = set()
        with patch.object(bridge, "load_comment_request", return_value=("总结一下", "文档内容", True, False)), \
             patch.object(bridge, "ask_codex", return_value="这是总结。"), \
             patch.object(bridge, "review_answer", return_value="这是总结。"), \
             patch.object(bridge, "publish_comment_answer") as publish, \
             patch.object(bridge, "send_text") as send_im, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, done, {})
        publish.assert_called_once_with(record, bridge.PROFILE, "这是总结。")
        send_im.assert_not_called()
        self.assertTrue(record["comment_loaded"])
        self.assertEqual(len(done), 1)

    def test_unreadable_document_does_not_block_im_outbox(self):
        doc = {"source_type": "doc_comment", "chat_type": "doc_comment",
               "chat_id": "doc:docx:token123456:comment123456",
               "file_token": "token123456", "file_type": "docx",
               "comment_id": "comment123456", "reply_id": "reply123456",
               "sender_id": "ou_owner123456", "answer": None,
               "question": None, "sent": False}
        im = {"chat_type": "p2p", "chat_id": "oc_12345678",
              "sender_id": "ou_owner123456", "question": "你好", "answer": "已回答",
              "sent": False, "source_excerpt_detected": False, "unsupported_tool": False}
        outbox = {"dc:docx:token123456:comment123456:reply123456": doc,
                  "om_12345678": im}
        done = set()
        with patch.object(bridge, "load_comment_request", side_effect=RuntimeError("missing_scope")), \
             patch.object(bridge, "review_answer", return_value="已回答"), \
             patch.object(bridge, "send_text") as send_im, \
             patch.object(bridge, "save_outbox"), patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, done, {})
        self.assertIn("om_12345678", done)
        self.assertNotIn("dc:docx:token123456:comment123456:reply123456", done)
        send_im.assert_called_once()

    def test_media_message_is_visible_in_codex_without_guessing_attachment(self):
        event = {"type": "im.message.receive_v1", "message_id": "om_12345678",
                 "sender_type": "user", "chat_type": "p2p", "message_type": "image",
                 "chat_id": "oc_12345678", "sender_id": "ou_12345678", "content": "图片"}
        outbox = {}
        with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
            bridge.handle(event, set(), outbox, {})
        self.assertIn("om_12345678", outbox)
        self.assertIn("无法读取", outbox["om_12345678"]["question"])


if __name__ == "__main__":
    unittest.main()
