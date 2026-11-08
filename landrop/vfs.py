"""Virtual Filesystem Provider for LanDrop P2P Virtual Drive."""

import os
import io
import time
import shutil
import urllib.request
import urllib.parse
import mimetypes
import logging
from typing import Optional, List, Callable
from wsgidav.dav_provider import DAVNonCollection, _DAVResource, DAVError, HTTP_FORBIDDEN
from wsgidav.fs_dav_provider import FilesystemProvider, FileResource, FolderResource
from wsgidav import util
from .mapping import MappingTable, MappingEntry

logger = logging.getLogger("landrop.vfs")


class RemoteStreamReader:
    """A seekable file-like stream wrapping an HTTP endpoint for WsgiDAV and VLC."""

    def __init__(self, candidate_urls: List[str], rel_path: str, filesize: int):
        self.candidate_urls = candidate_urls if isinstance(candidate_urls, list) else [candidate_urls]
        self.rel_path = rel_path
        self.filesize = filesize
        self.position = 0
        self._resp = None
        self._active_base_url = self.candidate_urls[0] if self.candidate_urls else ""

    def _ensure_stream(self, start_pos: int = 0):
        if self._resp is not None:
            if self.position == start_pos:
                return
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None

        encoded_path = urllib.parse.quote(self.rel_path)
        urls_to_try = []
        if self._active_base_url:
            urls_to_try.append(self._active_base_url)
        for u in self.candidate_urls:
            if u and u not in urls_to_try:
                urls_to_try.append(u)

        for base_url in urls_to_try:
            raw_url = f"{base_url.rstrip('/')}/__landrop__/api/raw{encoded_path}"
            req = urllib.request.Request(raw_url, headers={"User-Agent": "LanDrop-VFS"})
            if start_pos > 0:
                req.add_header("Range", f"bytes={start_pos}-")

            try:
                resp = urllib.request.urlopen(req, timeout=5)
                self._resp = resp
                self.position = start_pos
                self._active_base_url = base_url
                return
            except Exception as e:
                logger.debug("Failed to open remote stream at %s: %s", raw_url, e)

        self._resp = None

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = max(0, offset)
        elif whence == os.SEEK_CUR:
            target = max(0, self.position + offset)
        elif whence == os.SEEK_END:
            target = max(0, self.filesize + offset)
        else:
            raise ValueError(f"Invalid whence: {whence}")

        # If already at target position and stream is ready, nothing to do!
        if target == self.position and self._resp is not None:
            return self.position

        self._ensure_stream(target)
        return self.position

    def tell(self) -> int:
        return self.position

    def read(self, size: int = -1) -> bytes:
        if self._resp is None:
            self._ensure_stream(self.position)
        if self._resp is None:
            return b""

        try:
            chunk = self._resp.read(size) if size >= 0 else self._resp.read()
            self.position += len(chunk)
            return chunk
        except Exception as e:
            logger.error("Error reading from remote stream at %s: %s", self._active_base_url, e)
            return b""

    def close(self):
        if self._resp:
            try:
                self._resp.close()
            except Exception:
                pass
            self._resp = None


class RemoteVirtualFileResource(DAVNonCollection):
    """Represents a virtual file physically held on a remote peer, with overwrite support."""

    def __init__(self, path: str, environ: dict, entry: MappingEntry, provider: "VirtualStorageProvider"):
        super().__init__(path, environ)
        self.entry = entry
        self.provider = provider
        self._target_file_path = self.provider._loc_to_file_path(self.path, self.environ)

    def get_content_length(self) -> int:
        return self.entry.size

    def get_content_type(self) -> str:
        ctype, _ = mimetypes.guess_type(self.entry.path)
        return ctype or "application/octet-stream"

    def get_last_modified(self) -> float:
        return self.entry.mtime

    def get_etag(self) -> Optional[str]:
        if self.entry.file_hash:
            return self.entry.file_hash.strip('"')
        return f"{int(self.entry.mtime)}-{self.entry.size}"

    def get_creation_date(self) -> Optional[float]:
        return self.entry.mtime

    def support_etag(self) -> bool:
        return True

    def support_ranges(self) -> bool:
        return True

    def get_content(self):
        """Stream file content directly from the remote peer holder using a seekable stream."""
        candidate_urls = self.provider.resolve_holder_urls(self.entry)
        return RemoteStreamReader(candidate_urls, self.entry.path, self.entry.size)

    def begin_write(self, *, content_type=None):
        """Allow local client to replace remote file with a new local copy."""
        os.makedirs(os.path.dirname(self._target_file_path), exist_ok=True)
        return open(self._target_file_path, "wb")

    def end_write(self, *, with_errors: bool = False, **kwargs):
        if not with_errors and os.path.exists(self._target_file_path):
            entry = self.provider.mapping_table.register_local_file(
                rel_path=self.path,
                physical_path=self._target_file_path,
                node_id=self.provider.node_id,
                device_name=self.provider.device_name,
                my_url=self.provider.get_my_url(),
            )
            if self.provider.on_file_changed:
                self.provider.on_file_changed([entry])

    def delete(self):
        """Mark deleted in mapping table and propagate across peers."""
        self.provider.mapping_table.mark_deleted(self.path, self.provider.node_id)
        if self.provider.on_file_changed:
            self.provider.on_file_changed([self.entry])


