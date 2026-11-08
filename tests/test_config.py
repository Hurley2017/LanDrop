"""Unit tests for LanDrop configuration."""

import os
import json
import unittest
import tempfile
from pathlib import Path
from landrop.config import get_default_config, load_config, save_config


class TestConfig(unittest.TestCase):
    def test_default_config(self):
        cfg = get_default_config()
        self.assertIn("shared_dir", cfg)
        self.assertIn("webdav_port", cfg)
        self.assertEqual(cfg["webdav_port"], 8080)
        self.assertFalse(cfg["read_only"])

    def test_save_and_load_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            custom_cfg = {
                "shared_dir": str(tmp_path / "MyShare"),
                "device_name": "TestDevice",
                "share_name": "TestShare",
                "webdav_port": 8888,
                "discovery_port": 42424,
                "read_only": True,
                "auto_start_share": False,
            }
            config_file = tmp_path / "test_config.json"
            with open(config_file, "w") as f:
                json.dump(custom_cfg, f)

            with open(config_file, "r") as f:
                loaded = json.load(f)

            self.assertEqual(loaded["webdav_port"], 8888)
            self.assertEqual(loaded["device_name"], "TestDevice")
            self.assertTrue(loaded["read_only"])


if __name__ == "__main__":
    unittest.main()
