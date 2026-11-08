"""Tailscale integration for LanDrop."""

import os
import sys
import json
import socket
import logging
import subprocess
from typing import Dict, List, Optional

logger = logging.getLogger("landrop.tailscale")

TAILSCALE_PATHS = [
    "tailscale.exe",
    r"C:\Program Files\Tailscale\tailscale.exe",
    r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Tailscale\tailscale.exe"),
]


def find_tailscale_binary() -> Optional[str]:
    """Find tailscale executable path on system."""
    for path in TAILSCALE_PATHS:
        try:
            if os.path.isabs(path) and os.path.exists(path):
                return path
            # Try running as command
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            proc = subprocess.run([path, "version"], capture_output=True, timeout=2, creationflags=flags)
            if proc.returncode == 0:
                return path
        except Exception:
            continue
    return None


def get_tailscale_info() -> Dict:
    """
    Detect Tailscale status, local Tailscale IP, MagicDNS name, and network peers.
    """
    result = {
        "available": False,
        "status": "Not Detected",
        "ip": None,
        "magic_dns": None,
        "tailnet_name": None,
        "peers": [],
    }

    tailscale_bin = find_tailscale_binary()
    if not tailscale_bin:
        # Fallback: check network adapter directly for a 100.x.x.x IP
        ip = _get_tailscale_ip_from_adapters()
        if ip:
            result["available"] = True
            result["status"] = "Running"
            result["ip"] = ip
        return result

    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(
            [tailscale_bin, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=flags,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            backend_state = data.get("BackendState", "")
            result["status"] = backend_state

            if backend_state == "Running":
                result["available"] = True
                # Get local Tailscale IPv4
                for ip_str in data.get("TailscaleIPs", []):
                    if "." in ip_str and not ip_str.startswith("fe80"):
                        result["ip"] = ip_str
                        break

                self_node = data.get("Self", {})
                if not result["ip"]:
                    for ip_str in self_node.get("TailscaleIPs", []):
                        if "." in ip_str:
                            result["ip"] = ip_str
                            break

                dns_name = self_node.get("DNSName", "").rstrip(".")
                result["magic_dns"] = dns_name if dns_name else None

                current_tailnet = data.get("CurrentTailnet", {})
                result["tailnet_name"] = current_tailnet.get("Name")

                # Parse peers for unicast peer discovery
                peer_list = []
                for _, pinfo in data.get("Peer", {}).items():
                    hostname = pinfo.get("HostName", "Unknown")
                    online = pinfo.get("Online", False)
                    peer_ip = None
                    for pip in pinfo.get("TailscaleIPs", []):
                        if "." in pip:
                            peer_ip = pip
                            break
                    if peer_ip:
                        peer_list.append({
                            "hostname": hostname,
                            "ip": peer_ip,
                            "online": online,
                            "os": pinfo.get("OS", "unknown"),
                        })
                result["peers"] = peer_list
    except Exception as e:
        logger.debug("Tailscale status query failed: %s", e)

    return result


def _get_tailscale_ip_from_adapters() -> Optional[str]:
    """Scan local network interfaces for 100.64.0.0/10 address."""
    try:
        # Check hostname IPs
        for info in socket.getaddrinfo(socket.gethostname(), None):
            ip = info[4][0]
            if ip.startswith("100."):
                # Basic check for 100.64.0.0/10 range
                parts = ip.split(".")
                if len(parts) == 4 and parts[0] == "100":
                    second = int(parts[1])
                    if 64 <= second <= 127:
                        return ip
    except Exception:
        pass
    return None
