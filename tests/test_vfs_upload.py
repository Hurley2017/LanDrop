"""Tests for file upload, replace, and atomic move operations."""

import os
import time
import unittest
import tempfile
import urllib.request
from pathlib import Path

from landrop.mapping import MappingTable
from landrop.vfs import VirtualStorageProvider
from landrop.sync import SyncEngine
from landrop.webdav_server import WebDAVServer


class TestVFSFileUpload(unittest.TestCase):
    def test_file_upload_replace_and_move(self):
        with tempfile.TemporaryDirectory() as storage_dir:
            port = 9330
            mapping_table = MappingTable(table_file=Path(storage_dir) / "map.json")
            sync_engine = SyncEngine(
                mapping_table=mapping_table,
                local_storage_dir=storage_dir,
                node_id="test_node",
                device_name="Test-PC",
                get_peers_func=lambda: [],
            )
            vfs = VirtualStorageProvider(
                local_storage_dir=storage_dir,
                mapping_table=mapping_table,
                quota_bytes=10 * 1024 * 1024 * 1024,
                node_id="test_node",
                device_name="Test-PC",
                get_my_url_func=lambda: f"http://127.0.0.1:{port}/",
            )
            server = WebDAVServer()
            server.start(shared_dir=storage_dir, port=port, provider=vfs, sync_engine=sync_engine)

            time.sleep(1.0)
            base_url = f"http://127.0.0.1:{port}/"

            try:
                # 1. Initial PUT /notes.txt (First file creation)
                content1 = b"Initial notes content version 1"
                req1 = urllib.request.Request(f"{base_url}notes.txt", data=content1, method="PUT")
                with urllib.request.urlopen(req1, timeout=5) as resp:
                    self.assertIn(resp.status, (200, 201, 204))

                # Verify file exists on disk and in mapping table
                self.assertTrue(os.path.exists(os.path.join(storage_dir, "notes.txt")))
                entry1 = mapping_table.get_entry("/notes.txt")
                self.assertIsNotNone(entry1)
                self.assertEqual(entry1.size, len(content1))

                # 2. PUT /notes.txt again (Replace existing file)
                content2 = b"Overwritten notes content version 2 with more data!"
                req2 = urllib.request.Request(f"{base_url}notes.txt", data=content2, method="PUT")
                with urllib.request.urlopen(req2, timeout=5) as resp:
                    self.assertIn(resp.status, (200, 201, 204))

                with open(os.path.join(storage_dir, "notes.txt"), "rb") as f:
                    self.assertEqual(f.read(), content2)

                # 3. Simulate Windows Explorer temp-file upload & MOVE overwrite
                # Upload to temp.tmp
                content3 = b"Content uploaded to temp file then moved"
                req_temp = urllib.request.Request(f"{base_url}temp.tmp", data=content3, method="PUT")
                with urllib.request.urlopen(req_temp, timeout=5) as resp:
                    self.assertIn(resp.status, (200, 201, 204))

                # MOVE temp.tmp -> notes.txt (with Overwrite header)
                req_move = urllib.request.Request(f"{base_url}temp.tmp", method="MOVE")
                req_move.add_header("Destination", f"{base_url}notes.txt")
                req_move.add_header("Overwrite", "T")
                with urllib.request.urlopen(req_move, timeout=5) as resp:
                    self.assertIn(resp.status, (200, 201, 204))

                # Verify final content and mapping table
                with open(os.path.join(storage_dir, "notes.txt"), "rb") as f:
                    self.assertEqual(f.read(), content3)

                entry_final = mapping_table.get_entry("/notes.txt")
                self.assertIsNotNone(entry_final)
                self.assertEqual(entry_final.size, len(content3))

            finally:
                server.stop()


if __name__ == "__main__":
    unittest.main()
