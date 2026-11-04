"""Attachment references stay metadata until Codex explicitly fetches originals."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bridge
from attachment_mcp import dispatch
from attachment_store import fetch, register
from im_attachments import download_original, file_refs
from privacy import unsupported_tool_used


class AttachmentTests(unittest.TestCase):
    def test_file_reference_is_metadata_for_text_audio_and_video(self):
        key = "file_v3_00174_example"
        self.assertEqual(file_refs(f'分析 <file key="{key}" name="指标.csv"/>', "post"),
                         [{"key": key, "name": "指标.csv"}])
        for kind, name in (("file", "指标.csv"), ("audio", "录音.mp3"),
                           ("video", "录像.mp4")):
            self.assertEqual(file_refs(json.dumps({"file_key": key,
                                                    "file_name": name}), kind),
                             [{"key": key, "name": name}])

    def test_download_preserves_original_bytes_and_is_on_demand(self):
        original = b"\x00\xff\x01not-a-text-file"
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "video.mp4"
            def fake_run(argv, **kwargs):
                self.assertEqual(argv[argv.index("--as") + 1], "bot")
                self.assertEqual(argv[argv.index("--file-key") + 1], "file_test")
                Path(kwargs["cwd"], argv[argv.index("--output") + 1]).write_bytes(original)
                return type("Result", (), {"returncode": 0, "stdout": "{}", "stderr": ""})()
            with patch("im_attachments.subprocess.run", side_effect=fake_run) as run:
                self.assertFalse(target.exists())
                result = download_original("om_test", {"key": "file_test", "name": "video.mp4"},
                                           "bot", target)
                self.assertEqual(result["bytes"], len(original))
                self.assertEqual(target.read_bytes(), original)
                download_original("om_test", {"key": "file_test", "name": "video.mp4"},
                                  "bot", target)
                self.assertEqual(run.call_count, 1)

    def test_missing_download_scope_is_explicit(self):
        error = '{"error":{"message":"im:message:readonly is required"}}'
        with tempfile.TemporaryDirectory() as directory, \
             patch("im_attachments.subprocess.run", return_value=type(
                 "Result", (), {"returncode": 4, "stdout": "", "stderr": error})()):
            with self.assertRaisesRegex(PermissionError, "消息只读权限"):
                download_original("om_test", {"key": "file_test", "name": "指标.csv"},
                                  "bot", Path(directory) / "a.csv")

    def test_post_file_is_registered_without_downloading_before_answer(self):
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
            self.assertEqual(incoming["attachment_handles"][0]["name"], "指标.csv")
            self.assertNotIn("attachment_context", incoming)
            return "可先核对指标口径。"
        with patch.object(bridge, "register_attachments", return_value=[{
                 "handle": "opaque", "name": "指标.csv", "key": "file_test123"}]) as reg, \
             patch.object(bridge, "ask_codex", side_effect=fake_answer), \
             patch.object(bridge, "review_answer", return_value="可先核对指标口径。"), \
             patch.object(bridge, "send_text"), patch.object(bridge, "save_outbox"), \
             patch.object(bridge, "save_done"):
            bridge.flush_outbox(outbox, set(), {})
            reg.assert_called_once()

    def test_unissued_handle_cannot_fetch(self):
        with patch("attachment_store._load", return_value={}):
            with self.assertRaisesRegex(ValueError, "不存在或已过期"):
                fetch("arbitrary", project_dir=Path("/tmp"), profile="bot")

    def test_mcp_exposes_one_tool_and_only_metadata(self):
        listed = dispatch({"method": "tools/list"})
        self.assertEqual([item["name"] for item in listed["tools"]],
                         ["fetch_feishu_attachment"])
        with patch("attachment_mcp.fetch", return_value={"path": "/tmp/a.mp4",
                                                          "bytes": 3, "name": "a.mp4"}), \
             patch.dict("os.environ", {"FEISHU_CODEX_PROJECT_DIR": "/tmp"}):
            result = dispatch({"method": "tools/call", "params": {
                "name": "fetch_feishu_attachment", "arguments": {"handle": "issued"}}})
        self.assertIn("/tmp/a.mp4", result["content"][0]["text"])

    def test_only_this_message_handle_is_allowed_in_codex_turn(self):
        item = {"type": "mcpToolCall", "server": "futou_attachment",
                "tool": "fetch_feishu_attachment", "arguments": {"handle": "current"}}
        self.assertFalse(unsupported_tool_used([item], allow_history=False,
                                                attachment_handles={"current"}))
        self.assertTrue(unsupported_tool_used([item], allow_history=False,
                                               attachment_handles={"other"}))


if __name__ == "__main__":
    unittest.main()
