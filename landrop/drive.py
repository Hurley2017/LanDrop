"""Windows virtual drive letter mounting and management for LanDrop."""

import os
import sys
import string
import logging
import subprocess
from typing import Optional, List

logger = logging.getLogger("landrop.drive")


def get_used_drive_letters() -> List[str]:
    """Return a list of all currently assigned drive letters (e.g. ['C', 'D'])."""
    used = []
    if sys.platform == "win32":
        try:
            import ctypes
            bitmask = ctypes.windll.kernel32.GetLogicalDrives()
            for letter in string.ascii_uppercase:
                if bitmask & 1:
                    used.append(letter)
                bitmask >>= 1
        except Exception:
            for letter in string.ascii_uppercase:
                if os.path.exists(f"{letter}:\\"):
                    used.append(letter)
    return used


def get_available_drive_letter(preferred: str = "Z") -> Optional[str]:
    """Find the best available drive letter, preferring 'Z', then counting backwards."""
    used = set(get_used_drive_letters())
    preferred_clean = preferred.rstrip(":").upper()
    if preferred_clean not in used:
        return f"{preferred_clean}:"

    # Search backwards from Z down to E
    for char in "ZYXWVUTSRQPONMLKJHGFED":
        if char not in used:
            return f"{char}:"
    return None


def is_drive_mounted(drive_letter: str = "Z:") -> bool:
    """Check if the given drive letter is currently mounted."""
    letter = drive_letter.rstrip(":").upper() + ":"
    return letter.rstrip(":") in get_used_drive_letters()


def mount_drive(webdav_url: str, drive_letter: str = "Z:") -> Optional[str]:
    """
    Mount the local WebDAV virtual storage as a Windows drive letter.
    Returns the mounted drive letter on success, or None on failure.
    """
    if sys.platform != "win32":
        return None

    target_letter = drive_letter.rstrip(":").upper() + ":"

    # If already mounted to something, check or unmount if needed
    if is_drive_mounted(target_letter):
        # Check if already connected to our URL
        proc = subprocess.run(["net", "use"], capture_output=True, text=True, timeout=5)
        if target_letter in proc.stdout:
            logger.info("Drive %s is already mounted", target_letter)
            return target_letter
        # If taken by another device, find alternative
        alt = get_available_drive_letter(target_letter)
        if not alt:
            logger.warning("No available drive letters to mount %s", webdav_url)
            return None
        target_letter = alt

    # Strip trailing slash for Windows WebClient compatibility
    url_cleaned = webdav_url.rstrip("/")

    cmd = ["net", "use", target_letter, url_cleaned, "/persistent:no"]
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12, creationflags=flags)
        if res.returncode == 0:
            logger.info("Successfully mounted %s to %s", url_cleaned, target_letter)
            return target_letter
        else:
            logger.warning("net use failed: %s (%s)", res.stdout.strip(), res.stderr.strip())
            # Fallback: Try with trailing slash
            cmd_alt = ["net", "use", target_letter, url_cleaned + "/", "/persistent:no"]
            res2 = subprocess.run(cmd_alt, capture_output=True, text=True, timeout=10, creationflags=flags)
            if res2.returncode == 0:
                logger.info("Successfully mounted %s to %s with trailing slash", url_cleaned, target_letter)
                return target_letter
    except Exception as e:
        logger.error("Error executing net use mount: %s", e)

    return None


def unmount_drive(drive_letter: str = "Z:") -> bool:
    """Unmount a previously mapped network drive letter."""
    if sys.platform != "win32":
        return True

    target_letter = drive_letter.rstrip(":").upper() + ":"
    if not is_drive_mounted(target_letter):
        return True

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        res = subprocess.run(
            ["net", "use", target_letter, "/delete", "/y"],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=flags,
        )
        if res.returncode == 0:
            logger.info("Successfully unmounted %s", target_letter)
            return True
    except Exception as e:
        logger.error("Error unmounting %s: %s", target_letter, e)
    return False
