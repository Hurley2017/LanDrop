"""Utility functions for LanDrop."""

import os
import sys
import socket
import subprocess
import webbrowser
from typing import Optional
from PIL import Image, ImageDraw


def get_local_ip() -> str:
    """Retrieve the primary local IPv4 address of this machine."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Does not actually transmit packets, just resolves routing
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def get_broadcast_ip() -> str:
    """Get the broadcast address for LAN discovery."""
    return "255.255.255.255"


def open_path_or_url(target: str) -> None:
    """Open a folder path or WebDAV URL in Windows Explorer or default handler."""
    if sys.platform == "win32":
        try:
            # os.startfile opens URLs or local directory paths in Windows Explorer
            os.startfile(target)
        except Exception:
            webbrowser.open(target)
    elif sys.platform == "darwin":
        subprocess.run(["open", target], check=False)
    else:
        subprocess.run(["xdg-open", target], check=False)


def mount_network_drive(webdav_url: str) -> Optional[str]:
    """
    On Windows, attempts to map the WebDAV URL to an available network drive letter.
    Returns the mapped drive letter (e.g. 'Z:') or None.
    """
    if sys.platform != "win32":
        open_path_or_url(webdav_url)
        return None

    try:
        # Use 'net use * <url>' to map next available drive letter
        cmd = ["net", "use", "*", webdav_url, "/persistent:no"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            # Output format: "Drive Z: is now connected to http://..."
            for word in proc.stdout.split():
                if len(word) == 2 and word[1] == ":" and word[0].isalpha():
                    return word
        # If mapping fails (e.g. WebClient service not started), open URL directly
        open_path_or_url(webdav_url)
    except Exception:
        open_path_or_url(webdav_url)
    return None


def get_asset_path(filename: str) -> str:
    """Resolve asset path whether running from source or PyInstaller bundle."""
    if hasattr(sys, "_MEIPASS"):
        base_dir = getattr(sys, "_MEIPASS")
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "assets", filename)


def create_app_icon(size: int = 64, color: str = "#2563EB") -> Image.Image:
    """
    Generate a clean LanDrop icon dynamically using Pillow.
    A modern rounded folder with a network antenna/drop badge.
    """
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Base folder tab
    tab_x0, tab_y0 = int(size * 0.15), int(size * 0.22)
    tab_x1, tab_y1 = int(size * 0.50), int(size * 0.38)
    draw.rounded_rectangle([tab_x0, tab_y0, tab_x1, tab_y1], radius=size // 16, fill="#3B82F6")

    # Folder body
    body_x0, body_y0 = int(size * 0.12), int(size * 0.32)
    body_x1, body_y1 = int(size * 0.88), int(size * 0.82)
    draw.rounded_rectangle([body_x0, body_y0, body_x1, body_y1], radius=size // 12, fill=color)

    # Network / Drop symbol (wifi-like arc / down-arrow in white)
    cx, cy = size // 2, int(size * 0.57)
    arrow_w = size // 5
    arrow_h = size // 6
    # Arrow stem
    draw.rectangle([cx - size // 20, cy - arrow_h, cx + size // 20, cy], fill="white")
    # Arrow head pointing down
    draw.polygon([
        (cx - arrow_w // 2, cy),
        (cx + arrow_w // 2, cy),
        (cx, cy + arrow_h)
    ], fill="white")

    return image
