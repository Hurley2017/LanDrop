"""LanDrop - Main Application Entrypoint."""

import sys
import os
import argparse
import logging
import threading
import tkinter as tk
from landrop.config import load_config, save_config
from landrop.utils import open_path_or_url
from landrop.webdav_server import WebDAVServer
from landrop.discovery import DiscoveryService
from landrop.tray import TrayIconManager
from landrop.ui import LanDropAppUI
from landrop.mapping import MappingTable
from landrop.vfs import VirtualStorageProvider
from landrop.sync import SyncEngine
from landrop.drive import mount_drive, unmount_drive, is_drive_mounted

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s"
)
logger = logging.getLogger("landrop.main")


def parse_args():
    parser = argparse.ArgumentParser(description="LanDrop - P2P Distributed Virtual Storage & Peer Sniffer")
    parser.add_argument("--storage", "-s", type=str, help="Path to local physical storage pool")
    parser.add_argument("--port", "-p", type=int, help="WebDAV server port (default: 8080)")
    parser.add_argument("--quota", "-q", type=int, help="Virtual storage size in GB (default: 20)")
    parser.add_argument("--drive", "-d", type=str, default="Z:", help="Drive letter to mount (default: Z:)")
    parser.add_argument("--no-mount", action="store_true", help="Do not automatically mount drive letter")
    parser.add_argument("--minimized", "-m", action="store_true", help="Start minimized in system tray")
    parser.add_argument("--read-only", "-r", action="store_true", help="Start share in read-only mode")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()

    if args.storage:
        config["storage_pool_dir"] = os.path.abspath(args.storage)
    if args.port:
        config["webdav_port"] = args.port
    if args.quota:
        config["virtual_storage_gb"] = args.quota
    if args.drive:
        config["mount_drive_letter"] = args.drive
    if args.no_mount:
        config["auto_mount_drive"] = False
    if args.read_only:
        config["read_only"] = True

    port = config.get("webdav_port", 8080)
    device_name = config.get("device_name", "LanDrop-PC")
    read_only = config.get("read_only", False)
    auto_start = config.get("auto_start_share", True)
    storage_pool_dir = config.get("storage_pool_dir")
    virtual_storage_gb = config.get("virtual_storage_gb", 20)
    mount_letter = config.get("mount_drive_letter", "Z:")
    auto_mount = config.get("auto_mount_drive", True)

    # 1. Initialize Distributed Mapping Table
    mapping_table = MappingTable()

    # 2. Initialize WebDAV Server & Discovery Service
    server = WebDAVServer()

    discovery = DiscoveryService(
        port=config.get("discovery_port", 42424)
    )

    # 3. Initialize P2P Sync Engine
    sync_engine = SyncEngine(
        mapping_table=mapping_table,
        local_storage_dir=storage_pool_dir,
        node_id=discovery.node_id,
        device_name=device_name,
        get_peers_func=discovery.get_peers,
    )

    # 4. Initialize Virtual Storage Provider
    quota_bytes = virtual_storage_gb * 1024 * 1024 * 1024
    vfs_provider = VirtualStorageProvider(
        local_storage_dir=storage_pool_dir,
        mapping_table=mapping_table,
        quota_bytes=quota_bytes,
        node_id=discovery.node_id,
        device_name=device_name,
        get_my_url_func=server.get_url,
        on_file_changed=lambda entries: sync_engine.broadcast_update(entries),
        get_peers_func=discovery.get_peers,
    )

    # 5. Connect discovery events with auto-sync
    known_peers_synced = set()

    def on_peers_changed(peers):
        # Notify UI on main thread
        try:
            if root and root.winfo_exists():
                root.after(0, lambda: ui.update_peer_list(peers))
        except Exception:
            pass

        # Auto-sync with any newly discovered peer
        for p in peers:
            if p.node_id not in known_peers_synced:
                known_peers_synced.add(p.node_id)
                def _do_sync(peer_url=p.webdav_url):
                    if sync_engine.sync_with_peer(peer_url):
                        try:
                            if root and root.winfo_exists():
                                root.after(0, ui.refresh_virtual_files)
                        except Exception:
                            pass
                threading.Thread(target=_do_sync, daemon=True).start()

    discovery._on_peers_changed = on_peers_changed

    # Connect mapping table changes to UI update
    def on_table_changed():
        try:
            if root and root.winfo_exists():
                root.after(0, ui.refresh_virtual_files)
        except Exception:
            pass

    mapping_table._on_table_changed = on_table_changed

    # 6. Create Tkinter Root & UI
    root = tk.Tk()

    def clean_exit():
        logger.info("Shutting down LanDrop...")
        try:
            tray.stop()
        except Exception:
            pass
        try:
            discovery.stop()
        except Exception:
            pass
        try:
            server.stop()
        except Exception:
            pass
        try:
            # Unmount virtual drive letter
            letter = config.get("mount_drive_letter", "Z:")
            if is_drive_mounted(letter):
                unmount_drive(letter)
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    ui = LanDropAppUI(
        root=root,
        server=server,
        discovery=discovery,
        mapping_table=mapping_table,
        vfs_provider=vfs_provider,
        sync_engine=sync_engine,
        config=config,
        on_exit_requested=clean_exit
    )

    # 7. Start Server and Discovery
    if auto_start:
        server.start(
            shared_dir=storage_pool_dir,
            port=port,
            read_only=read_only,
            provider=vfs_provider,
            sync_engine=sync_engine,
        )
        discovery.update_info(
            device_name=device_name,
            share_name="LanDrop",
            webdav_port=port,
            read_only=read_only,
            sharing_active=True
        )

    discovery.start()

    # 8. Mount Windows Drive (Z:) if enabled
    if auto_mount and sys.platform == "win32":
        def _mount_bg():
            url = f"http://127.0.0.1:{port}/"
            res = mount_drive(url, drive_letter=mount_letter)
            if res:
                logger.info("Virtual storage mounted to %s", res)
                try:
                    if root and root.winfo_exists():
                        root.after(0, lambda: ui._update_mount_display(True, res))
                except Exception:
                    pass
        threading.Thread(target=_mount_bg, daemon=True).start()

    # 9. Initialize System Tray
    tray = TrayIconManager(
        on_open_ui=lambda: root.after(0, ui.show_window),
        on_open_folder=lambda: root.after(0, ui._open_virtual_drive),
        on_toggle_share=lambda: root.after(0, ui._toggle_share),
        on_quit=lambda: root.after(0, clean_exit),
        is_sharing_active=lambda: server.is_running
    )
    tray.start()

    # Minimize on startup if flag given
    if args.minimized:
        root.withdraw()

    # 10. Run Tkinter Main Event Loop
    try:
        root.mainloop()
    except (KeyboardInterrupt, SystemExit):
        clean_exit()


if __name__ == "__main__":
    main()
