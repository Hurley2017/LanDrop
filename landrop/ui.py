"""Native Tkinter setup UI and peer manager for LanDrop."""

import os
import sys
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional, Callable
from .config import load_config, save_config
from .utils import get_local_ip, open_path_or_url, mount_network_drive, get_asset_path
from .tailscale import get_tailscale_info
from .discovery import PeerInfo, DiscoveryService
from .webdav_server import WebDAVServer
from .drive import mount_drive, unmount_drive, is_drive_mounted, get_available_drive_letter
from .mapping import MappingTable, MappingEntry
from .vfs import VirtualStorageProvider
from .sync import SyncEngine


def format_bytes(num_bytes: int) -> str:
    """Format bytes into readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


class LanDropAppUI:
    """Main application window for LanDrop."""

    def __init__(
        self,
        root: tk.Tk,
        server: WebDAVServer,
        discovery: DiscoveryService,
        mapping_table: MappingTable,
        vfs_provider: VirtualStorageProvider,
        sync_engine: SyncEngine,
        config: dict,
        on_exit_requested: Callable[[], None],
    ):
        self.root = root
        self.server = server
        self.discovery = discovery
        self.mapping_table = mapping_table
        self.vfs_provider = vfs_provider
        self.sync_engine = sync_engine
        self.config = config
        self.on_exit_requested = on_exit_requested

        self.root.title("LanDrop - Distributed Virtual Drive")
        self.root.geometry("740x620")
        self.root.minsize(640, 520)

        icon_file = get_asset_path("icon.ico")
        if os.path.exists(icon_file):
            try:
                self.root.iconbitmap(icon_file)
            except Exception:
                pass

        # Style configuration
        self._setup_styles()

        # Intercept close window to minimize to system tray
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        # Track UI state
        self._discovered_peers: List[PeerInfo] = []
        self._virtual_files: List[MappingEntry] = []

        self._build_ui()
        self._update_status_display()
        self.refresh_virtual_files()

    def _setup_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista" if sys.platform == "win32" else "clam")
        except Exception:
            pass

        style.configure("Header.TLabel", font=("Segoe UI", 16, "bold"), foreground="#1E293B")
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#64748B")
        style.configure("StatusBadgeActive.TLabel", font=("Segoe UI", 9, "bold"), foreground="#15803D", background="#DCFCE7", padding=4)
        style.configure("StatusBadgeInactive.TLabel", font=("Segoe UI", 9, "bold"), foreground="#475569", background="#F1F5F9", padding=4)
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding="14")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header section
        header_frame = ttk.Frame(main_frame)
        header_frame.pack(fill=tk.X, pady=(0, 10))

        title_box = ttk.Frame(header_frame)
        title_box.pack(side=tk.LEFT)
        ttk.Label(title_box, text="LanDrop", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="P2P Distributed Virtual Storage & LAN Share", style="SubHeader.TLabel").pack(anchor="w")

        self.status_badge = ttk.Label(header_frame, text="● Sharing Active", style="StatusBadgeActive.TLabel")
        self.status_badge.pack(side=tk.RIGHT, pady=6)

        # Tabs (Notebook)
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Virtual Drive (Z:) & Distributed Storage
        self.tab_vfs = ttk.Frame(notebook, padding="12")
        notebook.add(self.tab_vfs, text="  Virtual Drive (Z:)  ")
        self._build_vfs_tab(self.tab_vfs)

        # Tab 2: Discovered Peers (Sniffer)
        self.tab_peers = ttk.Frame(notebook, padding="12")
        notebook.add(self.tab_peers, text="  Discovered Peers  ")
        self._build_peers_tab(self.tab_peers)

        # Tab 3: Network & Share Settings
        self.tab_share = ttk.Frame(notebook, padding="12")
        notebook.add(self.tab_share, text="  Network Settings  ")
        self._build_share_tab(self.tab_share)

        # Tab 4: How It Works Guide
        self.tab_guide = ttk.Frame(notebook, padding="12")
        notebook.add(self.tab_guide, text="  How It Works  ")
        self._build_guide_tab(self.tab_guide)

        # Footer / Status bar
        footer_frame = ttk.Frame(main_frame)
        footer_frame.pack(fill=tk.X, pady=(10, 0))

        self.status_text = ttk.Label(footer_frame, text="Ready", font=("Segoe UI", 8), foreground="#475569")
        self.status_text.pack(side=tk.LEFT)

        btn_box = ttk.Frame(footer_frame)
        btn_box.pack(side=tk.RIGHT)

        ttk.Button(btn_box, text="Minimize to Tray", command=self.hide_window).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="Exit LanDrop", command=self.on_exit_requested).pack(side=tk.LEFT)

    def _build_vfs_tab(self, parent: ttk.Frame) -> None:
        # Drive Mount Status Card
        mount_frame = ttk.LabelFrame(parent, text="Windows Virtual Drive Mount", padding="10")
        mount_frame.pack(fill=tk.X, pady=(0, 8))

        row_m = ttk.Frame(mount_frame)
        row_m.pack(fill=tk.X, pady=2)

        ttk.Label(row_m, text="Drive Letter:", width=12).pack(side=tk.LEFT)
        self.var_drive_letter = tk.StringVar(value=self.config.get("mount_drive_letter", "Z:"))
        self.combo_drive = ttk.Combobox(row_m, textvariable=self.var_drive_letter, values=["Z:", "Y:", "X:", "W:", "V:"], width=5, state="readonly")
        self.combo_drive.pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_mount_status = ttk.Label(row_m, text="Checking mount...", font=("Segoe UI", 9, "bold"), foreground="#15803D")
        self.lbl_mount_status.pack(side=tk.LEFT, padx=(0, 16))

        self.btn_open_drive = ttk.Button(row_m, text="Open Z: Drive", command=self._open_virtual_drive)
        self.btn_open_drive.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_toggle_mount = ttk.Button(row_m, text="Remount", command=self._toggle_mount_drive)
        self.btn_toggle_mount.pack(side=tk.LEFT)

        # Storage Quota Configuration Card
        quota_frame = ttk.LabelFrame(parent, text="Virtual Storage Pool & Quota", padding="10")
        quota_frame.pack(fill=tk.X, pady=(0, 8))

        q_row1 = ttk.Frame(quota_frame)
        q_row1.pack(fill=tk.X, pady=2)

        ttk.Label(q_row1, text="Virtual Storage Size:", width=18).pack(side=tk.LEFT)
        self.var_quota_gb = tk.StringVar(value=str(self.config.get("virtual_storage_gb", 20)))
        spin_quota = ttk.Spinbox(q_row1, from_=1, to=1000, textvariable=self.var_quota_gb, width=6)
        spin_quota.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(q_row1, text="GB").pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(q_row1, text="Update Quota", command=self._update_quota).pack(side=tk.LEFT, padx=(0, 12))

        self.lbl_quota_usage = ttk.Label(q_row1, text="Calculating...", font=("Segoe UI", 8), foreground="#475569")
        self.lbl_quota_usage.pack(side=tk.LEFT)

        q_row2 = ttk.Frame(quota_frame)
        q_row2.pack(fill=tk.X, pady=(6, 2))

        self.progress_quota = ttk.Progressbar(q_row2, orient="horizontal", mode="determinate")
        self.progress_quota.pack(fill=tk.X, expand=True)

        # Virtual Files Catalog
        catalog_frame = ttk.LabelFrame(parent, text="Shared Virtual Files (Distributed across all connected PCs)", padding="10")
        catalog_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 4))

        cat_top = ttk.Frame(catalog_frame)
        cat_top.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(cat_top, text="Files placed into Z: are indexed below and accessible to all peers:", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        ttk.Button(cat_top, text="Refresh", command=self.refresh_virtual_files).pack(side=tk.RIGHT)

        cols = ("name", "size", "holder", "network", "modified")
        self.vfs_tree = ttk.Treeview(catalog_frame, columns=cols, show="headings", height=6, selectmode="browse")
        self.vfs_tree.heading("name", text="Virtual File Path")
        self.vfs_tree.heading("size", text="Size")
        self.vfs_tree.heading("holder", text="Physical Storage Location")
        self.vfs_tree.heading("network", text="Access")
        self.vfs_tree.heading("modified", text="Last Modified")

        self.vfs_tree.column("name", width=220)
        self.vfs_tree.column("size", width=80)
        self.vfs_tree.column("holder", width=160)
        self.vfs_tree.column("network", width=80)
        self.vfs_tree.column("modified", width=120)

        scrollbar = ttk.Scrollbar(catalog_frame, orient=tk.VERTICAL, command=self.vfs_tree.yview)
        self.vfs_tree.configure(yscrollcommand=scrollbar.set)

        self.vfs_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        cat_actions = ttk.Frame(parent)
        cat_actions.pack(fill=tk.X, pady=(4, 0))

        ttk.Button(cat_actions, text="Open in Explorer / Stream", command=self._open_selected_vfs_file).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(cat_actions, text="Collect / Download to Local", style="Accent.TButton", command=self._collect_selected_file).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(cat_actions, text="Open Physical Storage Folder", command=self._open_physical_pool).pack(side=tk.RIGHT)

    def _build_peers_tab(self, parent: ttk.Frame) -> None:
        top_bar = ttk.Frame(parent)
        top_bar.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top_bar, text="Connected LanDrop PCs sharing this Virtual Drive:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)
        ttk.Button(top_bar, text="Rescan", command=self._rescan_peers).pack(side=tk.RIGHT)

        cols = ("device", "network", "ip", "port", "access", "url")
        self.peer_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8, selectmode="browse")
        self.peer_tree.heading("device", text="Device Name")
        self.peer_tree.heading("network", text="Network")
        self.peer_tree.heading("ip", text="IP Address")
        self.peer_tree.heading("port", text="Port")
        self.peer_tree.heading("access", text="Access Mode")
        self.peer_tree.heading("url", text="WebDAV URL")

        self.peer_tree.column("device", width=130)
        self.peer_tree.column("network", width=85)
        self.peer_tree.column("ip", width=105)
        self.peer_tree.column("port", width=55)
        self.peer_tree.column("access", width=85)
        self.peer_tree.column("url", width=200)

        scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=self.peer_tree.yview)
        self.peer_tree.configure(yscrollcommand=scrollbar.set)

        self.peer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        actions = ttk.Frame(parent)
        actions.pack(fill=tk.X, pady=(8, 0))

        ttk.Button(actions, text="Sync Mapping Tables", style="Accent.TButton", command=self._sync_selected_peer).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Open in Explorer", command=self._open_selected_peer).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Copy URL", command=self._copy_selected_peer_url).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Connect by IP / Tailscale...", command=self._manual_connect_dialog).pack(side=tk.RIGHT)

    def _build_share_tab(self, parent: ttk.Frame) -> None:
        opts_frame = ttk.LabelFrame(parent, text="Device & Network Configuration", padding="10")
        opts_frame.pack(fill=tk.X, pady=(0, 10))

        row1 = ttk.Frame(opts_frame)
        row1.pack(fill=tk.X, pady=3)

        ttk.Label(row1, text="Device Name:", width=14).pack(side=tk.LEFT)
        self.var_device = tk.StringVar(value=self.config.get("device_name", ""))
        ttk.Entry(row1, textvariable=self.var_device, width=24).pack(side=tk.LEFT, padx=(0, 16))

        ttk.Label(row1, text="WebDAV Port:", width=13).pack(side=tk.LEFT)
        self.var_port = tk.StringVar(value=str(self.config.get("webdav_port", 8080)))
        ttk.Entry(row1, textvariable=self.var_port, width=8).pack(side=tk.LEFT)

        row2 = ttk.Frame(opts_frame)
        row2.pack(fill=tk.X, pady=4)

        self.var_readonly = tk.BooleanVar(value=self.config.get("read_only", False))
        ttk.Checkbutton(row2, text="Read-only mode (prevent remote upload and file modifications)",
                        variable=self.var_readonly).pack(anchor="w")

        net_frame = ttk.LabelFrame(parent, text="Network Access Endpoints", padding="10")
        net_frame.pack(fill=tk.X, pady=(0, 10))

        # Local LAN URL
        url_row = ttk.Frame(net_frame)
        url_row.pack(fill=tk.X, pady=3)

        ttk.Label(url_row, text="Local LAN URL:", width=14).pack(side=tk.LEFT)
        self.var_url = tk.StringVar(value="")
        entry_url = ttk.Entry(url_row, textvariable=self.var_url, state="readonly")
        entry_url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_url_box = ttk.Frame(net_frame)
        btn_url_box.pack(fill=tk.X, pady=(2, 6))

        ttk.Button(btn_url_box, text="Copy LAN URL", command=self._copy_url).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_url_box, text="Open in Explorer", command=self._open_webdav_url).pack(side=tk.LEFT)

        # Tailscale VPN URL
        ts_row = ttk.Frame(net_frame)
        ts_row.pack(fill=tk.X, pady=(6, 2))

        ttk.Label(ts_row, text="Tailscale URL:", width=14).pack(side=tk.LEFT)
        self.var_ts_url = tk.StringVar(value="")
        entry_ts_url = ttk.Entry(ts_row, textvariable=self.var_ts_url, state="readonly")
        entry_ts_url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_ts_box = ttk.Frame(net_frame)
        btn_ts_box.pack(fill=tk.X, pady=(2, 2))

        self.btn_copy_ts = ttk.Button(btn_ts_box, text="Copy Tailscale URL", command=self._copy_ts_url)
        self.btn_copy_ts.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_ts_status = ttk.Label(btn_ts_box, text="Checking Tailscale...", font=("Segoe UI", 8), foreground="#475569")
        self.lbl_ts_status.pack(side=tk.LEFT, padx=6)

        ctrl_frame = ttk.Frame(parent)
        ctrl_frame.pack(fill=tk.X, pady=(8, 0))

        self.btn_toggle_share = ttk.Button(ctrl_frame, text="Pause Sharing", command=self._toggle_share)
        self.btn_toggle_share.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(ctrl_frame, text="Apply Changes", style="Accent.TButton", command=self._apply_settings).pack(side=tk.LEFT)

    def _build_guide_tab(self, parent: ttk.Frame) -> None:
        guide_box = ttk.LabelFrame(parent, text="How P2P Virtual Drive Works", padding="14")
        guide_box.pack(fill=tk.BOTH, expand=True)

        tips = [
            ("⚡ Unified Z: Drive:",
             "Both PCs running LanDrop have their virtual storage mapped to drive Z:.\n"
             "Opening Z: on PC A and PC B displays the exact same shared virtual directory!"),

            ("📂 Placing Files into Virtual Storage:",
             "Drag and drop any file into your Z: drive. The file is stored in your local storage pool, "
             "and LanDrop instantly updates the peer-to-peer mapping table across all connected computers."),

            ("🎬 Instant Streaming / Collecting:",
             "Other PCs see your files in their Z: drive instantly. They can double click to play/stream videos "
             "directly (via HTTP Range requests) without downloading, or drag files out of Z: to collect them!"),

            ("🌐 Automatic LAN & Tailscale Mesh Sync:",
             "Whether you are on the same local Wi-Fi or connected via Tailscale from different cities, "
             "mapping tables synchronize automatically upon connection."),
        ]

        for title, text in tips:
            ttk.Label(guide_box, text=title, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(4, 1))
            ttk.Label(guide_box, text=text, font=("Segoe UI", 8), justify=tk.LEFT).pack(anchor="w", padx=12, pady=(0, 6))

    # --- Virtual Drive & Storage Actions ---

    def _open_virtual_drive(self) -> None:
        letter = self.var_drive_letter.get().rstrip(":") + ":"
        if is_drive_mounted(letter):
            open_path_or_url(f"{letter}\\")
        else:
            self._mount_local_drive()

    def _mount_local_drive(self) -> None:
        url = f"http://127.0.0.1:{self.server.port}/"
        letter = self.var_drive_letter.get().rstrip(":") + ":"
        res = mount_drive(url, drive_letter=letter)
        if res:
            self.var_drive_letter.set(res)
            self._update_mount_display(True, res)
            self.set_status(f"Mounted virtual drive at {res}")
            open_path_or_url(f"{res}\\")
        else:
            self._update_mount_display(False, letter)
            self.set_status("Failed to mount drive. Ensure WebClient service is running.")

    def _toggle_mount_drive(self) -> None:
        letter = self.var_drive_letter.get().rstrip(":") + ":"
        if is_drive_mounted(letter):
            unmount_drive(letter)
            self.set_status(f"Unmounted {letter}")
        self._mount_local_drive()

    def _update_mount_display(self, mounted: bool, letter: str) -> None:
        if mounted:
            self.lbl_mount_status.configure(text=f"● Mounted at {letter}", foreground="#15803D")
            self.btn_open_drive.configure(text=f"Open {letter} Drive", state="normal")
            self.btn_toggle_mount.configure(text="Remount")
        else:
            self.lbl_mount_status.configure(text="○ Not Mounted", foreground="#475569")
            self.btn_open_drive.configure(state="disabled")
            self.btn_toggle_mount.configure(text="Mount Drive")

    def _update_quota(self) -> None:
        try:
            gb = int(self.var_quota_gb.get().strip())
            if gb <= 0:
                raise ValueError()
        except ValueError:
            messagebox.showerror("Invalid Size", "Please enter a valid size in GB (e.g. 20).")
            return

        self.config["virtual_storage_gb"] = gb
        save_config(self.config)
        self.vfs_provider.set_quota(gb * 1024 * 1024 * 1024)
        self.refresh_virtual_files()
        self.set_status(f"Virtual storage quota set to {gb} GB.")

    def _open_physical_pool(self) -> None:
        path = self.vfs_provider.local_storage_dir
        if os.path.exists(path):
            open_path_or_url(path)

    def refresh_virtual_files(self) -> None:
        """Reload and refresh the virtual storage files catalog table."""
        # Scan local storage for any direct file additions
        self.mapping_table.scan_local_storage(
            storage_dir=self.vfs_provider.local_storage_dir,
            node_id=self.vfs_provider.node_id,
            device_name=self.vfs_provider.device_name,
            my_url=self.vfs_provider.get_my_url(),
        )

        entries = self.mapping_table.get_all_active_entries()
        self._virtual_files = entries

        # Clear and repopulate table
        for item_id in self.vfs_tree.get_children():
            self.vfs_tree.delete(item_id)

        import datetime
        for e in entries:
            mtime_str = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d %H:%M")
            holder_str = f"This PC (Local)" if e.is_local else f"{e.holder_name} (Remote)"
            net_str = "Local Disk" if e.is_local else ("Tailscale" if "100." in e.holder_url else "LAN")
            self.vfs_tree.insert(
                "", tk.END,
                values=(e.path, format_bytes(e.size), holder_str, net_str, mtime_str)
            )

        # Update quota usage bar
        total_quota = self.vfs_provider.quota_bytes
        total_used = self.mapping_table.get_total_used_bytes()
        pct = min(100.0, (total_used / total_quota) * 100.0) if total_quota > 0 else 0
        self.progress_quota["value"] = pct
        self.lbl_quota_usage.configure(
            text=f"Used: {format_bytes(total_used)} / Total Quota: {format_bytes(total_quota)} ({pct:.1f}%)"
        )

        # Check mount status
        letter = self.var_drive_letter.get().rstrip(":") + ":"
        self._update_mount_display(is_drive_mounted(letter), letter)

    def _open_selected_vfs_file(self) -> None:
        sel = self.vfs_tree.selection()
        if not sel:
            messagebox.showinfo("Select File", "Please select a file from the catalog first.")
            return
        item_data = self.vfs_tree.item(sel[0])
        file_path = item_data["values"][0]

        # If drive is mounted, open in Z:
        letter = self.var_drive_letter.get().rstrip(":") + ":"
        if is_drive_mounted(letter):
            full = f"{letter}\\{file_path.lstrip('/')}".replace("/", "\\")
            open_path_or_url(full)
        else:
            url = f"http://127.0.0.1:{self.server.port}{file_path}"
            open_path_or_url(url)

    def _collect_selected_file(self) -> None:
        sel = self.vfs_tree.selection()
        if not sel:
            messagebox.showinfo("Select File", "Please select a file to collect.")
            return
        item_data = self.vfs_tree.item(sel[0])
        rel_path = item_data["values"][0]

        entry = self.mapping_table.get_entry(rel_path)
        if not entry:
            return

        dest_dir = filedialog.askdirectory(title="Select Destination Folder to Collect File")
        if not dest_dir:
            return

        dest_file = os.path.join(dest_dir, os.path.basename(rel_path))

        if entry.is_local:
            # File is local, copy directly
            src = os.path.join(self.vfs_provider.local_storage_dir, rel_path.lstrip("/"))
            if os.path.exists(src):
                shutil.copy2(src, dest_file)
                self.set_status(f"Collected {os.path.basename(rel_path)} to {dest_dir}")
                messagebox.showinfo("Collected", f"File saved to {dest_file}")
            return

        # Remote file: stream from remote peer into destination file
        self.set_status(f"Collecting {os.path.basename(rel_path)} from {entry.holder_name}...")
        holder_url = self.vfs_provider.resolve_holder_url(entry) if hasattr(self.vfs_provider, "resolve_holder_url") else entry.holder_url
        encoded_path = urllib.parse.quote(entry.path)
        raw_url = f"{holder_url.rstrip('/')}/__landrop__/api/raw{encoded_path}"

        def _do_download():
            import urllib.request
            try:
                with urllib.request.urlopen(raw_url, timeout=60) as resp, open(dest_file, "wb") as f_out:
                    shutil.copyfileobj(resp, f_out)
                self.root.after(0, lambda: messagebox.showinfo("Collected", f"Successfully collected to:\n{dest_file}"))
                self.root.after(0, lambda: self.set_status(f"Collected {os.path.basename(rel_path)}"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Download Error", f"Failed to collect file: {e}"))

        import threading
        threading.Thread(target=_do_download, daemon=True).start()

    # --- Peer Tab Actions ---

    def _sync_selected_peer(self) -> None:
        peer = self._get_selected_peer()
        if not peer:
            return
        self.set_status(f"Syncing mapping tables with {peer.device_name}...")
        success = self.sync_engine.sync_with_peer(peer.webdav_url)
        if success:
            self.refresh_virtual_files()
            self.set_status(f"Synchronized with {peer.device_name}!")
        else:
            self.set_status(f"Failed to sync with {peer.device_name}")

    def _copy_ts_url(self) -> None:
        url = self.var_ts_url.get()
        if url and url.startswith("http"):
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.set_status("Copied Tailscale WebDAV URL to clipboard!")

    def _manual_connect_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect to Remote Peer")
        dialog.geometry("400x190")
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(dialog, text="Enter Remote IP or Tailscale Address / Port:", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=16, pady=(16, 6))

        row = ttk.Frame(dialog)
        row.pack(fill=tk.X, padx=16, pady=4)
        ttk.Label(row, text="Address / IP:", width=12).pack(side=tk.LEFT)
        var_addr = tk.StringVar(value="")
        entry = ttk.Entry(row, textvariable=var_addr)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus()

        row2 = ttk.Frame(dialog)
        row2.pack(fill=tk.X, padx=16, pady=4)
        ttk.Label(row2, text="Port:", width=12).pack(side=tk.LEFT)
        var_port = tk.StringVar(value=str(self.server.port))
        ttk.Entry(row2, textvariable=var_port, width=8).pack(side=tk.LEFT)

        btn_row = ttk.Frame(dialog)
        btn_row.pack(fill=tk.X, padx=16, pady=(14, 0))

        def _do_sync():
            addr = var_addr.get().strip()
            port = var_port.get().strip()
            if not addr:
                return
            url = f"http://{addr}:{port}/"
            self.sync_engine.sync_with_peer(url)
            self.refresh_virtual_files()
            dialog.destroy()

        def _do_open():
            addr = var_addr.get().strip()
            port = var_port.get().strip()
            if not addr:
                return
            url = f"http://{addr}:{port}/"
            open_path_or_url(url)
            dialog.destroy()

        ttk.Button(btn_row, text="Sync Mapping", style="Accent.TButton", command=_do_sync).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Open in Explorer", command=_do_open).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)

    def _get_selected_peer(self) -> Optional[PeerInfo]:
        sel = self.peer_tree.selection()
        if not sel:
            messagebox.showinfo("Select Peer", "Please select a peer from the list first.")
            return None
        item_data = self.peer_tree.item(sel[0])
        peer_url = item_data["values"][5]
        for p in self._discovered_peers:
            if p.webdav_url == peer_url:
                return p
        return None

    def _open_selected_peer(self) -> None:
        peer = self._get_selected_peer()
        if peer:
            open_path_or_url(peer.webdav_url)

    def _copy_selected_peer_url(self) -> None:
        peer = self._get_selected_peer()
        if peer:
            self.root.clipboard_clear()
            self.root.clipboard_append(peer.webdav_url)
            self.set_status(f"Copied {peer.device_name} URL to clipboard!")

    def _rescan_peers(self) -> None:
        peers = self.discovery.get_peers()
        self.update_peer_list(peers)
        self.set_status(f"Refreshed peer list. Found {len(peers)} peer(s).")

    def update_peer_list(self, peers: List[PeerInfo]) -> None:
        """Called thread-safely via root.after when discovery sniffs new peers."""
        self._discovered_peers = peers

        cur_selection = self.peer_tree.selection()
        cur_url = None
        if cur_selection:
            cur_url = self.peer_tree.item(cur_selection[0])["values"][5]

        for item_id in self.peer_tree.get_children():
            self.peer_tree.delete(item_id)

        for p in peers:
            access_str = "Read-Only" if p.read_only else "Read-Write"
            net_type = getattr(p, "network_type", "LAN")
            item_id = self.peer_tree.insert(
                "", tk.END,
                values=(p.device_name, net_type, p.ip, p.port, access_str, p.webdav_url)
            )
            if p.webdav_url == cur_url:
                self.peer_tree.selection_set(item_id)

    # --- Share Tab Actions ---

    def _copy_url(self) -> None:
        url = self.var_url.get()
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.set_status("Copied WebDAV URL to clipboard!")

    def _open_webdav_url(self) -> None:
        url = self.var_url.get()
        if url:
            open_path_or_url(url)

    def _toggle_share(self) -> None:
        if self.server.is_running:
            self.server.stop()
            self.discovery.update_info(
                device_name=self.var_device.get().strip(),
                share_name="LanDrop",
                webdav_port=self.server.port,
                read_only=self.var_readonly.get(),
                sharing_active=False,
            )
            self._update_status_display()
            self.set_status("Sharing paused.")
        else:
            self._apply_settings()

    def _apply_settings(self) -> None:
        device = self.var_device.get().strip() or "LanDrop-PC"
        try:
            port = int(self.var_port.get().strip())
        except ValueError:
            messagebox.showerror("Invalid Port", "Port must be a valid number (e.g. 8080).")
            return

        read_only = self.var_readonly.get()

        self.config["device_name"] = device
        self.config["webdav_port"] = port
        self.config["read_only"] = read_only
        save_config(self.config)

        self.server.start(
            shared_dir=self.vfs_provider.local_storage_dir,
            port=port,
            read_only=read_only,
            provider=self.vfs_provider,
            sync_engine=self.sync_engine,
        )
        self.discovery.update_info(
            device_name=device,
            share_name="LanDrop",
            webdav_port=port,
            read_only=read_only,
            sharing_active=True,
        )

        self._update_status_display()
        self.set_status(f"Settings applied. Sharing on port {port}.")

    def _update_status_display(self) -> None:
        ts_info = get_tailscale_info()
        if self.server.is_running:
            url = f"http://{get_local_ip()}:{self.server.port}/"
            self.var_url.set(url)
            self.status_badge.configure(text="● Sharing Active", style="StatusBadgeActive.TLabel")
            self.btn_toggle_share.configure(text="Pause Sharing")

            if ts_info.get("available") and ts_info.get("ip"):
                ts_url = f"http://{ts_info['ip']}:{self.server.port}/"
                self.var_ts_url.set(ts_url)
                dns_text = f" ({ts_info['magic_dns']})" if ts_info.get("magic_dns") else ""
                self.lbl_ts_status.configure(
                    text=f"● Tailscale: {ts_info['ip']}{dns_text}",
                    foreground="#15803D"
                )
                self.btn_copy_ts.configure(state="normal")
            else:
                self.var_ts_url.set("Tailscale not active")
                self.lbl_ts_status.configure(
                    text=f"Tailscale: {ts_info.get('status', 'Not Detected')}",
                    foreground="#64748B"
                )
                self.btn_copy_ts.configure(state="disabled")

            self.set_status(f"Virtual Drive active on {url}")
        else:
            self.var_url.set("")
            self.var_ts_url.set("")
            self.status_badge.configure(text="○ Sharing Paused", style="StatusBadgeInactive.TLabel")
            self.btn_toggle_share.configure(text="Resume Sharing")
            self.lbl_ts_status.configure(text="Sharing paused", foreground="#64748B")
            self.btn_copy_ts.configure(state="disabled")
            self.set_status("Sharing paused.")

    def set_status(self, text: str) -> None:
        self.status_text.configure(text=text)

    def hide_window(self) -> None:
        """Minimize window to system tray."""
        self.root.withdraw()

    def show_window(self) -> None:
        """Restore window from system tray."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.refresh_virtual_files()
