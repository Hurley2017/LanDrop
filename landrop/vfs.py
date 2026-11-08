"""Virtual Filesystem Provider for LanDrop P2P Virtual Drive."""

import os
import io
import time
import shutil
import urllib.request
import mimetypes
import logging
from typing import Optional, List, Callable
from wsgidav.dav_provider import DAVNonCollection, _DAVResource, DAVError, HTTP_FORBIDDEN
from wsgidav.fs_dav_provider import FilesystemProvider, FileResource, FolderResource
from wsgidav import util
from .mapping import MappingTable, MappingEntry

logger = logging.getLogger("landrop.vfs")


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

    def get_etag(self) -> str:
        return f'"{self.entry.file_hash}"'

    def support_etag(self) -> bool:
        return True

    def support_ranges(self) -> bool:
        return True

    def get_content(self):
        """Stream file content directly from the remote peer holder."""
        raw_url = f"{self.entry.holder_url.rstrip('/')}/__landrop__/api/raw{self.entry.path}"
        req = urllib.request.Request(raw_url)
        http_range = self.environ.get("HTTP_RANGE")
        if http_range:
            req.add_header("Range", http_range)

        try:
            resp = urllib.request.urlopen(req, timeout=30)
            return resp
        except Exception as e:
            logger.error("Failed to stream remote file %s: %s", raw_url, e)
            return io.BytesIO(b"")

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
            # os.replace is atomic and automatically overwrites any existing file on Windows
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
        # 1. Names from local disk
        if os.path.isdir(self._file_path):
            try:
                for fn in os.listdir(self._file_path):
                    names.add(fn)
            except Exception:
                pass

        # 2. Names from mapping table for this folder path
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
        # Check quota before accepting new upload
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
