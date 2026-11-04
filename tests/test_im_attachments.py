"""Check file-marker extraction, local CSV parsing, and the bot download contract."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge
from im_attachments import _file_text, file_refs, load_file


class AttachmentTests(unittest.TestCase):
    def test_post_marker_and_file_message_extract_the_same_resource(self):
        key = "file_v3_00174_example"
        self.assertEqual(file_refs(
            f'请分析 <file key="{key}" name="指标.csv"/>', "post"),
            [{"key": key, "name": "指标.csv"}])
        self.assertEqual(file_refs(
            '{"file_key":"file_v3_00174_example","file_name":"指标.csv"}', "file"),
            [{"key": key, "name": "指标.csv"}])

    def test_csv_is_read_from_bytes_with_non_utf8_fallback(self):
        content = "指标,口径\n达成率,已完成/全部\n"
        rendered = _file_text("指标.csv", content.encode("gb18030"))
        self.assertIn("第 2 行", rendered)
        self.assertIn("已完成/全部", rendered)

    def test_download_uses_bot_identity_and_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            def fake_run(argv, **kwargs):
                self.assertIn("--as", argv)
                self.assertEqual(argv[argv.index("--as") + 1], "bot")
                Path(kwargs["cwd"], "attachment.csv").write_text("指标,口径\n达成率,已完成/全部")
                return type("Result", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()
            with patch("im_attachments.subprocess.run", side_effect=fake_run):
                result = load_file("om_test", {"key": "file_test", "name": "指标.csv"},
                                   "bot", Path(directory))
            self.assertIn("达成率", result)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_missing_download_scope_is_explicit(self):
        error = '{"error":{"message":"im:message:readonly is required"}}'
        with tempfile.TemporaryDirectory() as directory, \
             patch("im_attachments.subprocess.run", return_value=type(
                 "Result", (), {"returncode": 4, "stdout": "", "stderr": error})()):
            with self.assertRaisesRegex(PermissionError, "消息只读权限"):
                load_file("om_test", {"key": "file_test", "name": "指标.csv"},
                          "bot", Path(directory))

    def test_post_file_is_loaded_before_codex_answer(self):
        event = {"type": "im.message.receive_v1", "message_id": "om_file123",
                 "sender_type": "user", "chat_type": "p2p", "message_type": "post",
                 "chat_id": "oc_file123", "sender_id": "ou_file123",
                 "content": '分析指标 <file key="file_test123" name="指标.csv"/>'}
        outbox = {}
        with patch.object(bridge, "save_outbox"), patch.object(bridge, "flush_outbox"):
            bridge.handle(event, set(), outbox, {})
        record = outbox["om_file123"]
        self.assertEqual(record["attachment_refs"],
                         [{"key": "file_test123", "name": "指标.csv"}])
        def fake_answer(_message_id, incoming, _threads, _outbox):
            self.assertIn("第 1 行", incoming["attachment_context"])
            return "可先核对指标口径。"
        with patch.object(bridge, "load_file", return_value="第 1 行：指标、口径"), \
             patch.object(bridge, "ask_codex", side_effect=fake_answer), \
             patch.object(bridge, "review_answer", return_value="可先核对指标口径。"), \
             patch.object(bridge, "send_text"), patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, set(), {})


if __name__ == "__main__":
    unittest.main()
