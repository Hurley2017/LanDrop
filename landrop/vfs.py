"""Virtual Filesystem Provider for LanDrop P2P Virtual Drive."""

import os
import io
import time
import urllib.request
import mimetypes
import logging
from typing import Optional, List, Callable
from wsgidav.dav_provider import DAVProvider, DAVNonCollection, _DAVResource
from wsgidav.fs_dav_provider import FileResource, FolderResource
from .mapping import MappingTable, MappingEntry

logger = logging.getLogger("landrop.vfs")


class RemoteVirtualFileResource(DAVNonCollection):
    """Represents a virtual file that is physically stored on a remote peer."""

    def __init__(self, path: str, environ: dict, entry: MappingEntry, provider: "VirtualStorageProvider"):
        super().__init__(path, environ)
        self.entry = entry
        self.provider = provider

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

    def end_write(self, is_modified: bool):
        super().end_write(is_modified)
        if is_modified and os.path.exists(self._file_path):
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
            entry = self.provider.mapping_table.get_entry(self.path)
            if entry:
                self.provider.on_file_changed([entry])


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

        os.makedirs(self._file_path, exist_ok=True)
        file_path = os.path.join(self._file_path, name)
        with open(file_path, "wb"):
            pass

        res_path = os.path.join(self.path, name).replace("\\", "/")
        return LocalVirtualFileResource(res_path, self.environ, file_path, self.provider)

    def create_collection(self, name: str) -> _DAVResource:
        new_dir = os.path.join(self._file_path, name)
        os.makedirs(new_dir, exist_ok=True)
        res_path = os.path.join(self.path, name).replace("\\", "/") + "/"
        return VirtualFolderResource(res_path, self.environ, new_dir, self.provider)


class VirtualStorageProvider(DAVProvider):
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
        super().__init__()
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

    def _loc_to_file_path(self, path: str) -> str:
        rel = path.lstrip("/")
        return os.path.normpath(os.path.join(self.local_storage_dir, rel))

    def get_resource_inst(self, path: str, environ: dict) -> Optional[_DAVResource]:
        norm_path = "/" + path.replace("\\", "/").strip("/")
        if path == "/":
            norm_path = "/"

        phys_path = self._loc_to_file_path(norm_path)

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
