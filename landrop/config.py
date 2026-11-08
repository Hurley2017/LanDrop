"""Configuration management for LanDrop."""

import os
import json
import uuid
import socket
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".landrop"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_SHARE_DIR = Path.home() / "LanDropShare"
DEFAULT_STORAGE_POOL_DIR = DEFAULT_CONFIG_DIR / "storage"


def get_default_device_name() -> str:
    """Return the hostname or fallback device name."""
    try:
        return socket.gethostname()
    except Exception:
        return "LanDrop-Device"


def get_default_config() -> dict:
    """Return default configuration dictionary."""
    DEFAULT_SHARE_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_STORAGE_POOL_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "node_id": uuid.uuid4().hex[:12],
        "shared_dir": str(DEFAULT_SHARE_DIR),
        "device_name": get_default_device_name(),
        "share_name": "LanDrop",
        "webdav_port": 8080,
        "discovery_port": 42424,
        "read_only": False,
        "auto_start_share": True,
        # Virtual Storage Drive Settings
        "virtual_storage_gb": 20,
        "storage_pool_dir": str(DEFAULT_STORAGE_POOL_DIR),
        "mount_drive_letter": "Z:",
        "auto_mount_drive": True,
    }


def load_config() -> dict:
    """Load configuration from disk, creating default if absent."""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if DEFAULT_CONFIG_FILE.exists():
        try:
            with open(DEFAULT_CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                config = get_default_config()
                config.update(saved)
                if "node_id" not in saved or not saved["node_id"]:
                    config["node_id"] = uuid.uuid4().hex[:12]
                    save_config(config)
                return config
        except Exception:
            pass
    config = get_default_config()
    save_config(config)
    return config


def save_config(config: dict) -> None:
    """Save configuration to disk."""
    DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEFAULT_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)