class LocalVirtualFileResource(FileResource):
    """Represents a file physically stored in the local storage pool."""

    def __init__(self, path: str, environ: dict, file_path: str, provider: "VirtualStorageProvider"):
        super().__init__(path, environ, file_path)
        self.provider = provider

    def handle_move(self, dest_path: str) -> bool:
        """Handle MOVE request atomically using os.replace to prevent 'already exists' errors."""
        fp_dest = self.provider._loc_to_file_path(dest_path, self.environ)
        try:
            os.makedirs(os.path.dirname(fp_dest), exist_ok=True)
            os.replace(self._file_path, fp_dest)
            self._file_path = fp_dest

            self.provider.mapping_table.mark_deleted(self.path, self.provider.node_id)
            entry = self.provider.mapping_table.register_local_file(
                rel_path=dest_path,
                physical_path=fp_dest,
                node_id=self.provider.node_id,
                device_name=self.provider.device_name,
                my_url=self.provider.get_my_url(),
            )
            if self.provider.on_file_changed:
                self.provider.on_file_changed([entry])
            return True
        except Exception as e:
            logger.debug("Native handle_move fallback to copy_move: %s", e)
            return False

    def copy_move_single(self, dest_path: str, *, is_move: bool):
        super().copy_move_single(dest_path, is_move=is_move)
        fp_dest = self.provider._loc_to_file_path(dest_path, self.environ)
        if is_move and os.path.exists(self._file_path):
            try:
                os.remove(self._file_path)
            except Exception:
                pass
            self.provider.mapping_table.mark_deleted(self.path, self.provider.node_id)

        if os.path.exists(fp_dest):
            entry = self.provider.mapping_table.register_local_file(
                rel_path=dest_path,
                physical_path=fp_dest,
                node_id=self.provider.node_id,
                device_name=self.provider.device_name,
                my_url=self.provider.get_my_url(),
            )
            if self.provider.on_file_changed:
                self.provider.on_file_changed([entry])

    def begin_write(self, *, content_type=None):
        if self.provider.readonly:
            raise DAVError(HTTP_FORBIDDEN)
        os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
        return open(self._file_path, "wb")

    def end_write(self, *, with_errors: bool = False, **kwargs):
        super().end_write(with_errors=with_errors)
        if not with_errors and os.path.exists(self._file_path):
            entry = self.provider.mapping_table.register_local_file(
                rel_path=self.path,
                physical_path=self._file_path,
                node_id=self.provider.node_id,
                device_name=self.provider.device_name,
                my_url=self.provider.get_my_url(),
            )
            if self.provider.on_file_changed:
                self.provider.on_file_changed([entry])

    def delete(self):
        super().delete()
        self.provider.mapping_table.mark_deleted(self.path, self.provider.node_id)
        if self.provider.on_file_changed:
            tombstone = MappingEntry(
                path=self.path,
                size=0,
                mtime=time.time(),
                file_hash="",
                holder_node_id=self.provider.node_id,
                holder_name=self.provider.device_name,
                holder_url=self.provider.get_my_url(),
                is_local=True,
                deleted=True,
            )
            self.provider.on_file_changed([tombstone])


class VirtualFolderResource(FolderResource):
    """Folder resource merging local physical directories and remote mapping entries."""

    def __init__(self, path: str, environ: dict, file_path: str, provider: "VirtualStorageProvider"):
        super().__init__(path, environ, file_path)
        self.provider = provider

    def get_member_names(self) -> List[str]:
        names = set()
        if os.path.isdir(self._file_path):
            try:
                for fn in os.listdir(self._file_path):
                    names.add(fn)
            except Exception:
                pass

        prefix = self.path.rstrip("/") + "/"
        if self.path == "/":
            prefix = "/"

        for entry in self.provider.mapping_table.get_all_active_entries():
            if entry.path.startswith(prefix) and entry.path != prefix:
                remainder = entry.path[len(prefix):]
                top_part = remainder.split("/")[0]
                if top_part:
                    names.add(top_part)

        return sorted(list(names))

    def create_empty_resource(self, name: str) -> _DAVResource:
        if self.provider.get_available_bytes() <= 0:
            raise Exception("Virtual storage quota exceeded")

        path = util.join_uri(self.path, name)
        fp = self.provider._loc_to_file_path(path, self.environ)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "wb"):
            pass

        return self.provider.get_resource_inst(path, self.environ)

    def create_collection(self, name: str) -> _DAVResource:
        path = util.join_uri(self.path, name)
        fp = self.provider._loc_to_file_path(path, self.environ)
        os.makedirs(fp, exist_ok=True)
        return self.provider.get_resource_inst(path, self.environ)


