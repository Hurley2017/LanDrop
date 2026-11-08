"""Simple, stylish, and uncluttered desktop UI for LanDrop."""

import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional, Callable
from .config import load_config, save_config
from .utils import get_local_ip, open_path_or_url, get_asset_path
from .tailscale import get_tailscale_info
from .discovery import PeerInfo, DiscoveryService
from .webdav_server import WebDAVServer
from .drive import mount_drive, unmount_drive, is_drive_mounted, optimize_webclient_registry
from .mapping import MappingTable, MappingEntry
from .vfs import VirtualStorageProvider
from .sync import SyncEngine

# Minimalist, elegant palette
BG_APP = "#F9FAFB"         # Clean Gray-50
BG_CARD = "#FFFFFF"        # Pure White
BORDER_CARD = "#E5E7EB"    # Gray-200
COLOR_PRIMARY = "#2563EB"  # Royal Blue
COLOR_TEXT_MAIN = "#111827"# Gray-900
COLOR_TEXT_MUTED = "#6B7280"# Gray-500
COLOR_GREEN = "#10B981"    # Emerald-500
COLOR_GREEN_BG = "#ECFDF5" # Emerald-50
COLOR_GREEN_TEXT = "#065F46"


def format_bytes(num_bytes: int) -> str:
    """Format bytes into readable string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


class LanDropAppUI:
    """Minimalist, stylish application window for LanDrop."""

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

        self.root.title("LanDrop")
        self.root.geometry("620x510")
        self.root.minsize(580, 460)
        self.root.configure(bg=BG_APP)

        icon_file = get_asset_path("icon.ico")
        if os.path.exists(icon_file):
            try:
                self.root.iconbitmap(icon_file)
            except Exception:
                pass

        self._setup_styles()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

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

        try:
            style.configure("TNotebook", background=BG_APP, borderwidth=0)
            style.configure("TNotebook.Tab", font=("Segoe UI", 9, "bold"), padding=[16, 7])
        except Exception:
            pass

        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=[14, 6])
        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))

        try:
            style.configure("Peers.Treeview", font=("Segoe UI", 9), rowheight=26)
            style.configure("Peers.Treeview.Heading", font=("Segoe UI", 9, "bold"))
        except Exception:
            pass

    def _build_ui(self) -> None:
        canvas = tk.Frame(self.root, bg=BG_APP, padx=18, pady=14)
        canvas.pack(fill=tk.BOTH, expand=True)

        # --- Top Header: Minimalist & Clean ---
        header = tk.Frame(canvas, bg=BG_APP)
        header.pack(fill=tk.X, pady=(0, 12))

        # Brand Title
        brand_box = tk.Frame(header, bg=BG_APP)
        brand_box.pack(side=tk.LEFT)

        tk.Label(
            brand_box,
            text="LanDrop",
            font=("Segoe UI", 16, "bold"),
            bg=BG_APP,
            fg=COLOR_TEXT_MAIN,
        ).pack(side=tk.LEFT)

        tk.Label(
            brand_box,
            text="• Virtual Storage",
            font=("Segoe UI", 10),
            bg=BG_APP,
            fg=COLOR_TEXT_MUTED,
        ).pack(side=tk.LEFT, padx=(6, 0), pady=(3, 0))

        # Live Status Badges (Right side)
        badges = tk.Frame(header, bg=BG_APP)
        badges.pack(side=tk.RIGHT)

        self.badge_drive = tk.Label(
            badges,
            text="Z:\\ Ready",
            font=("Segoe UI", 8, "bold"),
            bg="#E0F2FE",
            fg="#0369A1",
            padx=8,
            pady=3,
        )
        self.badge_drive.pack(side=tk.RIGHT, padx=(6, 0))

        self.badge_peers = tk.Label(
            badges,
            text="👥 0 Peers",
            font=("Segoe UI", 8, "bold"),
            bg="#EEF2FF",
            fg="#4338CA",
            padx=8,
            pady=3,
        )
        self.badge_peers.pack(side=tk.RIGHT)

        # --- Notebook Tabs: 3 Clean Tabs ---
        notebook = ttk.Notebook(canvas)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Virtual Drive
        self.tab_drive = tk.Frame(notebook, bg=BG_APP, padx=12, pady=12)
        notebook.add(self.tab_drive, text="  📁 Drive (Z:)  ")
        self._build_drive_tab(self.tab_drive)

        # Tab 2: Connected Peers
        self.tab_peers = tk.Frame(notebook, bg=BG_APP, padx=12, pady=12)
        notebook.add(self.tab_peers, text="  👥 Peers  ")
        self._build_peers_tab(self.tab_peers)

        # Tab 3: Settings
        self.tab_settings = tk.Frame(notebook, bg=BG_APP, padx=12, pady=12)
        notebook.add(self.tab_settings, text="  ⚙️ Settings  ")
        self._build_settings_tab(self.tab_settings)

        # --- Bottom Status Footer ---
        footer = tk.Frame(canvas, bg=BG_APP)
        footer.pack(fill=tk.X, pady=(10, 0))

        self.lbl_footer_status = tk.Label(
            footer,
            text="● LanDrop is active and ready",
            font=("Segoe UI", 8),
            bg=BG_APP,
            fg=COLOR_TEXT_MUTED,
        )
        self.lbl_footer_status.pack(side=tk.LEFT)

        btn_box = tk.Frame(footer, bg=BG_APP)
        btn_box.pack(side=tk.RIGHT)

        ttk.Button(btn_box, text="Minimize", command=self.hide_window).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_box, text="Exit", command=self.on_exit_requested).pack(side=tk.LEFT)

    def _build_drive_tab(self, parent: tk.Widget) -> None:
        """Main Drive tab: Simple, prominent, zero clutter."""
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER_CARD, highlightthickness=1, padx=20, pady=20)
        card.pack(fill=tk.BOTH, expand=True)

        # Hero Drive Icon & Title
        hero_box = tk.Frame(card, bg=BG_CARD)
        hero_box.pack(pady=(6, 14))

        self.lbl_drive_title = tk.Label(
            hero_box,
            text="LanDrop Drive (Z:)",
            font=("Segoe UI", 16, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        )
        self.lbl_drive_title.pack()

        self.lbl_drive_state = tk.Label(
            hero_box,
            text="● Connected to Windows File Explorer",
            font=("Segoe UI", 9),
            bg=BG_CARD,
            fg=COLOR_GREEN_TEXT,
        )
        self.lbl_drive_state.pack(pady=(2, 0))

        # Main Big Hero Action Button
        self.btn_open_hero = ttk.Button(
            card,
            text="📂  Open LanDrop (Z:) in File Explorer",
            style="Primary.TButton",
            command=self._open_virtual_drive,
        )
        self.btn_open_hero.pack(pady=(0, 18), ipadx=10, ipady=3)

        # Minimal Divider
        div = tk.Frame(card, bg=BORDER_CARD, height=1)
        div.pack(fill=tk.X, pady=(0, 14))

        # Storage Usage Bar
        st_box = tk.Frame(card, bg=BG_CARD)
        st_box.pack(fill=tk.X, pady=(0, 12))

        st_top = tk.Frame(st_box, bg=BG_CARD)
        st_top.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            st_top,
            text="Virtual Storage Capacity",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(side=tk.LEFT)

        self.lbl_storage_stats = tk.Label(
            st_top,
            text="0 B used of 20.0 GB",
            font=("Segoe UI", 8),
            bg=BG_CARD,
            fg=COLOR_TEXT_MUTED,
        )
        self.lbl_storage_stats.pack(side=tk.RIGHT)

        self.progress_storage = ttk.Progressbar(st_box, orient="horizontal", mode="determinate")
        self.progress_storage.pack(fill=tk.X)

        # Drive Actions Row (Drive Letter, Remount, Open Physical)
        act_row = tk.Frame(card, bg=BG_CARD)
        act_row.pack(fill=tk.X, pady=(10, 0))

        tk.Label(act_row, text="Drive Letter:", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        self.var_drive_letter = tk.StringVar(value=self.config.get("mount_drive_letter", "Z:"))
        combo_drive = ttk.Combobox(
            act_row,
            textvariable=self.var_drive_letter,
            values=["Z:", "Y:", "X:", "W:", "V:"],
            width=4,
            state="readonly",
        )
        combo_drive.pack(side=tk.LEFT, padx=(0, 8))

        self.btn_remount = ttk.Button(act_row, text="Remount", command=self._toggle_mount_drive)
        self.btn_remount.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Button(act_row, text="Open Storage Folder", command=self._open_physical_pool).pack(side=tk.RIGHT)

    def _build_peers_tab(self, parent: tk.Widget) -> None:
        """Connected peers tab: Clean, uncluttered list."""
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER_CARD, highlightthickness=1, padx=14, pady=14)
        card.pack(fill=tk.BOTH, expand=True)

        top_row = tk.Frame(card, bg=BG_CARD)
        top_row.pack(fill=tk.X, pady=(0, 8))

        self.lbl_peers_count = tk.Label(
            top_row,
            text="Connected Computers (0 Online)",
            font=("Segoe UI", 10, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        )
        self.lbl_peers_count.pack(side=tk.LEFT)

        ttk.Button(top_row, text="🔄  Rescan", command=self._rescan_peers).pack(side=tk.RIGHT)

        cols = ("device", "network", "ip", "port", "access", "url")
        self.peer_tree = ttk.Treeview(
            card,
            columns=cols,
            show="headings",
            height=8,
            selectmode="browse",
            style="Peers.Treeview",
        )
        self.peer_tree.heading("device", text="Device")
        self.peer_tree.heading("network", text="Network")
        self.peer_tree.heading("ip", text="IP Address")
        self.peer_tree.heading("port", text="Port")
        self.peer_tree.heading("access", text="Mode")
        self.peer_tree.heading("url", text="WebDAV URL")

        self.peer_tree.column("device", width=130)
        self.peer_tree.column("network", width=90)
        self.peer_tree.column("ip", width=110)
        self.peer_tree.column("port", width=55)
        self.peer_tree.column("access", width=80)
        self.peer_tree.column("url", width=180)

        scrollbar = ttk.Scrollbar(card, orient=tk.VERTICAL, command=self.peer_tree.yview)
        self.peer_tree.configure(yscrollcommand=scrollbar.set)

        self.peer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.peer_tree.bind("<Double-1>", lambda e: self._open_selected_peer())

        # Action Strip
        act_row = tk.Frame(parent, bg=BG_APP)
        act_row.pack(fill=tk.X, pady=(10, 0))

        ttk.Button(act_row, text="⚡  Sync Mapping", style="Accent.TButton", command=self._sync_selected_peer).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(act_row, text="📂  Open in Explorer", command=self._open_selected_peer).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(act_row, text="📋  Copy URL", command=self._copy_selected_peer_url).pack(side=tk.LEFT)
        ttk.Button(act_row, text="➕  Connect by IP...", command=self._manual_connect_dialog).pack(side=tk.RIGHT)

    def _build_settings_tab(self, parent: tk.Widget) -> None:
        """Settings tab: Clean, essential options only."""
        card = tk.Frame(parent, bg=BG_CARD, highlightbackground=BORDER_CARD, highlightthickness=1, padx=16, pady=16)
        card.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            card,
            text="Preferences & Network Endpoints",
            font=("Segoe UI", 10, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(anchor="w", pady=(0, 12))

        # Device Name Row
        r1 = tk.Frame(card, bg=BG_CARD)
        r1.pack(fill=tk.X, pady=4)
        tk.Label(r1, text="Device Name:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_device = tk.StringVar(value=self.config.get("device_name", ""))
        ttk.Entry(r1, textvariable=self.var_device, width=22).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(r1, text="Quota:", width=6, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_quota_gb = tk.StringVar(value=str(self.config.get("virtual_storage_gb", 20)))
        ttk.Entry(r1, textvariable=self.var_quota_gb, width=5).pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(r1, text="GB", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED).pack(side=tk.LEFT)

        # Port Row
        r2 = tk.Frame(card, bg=BG_CARD)
        r2.pack(fill=tk.X, pady=4)
        tk.Label(r2, text="WebDAV Port:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_port = tk.StringVar(value=str(self.config.get("webdav_port", 8080)))
        ttk.Entry(r2, textvariable=self.var_port, width=8).pack(side=tk.LEFT, padx=(0, 16))

        self.var_readonly = tk.BooleanVar(value=self.config.get("read_only", False))
        ttk.Checkbutton(r2, text="Read-only mode", variable=self.var_readonly).pack(side=tk.LEFT)

        # Divider
        div = tk.Frame(card, bg=BORDER_CARD, height=1)
        div.pack(fill=tk.X, pady=12)

        # LAN URL
        r3 = tk.Frame(card, bg=BG_CARD)
        r3.pack(fill=tk.X, pady=4)
        tk.Label(r3, text="Local LAN URL:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_url = tk.StringVar(value="")
        ttk.Entry(r3, textvariable=self.var_url, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(r3, text="Copy", command=self._copy_url).pack(side=tk.LEFT)

        # Tailscale URL
        r4 = tk.Frame(card, bg=BG_CARD)
        r4.pack(fill=tk.X, pady=4)
        tk.Label(r4, text="Tailscale URL:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_ts_url = tk.StringVar(value="")
        ttk.Entry(r4, textvariable=self.var_ts_url, state="readonly").pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.btn_copy_ts = ttk.Button(r4, text="Copy", command=self._copy_ts_url)
        self.btn_copy_ts.pack(side=tk.LEFT)

        # Buttons Row
        b_row = tk.Frame(card, bg=BG_CARD)
        b_row.pack(fill=tk.X, pady=(16, 0))

        self.btn_toggle_share = ttk.Button(b_row, text="Pause Sharing", command=self._toggle_share)
        self.btn_toggle_share.pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(b_row, text="Save Settings", style="Accent.TButton", command=self._apply_settings).pack(side=tk.LEFT)

        ttk.Button(b_row, text="Fix Windows 47MB Limit", command=self._apply_webclient_fix).pack(side=tk.RIGHT)

    # --- Actions ---

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
            self.lbl_drive_title.configure(text=f"LanDrop Drive ({letter})")
            self.lbl_drive_state.configure(text="● Mounted & Synced in Windows File Explorer", fg=COLOR_GREEN_TEXT)
            self.badge_drive.configure(text=f"{letter}\\ Ready", bg="#E0F2FE", fg="#0369A1")
            self.btn_open_hero.configure(text=f"📂  Open LanDrop ({letter}) in File Explorer", state="normal")
            self.btn_remount.configure(text="Remount")
        else:
            self.lbl_drive_title.configure(text=f"LanDrop Drive ({letter}) [Offline]")
            self.lbl_drive_state.configure(text="○ Drive is not mounted", fg=COLOR_TEXT_MUTED)
            self.badge_drive.configure(text=f"{letter}\\ Offline", bg="#F1F5F9", fg=COLOR_TEXT_MUTED)
            self.btn_open_hero.configure(text="Mount Drive", state="normal")
            self.btn_remount.configure(text="Mount")

    def _apply_webclient_fix(self) -> None:
        ok = optimize_webclient_registry()
        if ok:
            messagebox.showinfo("Optimization Applied", "Windows WebClient registry settings successfully optimized for 4GB transfers.")
            self.set_status("Windows WebClient optimized.")
        else:
            messagebox.showinfo(
                "Windows Optimization",
                "WebClient optimization attempted.\nIf copying files larger than 47MB is restricted by Windows, run LanDrop once as Administrator to persist the 4GB registry key."
            )

    def _open_physical_pool(self) -> None:
        path = self.vfs_provider.local_storage_dir
        if os.path.exists(path):
            open_path_or_url(path)

    def refresh_virtual_files(self) -> None:
        """Reload and refresh virtual storage capacity & status."""
        self.mapping_table.scan_local_storage(
            storage_dir=self.vfs_provider.local_storage_dir,
            node_id=self.vfs_provider.node_id,
            device_name=self.vfs_provider.device_name,
            my_url=self.vfs_provider.get_my_url(),
        )

        total_quota = self.vfs_provider.quota_bytes
        total_used = self.mapping_table.get_total_used_bytes()
        pct = min(100.0, (total_used / total_quota) * 100.0) if total_quota > 0 else 0

        if hasattr(self, "progress_storage"):
            self.progress_storage["value"] = pct
        if hasattr(self, "lbl_storage_stats"):
            self.lbl_storage_stats.configure(
                text=f"{format_bytes(total_used)} used of {format_bytes(total_quota)} ({pct:.1f}%)"
            )

        if hasattr(self, "var_drive_letter"):
            letter = self.var_drive_letter.get().rstrip(":") + ":"
            self._update_mount_display(is_drive_mounted(letter), letter)

    # --- Peer Tab Actions ---

    def _sync_selected_peer(self) -> None:
        peer = self._get_selected_peer()
        if not peer:
            return
        self.set_status(f"Syncing with {peer.device_name}...")
        success = self.sync_engine.sync_with_peer(peer.webdav_url)
        if success:
            self.refresh_virtual_files()
            self.set_status(f"Synchronized with {peer.device_name}!")
            messagebox.showinfo("Synchronized", f"Successfully synchronized with {peer.device_name}!")
        else:
            self.set_status(f"Failed to sync with {peer.device_name}")
            messagebox.showerror("Sync Failed", f"Could not sync with {peer.device_name}.")

    def _copy_ts_url(self) -> None:
        url = self.var_ts_url.get()
        if url and url.startswith("http"):
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.set_status("Copied Tailscale URL!")

    def _manual_connect_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect to Peer")
        dialog.geometry("380x170")
        dialog.configure(bg=BG_APP)
        dialog.transient(self.root)
        dialog.grab_set()

        card = tk.Frame(dialog, bg=BG_CARD, highlightbackground=BORDER_CARD, highlightthickness=1, padx=14, pady=14)
        card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        tk.Label(card, text="Enter Remote IP or Tailscale Address:", font=("Segoe UI", 9, "bold"), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(anchor="w", pady=(0, 6))

        row = tk.Frame(card, bg=BG_CARD)
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text="IP / Address:", width=11, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        var_addr = tk.StringVar(value="")
        entry = ttk.Entry(row, textvariable=var_addr)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus()

        row2 = tk.Frame(card, bg=BG_CARD)
        row2.pack(fill=tk.X, pady=3)
        tk.Label(row2, text="Port:", width=11, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        var_port = tk.StringVar(value=str(self.server.port))
        ttk.Entry(row2, textvariable=var_port, width=8).pack(side=tk.LEFT)

        btn_row = tk.Frame(card, bg=BG_CARD)
        btn_row.pack(fill=tk.X, pady=(10, 0))

        def _do_sync():
            addr = var_addr.get().strip()
            port = var_port.get().strip()
            if not addr:
                return
            url = f"http://{addr}:{port}/"
            success = self.sync_engine.sync_with_peer(url)
            self.refresh_virtual_files()
            dialog.destroy()
            if success:
                messagebox.showinfo("Connected", f"Connected to {url}")
            else:
                messagebox.showerror("Error", f"Failed to reach {url}")

        ttk.Button(btn_row, text="Connect", style="Accent.TButton", command=_do_sync).pack(side=tk.LEFT, padx=(0, 6))
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
            self.set_status(f"Copied {peer.device_name} URL!")

    def _rescan_peers(self) -> None:
        peers = self.discovery.get_peers()
        self.update_peer_list(peers)
        self.set_status(f"Found {len(peers)} peer(s).")

    def update_peer_list(self, peers: List[PeerInfo]) -> None:
        """Called thread-safely via root.after when discovery sniffs peers."""
        self._discovered_peers = peers

        if hasattr(self, "badge_peers"):
            self.badge_peers.configure(text=f"👥 {len(peers)} Peer{'s' if len(peers) != 1 else ''}")
        if hasattr(self, "lbl_peers_count"):
            self.lbl_peers_count.configure(text=f"Connected Computers ({len(peers)} Online)")

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
            self.set_status("Copied WebDAV URL!")

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
            messagebox.showerror("Invalid Port", "Port must be a number (e.g. 8080).")
            return

        try:
            gb = int(self.var_quota_gb.get().strip())
            if gb <= 0:
                raise ValueError()
            self.config["virtual_storage_gb"] = gb
            self.vfs_provider.set_quota(gb * 1024 * 1024 * 1024)
        except ValueError:
            messagebox.showerror("Invalid Quota", "Quota must be a positive number in GB.")
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
        self.refresh_virtual_files()
        self.set_status(f"Settings saved. Sharing active on port {port}.")

    def _update_status_display(self) -> None:
        ts_info = get_tailscale_info()
        if self.server.is_running:
            url = f"http://{get_local_ip()}:{self.server.port}/"
            self.var_url.set(url)
            self.btn_toggle_share.configure(text="Pause Sharing")

            if ts_info.get("available") and ts_info.get("ip"):
                ts_url = f"http://{ts_info['ip']}:{self.server.port}/"
                self.var_ts_url.set(ts_url)
                self.btn_copy_ts.configure(state="normal")
            else:
                self.var_ts_url.set("Tailscale not connected")
                self.btn_copy_ts.configure(state="disabled")

            self.set_status(f"Virtual Drive active on {url}")
        else:
            self.var_url.set("")
            self.var_ts_url.set("")
            self.btn_toggle_share.configure(text="Resume Sharing")
            self.btn_copy_ts.configure(state="disabled")
            self.set_status("Sharing paused.")

    def set_status(self, text: str) -> None:
        self.lbl_footer_status.configure(text=f"● {text}")

    def hide_window(self) -> None:
        """Minimize window to system tray."""
        self.root.withdraw()

    def show_window(self) -> None:
        """Restore window from system tray."""
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.refresh_virtual_files()
