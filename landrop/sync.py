"""P2P Synchronization Engine and WSGI Middleware for LanDrop."""

import os
import re
import json
import logging
import threading
import urllib.request
import urllib.error
import urllib.parse
from typing import List, Optional, Callable
from .mapping import MappingTable, MappingEntry

logger = logging.getLogger("landrop.sync")


class SyncEngine:
    """Coordinates peer-to-peer mapping table synchronization and file streaming."""

    def __init__(
        self,
        mapping_table: MappingTable,
        local_storage_dir: str,
        node_id: str,
        device_name: str,
        get_peers_func: Callable[[], List],
    ):
        self.mapping_table = mapping_table
        self.local_storage_dir = os.path.abspath(local_storage_dir)
        self.node_id = node_id
        self.device_name = device_name
        self.get_peers = get_peers_func
        self._synced_peers = set()

    def sync_with_peer(self, peer_url: str) -> bool:
        """Perform initial two-way exchange of mapping tables with a newly connected peer."""
        clean_url = peer_url.rstrip("/")
        try:
            # 1. Fetch remote mapping table
            fetch_url = f"{clean_url}/__landrop__/api/mapping_table"
            req = urllib.request.Request(fetch_url, headers={"User-Agent": "LanDrop-P2P"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                remote_node_id = data.get("node_id")
                remote_entries = data.get("entries", [])
                # Ensure entries held by this peer point to the live URL that reached it
                for item in remote_entries:
                    if item.get("holder_node_id") == remote_node_id or not item.get("holder_url"):
                        item["holder_url"] = clean_url + "/"
                merged = self.mapping_table.merge_remote_entries(remote_entries, self.node_id)
                logger.info("Synced with peer %s: merged %d entries", clean_url, merged)

            # 2. Push our local mapping table back to peer
            push_url = f"{clean_url}/__landrop__/api/notify_sync"
            payload = json.dumps({
                "node_id": self.node_id,
                "entries": self.mapping_table.serialize(),
            }).encode("utf-8")
            req_push = urllib.request.Request(
                push_url,
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "LanDrop-P2P"}
            )
            with urllib.request.urlopen(req_push, timeout=5) as resp:
                pass
            return True
        except Exception as e:
            logger.debug("Failed sync with peer %s: %s", clean_url, e)
            return False

    def broadcast_update(self, entries: List[MappingEntry]) -> None:
        """Broadcast file changes (creations, edits, deletions) to all active peers."""
        if not entries:
            return

        def _do_broadcast():
            payload = json.dumps({
                "node_id": self.node_id,
                "entries": [e.to_dict() for e in entries],
            }).encode("utf-8")

            peers = self.get_peers()
            for peer in peers:
                peer_url = getattr(peer, "webdav_url", "")
                if not peer_url:
                    continue
                push_url = f"{peer_url.rstrip('/')}/__landrop__/api/notify_sync"
                try:
                    req = urllib.request.Request(
                        push_url,
                        data=payload,
                        headers={"Content-Type": "application/json", "User-Agent": "LanDrop-P2P"}
                    )
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        pass
                except Exception as e:
                    logger.debug("Broadcast to %s failed: %s", push_url, e)

        t = threading.Thread(target=_do_broadcast, daemon=True, name="LanDrop-P2PBroadcast")
        t.start()

    def handle_api_request(self, environ: dict, start_response: Callable):
        """Handle incoming P2P REST requests on /__landrop__/api/*."""
        path_info = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")

        # 1. GET /__landrop__/api/mapping_table
        if path_info == "/__landrop__/api/mapping_table" and method == "GET":
            body = json.dumps({
                "node_id": self.node_id,
                "device_name": self.device_name,
                "entries": self.mapping_table.serialize(),
            }).encode("utf-8")
            start_response("200 OK", [
                ("Content-Type", "application/json"),
                ("Content-Length", str(len(body))),
            ])
            return [body]

        # 2. POST /__landrop__/api/notify_sync
        if path_info == "/__landrop__/api/notify_sync" and method == "POST":
            try:
                content_len = int(environ.get("CONTENT_LENGTH", 0))
                raw_body = environ["wsgi.input"].read(content_len)
                data = json.loads(raw_body.decode("utf-8"))
                remote_entries = data.get("entries", [])
                merged = self.mapping_table.merge_remote_entries(remote_entries, self.node_id)
                res_body = json.dumps({"status": "ok", "merged": merged}).encode("utf-8")
                start_response("200 OK", [
                    ("Content-Type", "application/json"),
                    ("Content-Length", str(len(res_body))),
                ])
                return [res_body]
            except Exception as e:
                err_body = json.dumps({"error": str(e)}).encode("utf-8")
                start_response("400 Bad Request", [("Content-Type", "application/json")])
                return [err_body]

        # 3. GET /__landrop__/api/raw/<rel_path> - Raw stream with HTTP Range support
        if path_info.startswith("/__landrop__/api/raw/"):
            raw_encoded = path_info[len("/__landrop__/api/raw/"):].lstrip("/")
            rel_path = urllib.parse.unquote(raw_encoded)
            phys_path = os.path.normpath(os.path.join(self.local_storage_dir, rel_path))

            # Prevent directory traversal with case-insensitive check on Windows
            norm_phys = os.path.normcase(os.path.abspath(phys_path))
            norm_pool = os.path.normcase(os.path.abspath(self.local_storage_dir))
            if not norm_phys.startswith(norm_pool) or not os.path.isfile(phys_path):
                start_response("404 Not Found", [("Content-Length", "0")])
                return [b""]

            file_size = os.path.getsize(phys_path)
            range_header = environ.get("HTTP_RANGE")

            if range_header and range_header.startswith("bytes="):
                # Handle HTTP Range requests for video/audio seeking
                match = re.search(r"bytes=(\d+)-(\d*)", range_header)
                if match:
                    start_b = int(match.group(1))
                    end_b = int(match.group(2)) if match.group(2) else file_size - 1
                    end_b = min(end_b, file_size - 1)
                    chunk_len = max(0, end_b - start_b + 1)

                    headers = [
                        ("Content-Type", "application/octet-stream"),
                        ("Content-Range", f"bytes {start_b}-{end_b}/{file_size}"),
                        ("Content-Length", str(chunk_len)),
                        ("Accept-Ranges", "bytes"),
                    ]
                    start_response("206 Partial Content", headers)

                    def range_generator():
                        with open(phys_path, "rb") as f:
                            f.seek(start_b)
                            remaining = chunk_len
                            while remaining > 0:
                                read_bytes = min(65536, remaining)
                                chunk = f.read(read_bytes)
                                if not chunk:
                                    break
                                remaining -= len(chunk)
                                yield chunk
                    return range_generator()

            # Full file streaming
            headers = [
                ("Content-Type", "application/octet-stream"),
                ("Content-Length", str(file_size)),
                ("Accept-Ranges", "bytes"),
            ]
            start_response("200 OK", headers)

            def file_generator():
                with open(phys_path, "rb") as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        yield chunk
            return file_generator()

        start_response("404 Not Found", [("Content-Length", "0")])
        return [b""]


class P2PSyncMiddleware:
    """WSGI middleware routing LanDrop API calls before WebDAV processing."""

    def __init__(self, application, sync_engine: SyncEngine):
        self.application = application
        self.sync_engine = sync_engine

    def __call__(self, environ: dict, start_response: Callable):
        path_info = environ.get("PATH_INFO", "")
        if path_info.startswith("/__landrop__/api/"):
            return self.sync_engine.handle_api_request(environ, start_response)
        return self.application(environ, start_response)