class VirtualStorageProvider(FilesystemProvider):
    """
    WSGI-DAV Provider implementing the distributed virtual storage.
    Presents a unified directory tree combining local physical files and remote peer files.
    """

    def __init__(
        self,
        local_storage_dir: str,
        mapping_table: MappingTable,
        quota_bytes: int,
        node_id: str,
        device_name: str,
        get_my_url_func: Callable[[], str],
        on_file_changed: Optional[Callable[[List[MappingEntry]], None]] = None,
        get_peers_func: Optional[Callable[[], List]] = None,
    ):
        super().__init__(local_storage_dir, readonly=False)
        self.local_storage_dir = os.path.abspath(local_storage_dir)
        os.makedirs(self.local_storage_dir, exist_ok=True)
        self.mapping_table = mapping_table
        self.quota_bytes = quota_bytes
        self.node_id = node_id
        self.device_name = device_name
        self.get_my_url = get_my_url_func
        self.on_file_changed = on_file_changed
        self.get_peers_func = get_peers_func

    def resolve_holder_urls(self, entry: MappingEntry) -> List[str]:
        """Resolve ordered reachable candidate URLs for the peer hosting the file."""
        candidates = []
        # 1. Match active discovered peers by node_id or device_name
        if self.get_peers_func:
            for peer in self.get_peers_func():
                url = getattr(peer, "webdav_url", "")
                if not url:
                    continue
                if peer.node_id == entry.holder_node_id or (peer.device_name and peer.device_name == entry.holder_name):
                    if url not in candidates:
                        candidates.append(url)

        # 2. Add entry's recorded holder_url
        if entry.holder_url:
            clean = entry.holder_url.rstrip("/") + "/"
            if clean not in candidates:
                candidates.append(clean)

        # 3. Add any other discovered live peers as fallback
        if self.get_peers_func:
            for peer in self.get_peers_func():
                url = getattr(peer, "webdav_url", "")
                if url and url not in candidates:
                    candidates.append(url)

        return candidates

    def resolve_holder_url(self, entry: MappingEntry) -> str:
        """Resolve the primary live reachable URL for the peer hosting the file."""
        urls = self.resolve_holder_urls(entry)
        return urls[0] if urls else entry.holder_url

    def set_quota(self, quota_bytes: int) -> None:
        self.quota_bytes = quota_bytes

    def get_used_bytes(self) -> int:
        return self.mapping_table.get_total_used_bytes()

    def get_available_bytes(self) -> int:
        return max(0, self.quota_bytes - self.get_used_bytes())

    def _loc_to_file_path(self, path: str, environ: dict = None) -> str:
        """Convert resource path to unicode absolute file path in local storage."""
        rel = path.strip("/").replace("/", os.sep)
        return os.path.abspath(os.path.join(self.local_storage_dir, rel))

    def get_resource_inst(self, path: str, environ: dict) -> Optional[_DAVResource]:
        norm_path = "/" + path.replace("\\", "/").strip("/")
        if path == "/":
            norm_path = "/"

        phys_path = self._loc_to_file_path(norm_path, environ)

        # 1. Root folder
        if norm_path == "/":
            return VirtualFolderResource("/", environ, self.local_storage_dir, self)

        # 2. Local physical directory
        if os.path.isdir(phys_path):
            return VirtualFolderResource(norm_path, environ, phys_path, self)

        # 3. Check if path is a virtual directory created by remote peers
        prefix = norm_path + "/"
        for entry in self.mapping_table.get_all_active_entries():
            if entry.path.startswith(prefix):
                return VirtualFolderResource(norm_path, environ, phys_path, self)

        # 4. Local physical file
        if os.path.isfile(phys_path):
            return LocalVirtualFileResource(norm_path, environ, phys_path, self)

        # 5. Remote virtual file from mapping table
        entry = self.mapping_table.get_entry(norm_path)
        if entry:
            if entry.is_local and os.path.isfile(phys_path):
                return LocalVirtualFileResource(norm_path, environ, phys_path, self)
            else:
                return RemoteVirtualFileResource(norm_path, environ, entry, self)

        return None
