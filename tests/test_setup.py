"""Verify generated launchd settings without installing or starting a service."""

import plistlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import setup


class SetupTests(unittest.TestCase):
    def test_relative_project_path_is_repo_relative(self):
        self.assertEqual(setup.project_path({"project_dir": "./feishu-message"}),
                         setup.ROOT / "feishu-message")

    def test_plist_carries_config_and_runtime_paths(self):
        config = Path("/tmp/bridge-test-config.json")
        paths = {"codex": "/Applications/ChatGPT.app/Contents/MacOS/codex",
                 "lark-cli": "/Users/example/.nvm/bin/lark-cli",
                 "node": "/Users/example/.nvm/bin/node"}
        data = {"launchd_label": "cn.example.feishu-codex-bridge"}
        plist = setup.build_plist(data, paths, config, Path("/tmp/bridge-logs"))
        self.assertEqual(plist["Label"], data["launchd_label"])
        self.assertEqual(plist["EnvironmentVariables"]["FEISHU_CODEX_CONFIG"], str(config))
        self.assertIn("/Users/example/.nvm/bin", plist["EnvironmentVariables"]["PATH"])
        self.assertIn("bridge.py", plist["ProgramArguments"][1])
        plistlib.loads(plistlib.dumps(plist))

    def test_uninstall_stops_only_own_service(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({"launchd_label": "cn.example.bridge"}))
            agent = Path(temp) / "bridge.plist"
            agent.write_bytes(plistlib.dumps({
                "Label": "cn.example.bridge",
                "ProgramArguments": ["/usr/bin/python3", str(setup.ROOT / "bridge.py")],
                "WorkingDirectory": str(setup.ROOT),
            }))
            with patch.object(setup, "plist_path", return_value=agent), \
                    patch.object(setup.subprocess, "run") as run:
                run.return_value.returncode = 0
                setup.uninstall(config)
            self.assertFalse(agent.exists())
            self.assertTrue(config.exists())
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args.args[0][:2], ["launchctl", "bootout"])

    def test_uninstall_refuses_foreign_service(self):
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / "config.json"
            config.write_text(json.dumps({"launchd_label": "cn.example.bridge"}))
            agent = Path(temp) / "bridge.plist"
            agent.write_bytes(plistlib.dumps({
                "Label": "cn.example.bridge",
                "ProgramArguments": ["/usr/bin/python3", "/other/bridge.py"],
                "WorkingDirectory": "/other",
            }))
            with patch.object(setup, "plist_path", return_value=agent), \
                    patch.object(setup.subprocess, "run") as run:
                with self.assertRaises(RuntimeError):
                    setup.uninstall(config)
            self.assertTrue(agent.exists())
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
