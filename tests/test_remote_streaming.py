"""Test WebDAV streaming of remote peer files via seekable RemoteStreamReader."""

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


class TestRemoteWebDAVStreaming(unittest.TestCase):
    def test_remote_file_webdav_get_and_copy(self):
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            port_a = 9771
            port_b = 9772

            table_a = MappingTable(table_file=Path(dir_a) / "map_a.json")
            table_b = MappingTable(table_file=Path(dir_b) / "map_b.json")

            # Create a file on Node A
            file_a_path = os.path.join(dir_a, "video_clip.mp4")
            test_content = b"VIDEO_HEADER_12345" * 200  # 3600 bytes
            with open(file_a_path, "wb") as f:
                f.write(test_content)

            table_a.register_local_file(
                rel_path="video_clip.mp4",
                physical_path=file_a_path,
                node_id="node_a",
                device_name="Node-A",
                my_url=f"http://127.0.0.1:{port_a}/",
            )

            # Node A server
            sync_a = SyncEngine(table_a, dir_a, "node_a", "Node-A", lambda: [])
            vfs_a = VirtualStorageProvider(dir_a, table_a, 10 * 1024 * 1024 * 1024, "node_a", "Node-A", lambda: f"http://127.0.0.1:{port_a}/")
            server_a = WebDAVServer()
            server_a.start(dir_a, port=port_a, provider=vfs_a, sync_engine=sync_a)

            # Node B server
            sync_b = SyncEngine(table_b, dir_b, "node_b", "Node-B", lambda: [])
            vfs_b = VirtualStorageProvider(dir_b, table_b, 10 * 1024 * 1024 * 1024, "node_b", "Node-B", lambda: f"http://127.0.0.1:{port_b}/")
            server_b = WebDAVServer()
            server_b.start(dir_b, port=port_b, provider=vfs_b, sync_engine=sync_b)

            time.sleep(1.0)

            try:
                # 1. Node B syncs mapping table with Node A
                success = sync_b.sync_with_peer(f"http://127.0.0.1:{port_a}/")
                self.assertTrue(success)

                # 2. Node B accesses the remote file via its OWN local WebDAV mount:
                # GET http://127.0.0.1:9772/video_clip.mp4
                # (This simulates Windows Explorer copying the file from Z: drive on Node B)
                local_b_url = f"http://127.0.0.1:{port_b}/video_clip.mp4"
                req = urllib.request.Request(local_b_url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    downloaded = resp.read()
                    self.assertEqual(len(downloaded), len(test_content))
                    self.assertEqual(downloaded, test_content)

                # 3. Test HTTP Range request on Node B's WebDAV (simulates VLC streaming remote file from Z:)
                req_range = urllib.request.Request(local_b_url)
                req_range.add_header("Range", "bytes=50-99")
                with urllib.request.urlopen(req_range, timeout=5) as resp:
                    self.assertEqual(resp.status, 206)
                    range_bytes = resp.read()
                    self.assertEqual(len(range_bytes), 50)
                    self.assertEqual(range_bytes, test_content[50:100])

            finally:
                server_a.stop()
                server_b.stop()


if __name__ == "__main__":
    unittest.main()
