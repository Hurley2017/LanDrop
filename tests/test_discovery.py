"""Unit tests for LanDrop peer discovery and sniffer."""

import time
import json
import unittest
from landrop.discovery import DiscoveryService, PeerInfo, PROTOCOL_NAME, PROTOCOL_VERSION


class TestDiscovery(unittest.TestCase):
    def test_peer_info_structure(self):
        peer = PeerInfo(
            node_id="peer123",
            device_name="Laptop-Pro",
            share_name="LanDrop",
            ip="192.168.1.50",
            port=8080,
            read_only=False,
            last_seen=time.time()
        )
        self.assertEqual(peer.webdav_url, "http://192.168.1.50:8080/")
        d = peer.to_dict()
        self.assertEqual(d["node_id"], "peer123")
        self.assertEqual(d["device_name"], "Laptop-Pro")

    def test_discovery_sniffer_handling(self):
        discovered = []

        def on_changed(peers):
            nonlocal discovered
            discovered = peers

        service = DiscoveryService(port=45678, on_peers_changed=on_changed)

        # 1. Simulate packet from another peer
        remote_payload = {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "node_id": "other_node_999",
            "device_name": "LivingRoom-PC",
            "share_name": "LanDrop",
            "port": 8080,
            "read_only": False,
            "status": "online",
        }
        raw_data = json.dumps(remote_payload).encode("utf-8")
        service._handle_packet(raw_data, "192.168.1.88")

        self.assertEqual(len(discovered), 1)
        p = discovered[0]
        self.assertEqual(p.node_id, "other_node_999")
        self.assertEqual(p.device_name, "LivingRoom-PC")
        self.assertEqual(p.ip, "192.168.1.88")
        self.assertEqual(p.webdav_url, "http://192.168.1.88:8080/")

        # 2. Simulate self-packet (should be ignored)
        self_payload = {
            "protocol": PROTOCOL_NAME,
            "version": PROTOCOL_VERSION,
            "node_id": service.node_id,
            "device_name": "My-PC",
            "share_name": "LanDrop",
            "port": 8080,
            "read_only": False,
            "status": "online",
        }
        service._handle_packet(json.dumps(self_payload).encode("utf-8"), "127.0.0.1")
        # Discovered list should still only contain the other node
        self.assertEqual(len(discovered), 1)

        # 3. Simulate remote peer going offline
        offline_payload = dict(remote_payload)
        offline_payload["status"] = "offline"
        service._handle_packet(json.dumps(offline_payload).encode("utf-8"), "192.168.1.88")
        self.assertEqual(len(discovered), 0)


if __name__ == "__main__":
    unittest.main()
