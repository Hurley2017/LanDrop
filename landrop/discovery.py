"""UDP broadcast peer discovery and sniffer for LanDrop."""

import json
import time
import uuid
import socket
import logging
import threading
from typing import Dict, List, Callable, Optional
from .utils import get_broadcast_ip
from .tailscale import get_tailscale_info

logger = logging.getLogger("landrop.discovery")

PROTOCOL_NAME = "landrop"
PROTOCOL_VERSION = "1.0"
BEACON_INTERVAL = 3.0  # seconds between broadcasts
PEER_TIMEOUT = 10.0    # seconds before peer is marked offline


class PeerInfo:
    """Represents a discovered LanDrop peer on the network."""

    def __init__(self, node_id: str, device_name: str, share_name: str,
                 ip: str, port: int, read_only: bool, last_seen: float):
        self.node_id = node_id
        self.device_name = device_name
        self.share_name = share_name
        self.ip = ip
        self.port = port
        self.read_only = read_only
        self.last_seen = last_seen
        self.network_type = "Tailscale" if ip.startswith("100.") else "Local LAN"

    @property
    def webdav_url(self) -> str:
        return f"http://{self.ip}:{self.port}/"

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "device_name": self.device_name,
            "share_name": self.share_name,
            "ip": self.ip,
            "port": self.port,
            "read_only": self.read_only,
            "url": self.webdav_url,
            "last_seen": self.last_seen,
        }


class DiscoveryService:
    """Broadcasts own availability and sniffs peers running LanDrop."""

    def __init__(self, port: int = 42424,
                 node_id: Optional[str] = None,
                 on_peers_changed: Optional[Callable[[List[PeerInfo]], None]] = None):
        self._port = port
        self._node_id = node_id or uuid.uuid4().hex[:12]
        self._device_name = socket.gethostname()
        self._share_name = "LanDrop"
        self._webdav_port = 8080
        self._read_only = False
        self._sharing_active = False

        self._peers: Dict[str, PeerInfo] = {}
        self._peers_lock = threading.Lock()
        self._on_peers_changed = on_peers_changed

        self._running = False
        self._listen_thread: Optional[threading.Thread] = None
        self._beacon_thread: Optional[threading.Thread] = None
        self._cleaner_thread: Optional[threading.Thread] = None
        self._recv_socket: Optional[socket.socket] = None

    @property
    def node_id(self) -> str:
        return self._node_id

    def update_info(self, device_name: str, share_name: str,
                    webdav_port: int, read_only: bool, sharing_active: bool) -> None:
        """Update broadcast information."""
        self._device_name = device_name
        self._share_name = share_name
        self._webdav_port = webdav_port
        self._read_only = read_only
        self._sharing_active = sharing_active

    def get_peers(self) -> List[PeerInfo]:
        """Return a copy of currently active discovered peers."""
        with self._peers_lock:
            return list(self._peers.values())

    def start(self) -> bool:
        """Start discovery listening and broadcasting."""
        if self._running:
            return True

        self._running = True

        # Setup receiving UDP socket
        try:
            self._recv_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._recv_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._recv_socket.bind(("", self._port))
            self._recv_socket.settimeout(1.0)
        except Exception as e:
            logger.error("Failed to bind UDP discovery socket on port %d: %s", self._port, e)
            self._running = False
            return False

        self._listen_thread = threading.Thread(target=self._listen_loop, daemon=True, name="LanDrop-Sniffer")
        self._beacon_thread = threading.Thread(target=self._beacon_loop, daemon=True, name="LanDrop-Beacon")
        self._cleaner_thread = threading.Thread(target=self._cleaner_loop, daemon=True, name="LanDrop-Cleaner")

        self._listen_thread.start()
        self._beacon_thread.start()
        self._cleaner_thread.start()

        logger.info("Discovery service started on port %d", self._port)
        return True

    def stop(self) -> None:
        """Stop discovery and announce offline status."""
        if not self._running:
            return

        self._send_beacon(status="offline")
        self._running = False

        if self._recv_socket:
            try:
                self._recv_socket.close()
            except Exception:
                pass
            self._recv_socket = None

        with self._peers_lock:
            self._peers.clear()

        if self._on_peers_changed:
            self._on_peers_changed([])

    def _send_beacon(self, status: str = "online") -> None:
        """Transmit a UDP discovery beacon to LAN and Tailscale peers."""
        if not self._sharing_active and status != "offline":
            return

        payload = {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "node_id": self._node_id,
            "device_name": self._device_name,
            "share_name": self._share_name,
            "port": self._webdav_port,
            "read_only": self._read_only,
            "status": status,
        }

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(0.5)
            data = json.dumps(payload).encode("utf-8")
            
            # 1. Broadcast to local LAN
            sock.sendto(data, ("<broadcast>", self._port))

            # 2. Unicast to Tailscale peers if Tailscale is active
            ts_info = get_tailscale_info()
            if ts_info.get("available") and ts_info.get("peers"):
                for peer in ts_info["peers"]:
                    peer_ip = peer.get("ip")
                    if peer_ip:
                        try:
                            sock.sendto(data, (peer_ip, self._port))
                        except Exception:
                            pass

            sock.close()
        except Exception as e:
            logger.debug("Beacon send error: %s", e)

    def _beacon_loop(self) -> None:
        """Periodically broadcast availability while running."""
        while self._running:
            self._send_beacon(status="online")
            time.sleep(BEACON_INTERVAL)

    def _listen_loop(self) -> None:
        """Sniff incoming broadcasts from other LanDrop peers."""
        while self._running and self._recv_socket:
            try:
                data, addr = self._recv_socket.recvfrom(4096)
                self._handle_packet(data, addr[0])
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    logger.debug("Sniffer socket error: %s", e)
                break

    def _handle_packet(self, data: bytes, peer_ip: str) -> None:
        """Parse incoming beacon and update peer catalog."""
        try:
            msg = json.loads(data.decode("utf-8"))
        except Exception:
            return

        if msg.get("protocol") != PROTOCOL_NAME:
            return

        node_id = msg.get("node_id")
        if not node_id or node_id == self._node_id:
            # Ignore self-broadcast
            return

        status = msg.get("status", "online")
        changed = False

        with self._peers_lock:
            if status == "offline":
                if node_id in self._peers:
                    del self._peers[node_id]
                    changed = True
            else:
                peer = PeerInfo(
                    node_id=node_id,
                    device_name=msg.get("device_name", "Unknown PC"),
                    share_name=msg.get("share_name", "Share"),
                    ip=peer_ip,
                    port=int(msg.get("port", 8080)),
                    read_only=bool(msg.get("read_only", False)),
                    last_seen=time.time(),
                )
                self._peers[node_id] = peer
                changed = True

        if changed and self._on_peers_changed:
            self._on_peers_changed(self.get_peers())

    def _cleaner_loop(self) -> None:
        """Periodically remove peers that timed out."""
        while self._running:
            time.sleep(2.0)
            now = time.time()
            changed = False
            with self._peers_lock:
                to_remove = [
                    nid for nid, p in self._peers.items()
                    if now - p.last_seen > PEER_TIMEOUT
                ]
                for nid in to_remove:
                    del self._peers[nid]
                    changed = True

            if changed and self._on_peers_changed:
                self._on_peers_changed(self.get_peers())
