"""Distributed Mapping Table for P2P Virtual Drive."""

import os
import json
import time
import hashlib
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional, Callable

logger = logging.getLogger("landrop.mapping")

DEFAULT_MAPPING_FILE = Path.home() / ".landrop" / "mapping_table.json"


def compute_file_hash(filepath: str, max_bytes: int = 10 * 1024 * 1024) -> str:
    """Compute fast SHA-256 hash of a file (sampling first 10MB for speed on large files)."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(max_bytes)
            hasher.update(chunk)
        return hasher.hexdigest()[:16]
    except Exception:
        return ""


class MappingEntry:
    """Represents a single file entry in the distributed virtual storage."""

    def __init__(
        self,
        path: str,
        size: int,
        mtime: float,
        file_hash: str,
        holder_node_id: str,
        holder_name: str,
        holder_url: str,
        is_local: bool,
        version: int = 1,
        deleted: bool = False,
    ):
        # Normalize path to forward slashes starting with '/'
        norm_path = "/" + path.replace("\\", "/").lstrip("/")
        self.path = norm_path
        self.size = size
        self.mtime = mtime
        self.file_hash = file_hash
        self.holder_node_id = holder_node_id
        self.holder_name = holder_name
        self.holder_url = holder_url.rstrip("/") + "/" if holder_url else ""
        self.is_local = is_local
        self.version = version
        self.deleted = deleted

    @property
    def filename(self) -> str:
        return os.path.basename(self.path)

    @property
    def is_directory(self) -> bool:
        return self.size == 0 and self.path.endswith("/")

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "mtime": self.mtime,
            "file_hash": self.file_hash,
            "holder_node_id": self.holder_node_id,
            "holder_name": self.holder_name,
            "holder_url": self.holder_url,
            "version": self.version,
            "deleted": self.deleted,
        }

    @classmethod
    def from_dict(cls, data: dict, my_node_id: str) -> "MappingEntry":
        is_local = (data.get("holder_node_id") == my_node_id)
        return cls(
            path=data.get("path", ""),
            size=int(data.get("size", 0)),
            mtime=float(data.get("mtime", 0.0)),
            file_hash=data.get("file_hash", ""),
            holder_node_id=data.get("holder_node_id", ""),
            holder_name=data.get("holder_name", "Remote Peer"),
            holder_url=data.get("holder_url", ""),
            is_local=is_local,
            version=int(data.get("version", 1)),
            deleted=bool(data.get("deleted", False)),
        )


class MappingTable:
    """Manages the distributed catalog of all files across connected peers."""

    def __init__(
        self,
        table_file: Path = DEFAULT_MAPPING_FILE,
        on_table_changed: Optional[Callable[[], None]] = None,
    ):
        self._table_file = Path(table_file)
        self._entries: Dict[str, MappingEntry] = {}
        self._lock = threading.RLock()
        self._on_table_changed = on_table_changed
        self.load()

    def get_entry(self, path: str) -> Optional[MappingEntry]:
        norm_path = "/" + path.replace("\\", "/").lstrip("/")
        with self._lock:
            entry = self._entries.get(norm_path)
            if entry and not entry.deleted:
                return entry
            return None

    def get_all_active_entries(self) -> List[MappingEntry]:
        """Return all non-deleted files in the virtual filesystem."""
        with self._lock:
            return [e for e in self._entries.values() if not e.deleted]

    def get_total_used_bytes(self) -> int:
        """Calculate total physical bytes stored across the whole virtual pool."""
        with self._lock:
            return sum(e.size for e in self._entries.values() if not e.deleted)

    def get_local_used_bytes(self) -> int:
        """Calculate bytes physically stored on this machine."""
        with self._lock:
            return sum(e.size for e in self._entries.values() if not e.deleted and e.is_local)

    def register_local_file(
        self,
        rel_path: str,
        physical_path: str,
        node_id: str,
        device_name: str,
        my_url: str,
    ) -> MappingEntry:
        """Register or update a locally stored physical file."""
        norm_path = "/" + rel_path.replace("\\", "/").lstrip("/")
        stat = os.stat(physical_path)
        size = stat.st_size
        mtime = stat.st_mtime
        fhash = compute_file_hash(physical_path)

        with self._lock:
            prev = self._entries.get(norm_path)
            ver = (prev.version + 1) if prev else 1

            entry = MappingEntry(
                path=norm_path,
                size=size,
                mtime=mtime,
                file_hash=fhash,
                holder_node_id=node_id,
                holder_name=device_name,
                holder_url=my_url,
                is_local=True,
                version=ver,
                deleted=False,
            )
            self._entries[norm_path] = entry
            self.save()

        if self._on_table_changed:
            self._on_table_changed()
        return entry

    def mark_deleted(self, rel_path: str, node_id: str) -> bool:
        """Mark a virtual file as deleted (tombstone) to propagate across peers."""
        norm_path = "/" + rel_path.replace("\\", "/").lstrip("/")
        with self._lock:
            prev = self._entries.get(norm_path)
            if prev and not prev.deleted:
                prev.deleted = True
                prev.version += 1
                prev.mtime = time.time()
                self.save()
                if self._on_table_changed:
                    self._on_table_changed()
                return True
        return False

    def merge_remote_entries(self, remote_data: List[dict], my_node_id: str) -> int:
        """
        Merge remote peer mapping entries using version / timestamp conflict resolution.
        Returns the number of updated/added entries.
        """
        changed = 0
        with self._lock:
            for item in remote_data:
                entry = MappingEntry.from_dict(item, my_node_id)
                curr = self._entries.get(entry.path)

                if curr is None:
                    # New entry discovered from remote peer
                    self._entries[entry.path] = entry
                    changed += 1
                else:
                    # Resolve conflict: higher version wins, or later timestamp
                    if entry.version > curr.version:
                        self._entries[entry.path] = entry
                        changed += 1
                    elif entry.version == curr.version and entry.mtime > curr.mtime:
                        self._entries[entry.path] = entry
                        changed += 1
                    elif entry.holder_node_id == curr.holder_node_id and entry.holder_url != curr.holder_url:
                        # Update URL if peer IP changed
                        curr.holder_url = entry.holder_url
                        changed += 1

            if changed > 0:
                self.save()

        if changed > 0 and self._on_table_changed:
            self._on_table_changed()
        return changed

    def scan_local_storage(
        self,
        storage_dir: str,
        node_id: str,
        device_name: str,
        my_url: str,
    ) -> None:
        """Scan physical storage folder and register any newly placed files."""
        if not os.path.exists(storage_dir):
            os.makedirs(storage_dir, exist_ok=True)
            return

        changed = False
        with self._lock:
            for root, _, files in os.walk(storage_dir):
                for filename in files:
                    full_path = os.path.join(root, filename)
                    rel = os.path.relpath(full_path, storage_dir)
                    norm_path = "/" + rel.replace("\\", "/")

                    stat = os.stat(full_path)
                    entry = self._entries.get(norm_path)
                    if (
                        not entry
                        or entry.deleted
                        or entry.size != stat.st_size
                        or abs(entry.mtime - stat.st_mtime) > 1.0
                    ):
                        fhash = compute_file_hash(full_path)
                        ver = (entry.version + 1) if entry else 1
                        self._entries[norm_path] = MappingEntry(
                            path=norm_path,
                            size=stat.st_size,
                            mtime=stat.st_mtime,
                            file_hash=fhash,
                            holder_node_id=node_id,
                            holder_name=device_name,
                            holder_url=my_url,
                            is_local=True,
                            version=ver,
                            deleted=False,
                        )
                        changed = True
                    elif entry.is_local and (entry.holder_node_id != node_id or entry.holder_url != my_url or entry.holder_name != device_name):
                        entry.holder_node_id = node_id
                        entry.holder_name = device_name
                        entry.holder_url = my_url
                        changed = True

            if changed:
                self.save()

        if changed and self._on_table_changed:
            self._on_table_changed()

    def serialize(self) -> List[dict]:
        """Export all entries for peer-to-peer transmission."""
        with self._lock:
            return [e.to_dict() for e in self._entries.values()]

    def save(self) -> None:
        """Persist mapping table to disk."""
        try:
            self._table_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._table_file, "w", encoding="utf-8") as f:
                json.dump(self.serialize(), f, indent=2)
        except Exception as e:
            logger.error("Failed to save mapping table: %s", e)

    def load(self) -> None:
        """Load mapping table from disk."""
        if not self._table_file.exists():
            return
        try:
            with open(self._table_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                with self._lock:
                    self._entries.clear()
                    for item in data:
                        entry = MappingEntry.from_dict(item, my_node_id="")
                        self._entries[entry.path] = entry
        except Exception as e:
            logger.error("Failed to load mapping table: %s", e)
