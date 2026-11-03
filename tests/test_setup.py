"""Verify generated launchd settings without installing or starting a service."""

import plistlib
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
