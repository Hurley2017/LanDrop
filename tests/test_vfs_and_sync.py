"""Unit and integration tests for P2P Virtual Drive and Mapping Table."""

import os
import time
import json
import unittest
import tempfile
import urllib.request
from pathlib import Path

from landrop.mapping import MappingTable, MappingEntry
from landrop.vfs import VirtualStorageProvider
from landrop.sync import SyncEngine
from landrop.webdav_server import WebDAVServer


class TestP2PVirtualDrive(unittest.TestCase):
    def test_mapping_table_merge_and_conflict_resolution(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            table_file = Path(tmpdir) / "table.json"
            table = MappingTable(table_file=table_file)

            # 1. Register local file
            sample_file = os.path.join(tmpdir, "doc.pdf")
            with open(sample_file, "wb") as f:
                f.write(b"PDF document content")

            entry = table.register_local_file(
                rel_path="doc.pdf",
                physical_path=sample_file,
                node_id="node_a",
                device_name="PC-A",
                my_url="http://192.168.1.10:8080/",
            )
            self.assertEqual(entry.path, "/doc.pdf")
            self.assertTrue(entry.is_local)
            self.assertEqual(entry.version, 1)

            # 2. Simulate receiving remote entries from PC-B
            remote_entries = [
                {
                    "path": "/movies/intro.mp4",
                    "size": 50000000,
                    "mtime": 1700000000.0,
                    "file_hash": "hash_intro",
                    "holder_node_id": "node_b",
                    "holder_name": "PC-B",
                    "holder_url": "http://192.168.1.20:8080/",
                    "version": 1,
                    "deleted": False,
                }
            ]
            merged_count = table.merge_remote_entries(remote_entries, my_node_id="node_a")
            self.assertEqual(merged_count, 1)

            intro_entry = table.get_entry("/movies/intro.mp4")
            self.assertIsNotNone(intro_entry)
            self.assertFalse(intro_entry.is_local)
            self.assertEqual(intro_entry.holder_name, "PC-B")

            # 3. Remote peer updates file to version 2
            updated_entries = [
                {
                    "path": "/movies/intro.mp4",
                    "size": 60000000,
                    "mtime": 1700000100.0,
                    "file_hash": "hash_intro_v2",
                    "holder_node_id": "node_b",
                    "holder_name": "PC-B",
                    "holder_url": "http://192.168.1.20:8080/",
                    "version": 2,
                    "deleted": False,
                }
            ]
            table.merge_remote_entries(updated_entries, my_node_id="node_a")
            intro_entry = table.get_entry("/movies/intro.mp4")
            self.assertEqual(intro_entry.version, 2)
            self.assertEqual(intro_entry.size, 60000000)

            # 4. Total used bytes reflects both local and remote files
            total_used = table.get_total_used_bytes()
            self.assertEqual(total_used, len(b"PDF document content") + 60000000)

    def test_p2p_server_sync_and_raw_streaming(self):
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            port_a = 9110
            port_b = 9111

            table_a = MappingTable(table_file=Path(dir_a) / "map_a.json")
            table_b = MappingTable(table_file=Path(dir_b) / "map_b.json")

            # Create physical file on Node A
            file_a_path = os.path.join(dir_a, "video.mp4")
            sample_video = b"ABCDEFGHIJ" * 1000  # 10,000 bytes
            with open(file_a_path, "wb") as f:
                f.write(sample_video)

            table_a.register_local_file(
                rel_path="video.mp4",
                physical_path=file_a_path,
                node_id="node_a",
                device_name="Host-A",
                my_url=f"http://127.0.0.1:{port_a}/",
            )

            # Spin up Server A with SyncEngine
            sync_a = SyncEngine(
                mapping_table=table_a,
                local_storage_dir=dir_a,
                node_id="node_a",
                device_name="Host-A",
                get_peers_func=lambda: [],
            )
            vfs_a = VirtualStorageProvider(
                local_storage_dir=dir_a,
                mapping_table=table_a,
                quota_bytes=10 * 1024 * 1024 * 1024,
                node_id="node_a",
                device_name="Host-A",
                get_my_url_func=lambda: f"http://127.0.0.1:{port_a}/",
            )
            server_a = WebDAVServer()
            server_a.start(shared_dir=dir_a, port=port_a, provider=vfs_a, sync_engine=sync_a)

            # Spin up Server B with SyncEngine
            sync_b = SyncEngine(
                mapping_table=table_b,
                local_storage_dir=dir_b,
                node_id="node_b",
                device_name="Host-B",
                get_peers_func=lambda: [],
            )
            vfs_b = VirtualStorageProvider(
                local_storage_dir=dir_b,
                mapping_table=table_b,
                quota_bytes=10 * 1024 * 1024 * 1024,
                node_id="node_b",
                device_name="Host-B",
                get_my_url_func=lambda: f"http://127.0.0.1:{port_b}/",
            )
            server_b = WebDAVServer()
            server_b.start(shared_dir=dir_b, port=port_b, provider=vfs_b, sync_engine=sync_b)

            time.sleep(1.0)

            try:
                # Node B initiates sync with Node A
                success = sync_b.sync_with_peer(f"http://127.0.0.1:{port_a}/")
                self.assertTrue(success)

                # Node B should now have video.mp4 in its mapping table!
                entry_on_b = table_b.get_entry("/video.mp4")
                self.assertIsNotNone(entry_on_b)
                self.assertEqual(entry_on_b.size, 10000)
                self.assertFalse(entry_on_b.is_local)
                self.assertEqual(entry_on_b.holder_name, "Host-A")

                # Test Node B streaming raw bytes from Node A (with Range header)
                raw_url = f"http://127.0.0.1:{port_a}/__landrop__/api/raw/video.mp4"
                req = urllib.request.Request(raw_url)
                req.add_header("Range", "bytes=100-199")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 206)
                    chunk = resp.read()
                    self.assertEqual(len(chunk), 100)
                    self.assertEqual(chunk, sample_video[100:200])

            finally:
                server_a.stop()
                server_b.stop()


if __name__ == "__main__":
    unittest.main()
