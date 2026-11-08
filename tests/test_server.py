"""Integration tests for LanDrop WebDAV server."""

import os
import time
import unittest
import tempfile
import urllib.request
from landrop.webdav_server import WebDAVServer


class TestWebDAVServer(unittest.TestCase):
    def test_webdav_server_get_and_stream(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a test media file
            sample_data = b"0123456789" * 100  # 1000 bytes
            test_file = os.path.join(tmpdir, "media.mp4")
            with open(test_file, "wb") as f:
                f.write(sample_data)

            server = WebDAVServer()
            port = 8992
            success = server.start(shared_dir=tmpdir, port=port, read_only=False)
            self.assertTrue(success)
            time.sleep(1.0)  # Wait for server to bind

            base_url = f"http://127.0.0.1:{port}/"

            try:
                # 1. Test directory listing / root GET
                req = urllib.request.Request(base_url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertIn(resp.status, (200, 207))

                # 2. Test full GET of media file
                file_url = f"{base_url}media.mp4"
                req = urllib.request.Request(file_url)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 200)
                    content = resp.read()
                    self.assertEqual(content, sample_data)

                # 3. Test HTTP Range request (crucial for streaming media seeking in VLC / media players)
                req = urllib.request.Request(file_url)
                req.add_header("Range", "bytes=10-19")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 206)  # Partial Content
                    range_content = resp.read()
                    self.assertEqual(len(range_content), 10)
                    self.assertEqual(range_content, sample_data[10:20])

                # 4. Test HTTP PUT (uploading a file)
                put_url = f"{base_url}uploaded.txt"
                upload_data = b"Hello from remote computer!"
                req = urllib.request.Request(put_url, data=upload_data, method="PUT")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertIn(resp.status, (200, 201, 204))

                # Verify uploaded file is now on disk
                uploaded_file_path = os.path.join(tmpdir, "uploaded.txt")
                self.assertTrue(os.path.exists(uploaded_file_path))
                with open(uploaded_file_path, "rb") as f:
                    self.assertEqual(f.read(), upload_data)

            finally:
                server.stop()
                time.sleep(0.5)
                self.assertFalse(server.is_running)


if __name__ == "__main__":
    unittest.main()
