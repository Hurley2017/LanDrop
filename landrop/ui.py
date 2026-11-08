"""Native Tkinter setup UI and peer manager for LanDrop."""

import os
import sys
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import List, Optional, Callable
from .config import load_config, save_config
from .utils import get_local_ip, open_path_or_url, get_asset_path
from .tailscale import get_tailscale_info
from .discovery import PeerInfo, DiscoveryService
from .webdav_server import WebDAVServer
from .drive import mount_drive, unmount_drive, is_drive_mounted, get_available_drive_letter, optimize_webclient_registry
from .mapping import MappingTable, MappingEntry
from .vfs import VirtualStorageProvider
from .sync import SyncEngine

# Modern Color Palette
BG_WINDOW = "#F8FAFC"       # Slate-50 window canvas
BG_CARD = "#FFFFFF"         # Pure white cards
BORDER_CARD = "#E2E8F0"     # Crisp subtle border (Slate-200)

COLOR_PRIMARY = "#4F46E5"   # Indigo-600
COLOR_PRIMARY_HOVER = "#4338CA"
COLOR_PRIMARY_BG = "#EEF2FF"
COLOR_PRIMARY_TEXT = "#312E81"

COLOR_SUCCESS = "#10B981"   # Emerald-500
COLOR_SUCCESS_BG = "#DCFCE7"# Emerald-100
COLOR_SUCCESS_TEXT = "#166534"

COLOR_PURPLE = "#8B5CF6"    # Purple-500 (Tailscale)
COLOR_PURPLE_BG = "#EDE9FE" # Purple-100
COLOR_PURPLE_TEXT = "#5B21B6"

COLOR_TEXT_MAIN = "#0F172A" # Slate-900
COLOR_TEXT_MUTED = "#64748B"# Slate-500
COLOR_TEXT_LIGHT = "#94A3B8"# Slate-400


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
        self.root.geometry("780x680")
        self.root.minsize(720, 580)
        self.root.configure(bg=BG_WINDOW)

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

        # Configure notebook tabs
        try:
            style.configure("TNotebook", background=BG_WINDOW, borderwidth=0)
            style.configure("TNotebook.Tab", font=("Segoe UI", 9, "bold"), padding=[14, 8])
        except Exception:
            pass

        style.configure("Accent.TButton", font=("Segoe UI", 9, "bold"))
        style.configure("Hero.TButton", font=("Segoe UI", 10, "bold"), padding=[14, 6])
        style.configure("Card.TLabel", background=BG_CARD, foreground=COLOR_TEXT_MAIN, font=("Segoe UI", 9))
        style.configure("CardMuted.TLabel", background=BG_CARD, foreground=COLOR_TEXT_MUTED, font=("Segoe UI", 8))
        style.configure("CardTitle.TLabel", background=BG_CARD, foreground=COLOR_TEXT_MAIN, font=("Segoe UI", 10, "bold"))

        # Treeview styling
        try:
            style.configure(
                "Custom.Treeview",
                font=("Segoe UI", 9),
                rowheight=26,
            )
            style.configure(
                "Custom.Treeview.Heading",
                font=("Segoe UI", 9, "bold"),
            )
        except Exception:
            pass

    def _create_card(self, parent: tk.Widget, padding: int = 12, pady=(0, 10), fill=tk.X) -> tk.Frame:
        """Create a styled modern white card container."""
        card = tk.Frame(
            parent,
            bg=BG_CARD,
            highlightbackground=BORDER_CARD,
            highlightthickness=1,
            padx=padding,
            pady=padding,
        )
        card.pack(fill=fill, pady=pady)
        return card

    def _build_ui(self) -> None:
        main_frame = tk.Frame(self.root, bg=BG_WINDOW, padx=16, pady=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Top Header Section ---
        header_frame = tk.Frame(main_frame, bg=BG_WINDOW)
        header_frame.pack(fill=tk.X, pady=(0, 12))

        # Brand Title & Logo Badge
        brand_box = tk.Frame(header_frame, bg=BG_WINDOW)
        brand_box.pack(side=tk.LEFT)

        logo_row = tk.Frame(brand_box, bg=BG_WINDOW)
        logo_row.pack(anchor="w")

        logo_pill = tk.Label(
            logo_row,
            text=" 📁 LanDrop ",
            font=("Segoe UI", 14, "bold"),
            bg=COLOR_PRIMARY_BG,
            fg=COLOR_PRIMARY_TEXT,
            padx=6,
            pady=2,
            relief="flat",
        )
        logo_pill.pack(side=tk.LEFT, padx=(0, 8))

        lbl_title = tk.Label(
            logo_row,
            text="Virtual Storage Drive",
            font=("Segoe UI", 16, "bold"),
            bg=BG_WINDOW,
            fg=COLOR_TEXT_MAIN,
        )
        lbl_title.pack(side=tk.LEFT)

        lbl_subtitle = tk.Label(
            brand_box,
            text="High-Speed Distributed Virtual Storage & Peer-to-Peer Mesh Share",
            font=("Segoe UI", 9),
            bg=BG_WINDOW,
            fg=COLOR_TEXT_MUTED,
        )
        lbl_subtitle.pack(anchor="w", pady=(2, 0))

        # Header Right Status Chips
        chips_box = tk.Frame(header_frame, bg=BG_WINDOW)
        chips_box.pack(side=tk.RIGHT, pady=4)

        self.chip_status = tk.Label(
            chips_box,
            text="● Online & Mesh Active",
            font=("Segoe UI", 8, "bold"),
            bg=COLOR_SUCCESS_BG,
            fg=COLOR_SUCCESS_TEXT,
            padx=8,
            pady=4,
        )
        self.chip_status.pack(side=tk.RIGHT, padx=(6, 0))

        self.chip_peers = tk.Label(
            chips_box,
            text="👥 0 Peers",
            font=("Segoe UI", 8, "bold"),
            bg=COLOR_PRIMARY_BG,
            fg=COLOR_PRIMARY_TEXT,
            padx=8,
            pady=4,
        )
        self.chip_peers.pack(side=tk.RIGHT, padx=(6, 0))

        self.chip_drive = tk.Label(
            chips_box,
            text="Z:\\ Mounted",
            font=("Segoe UI", 8, "bold"),
            bg="#E0F2FE",
            fg="#0369A1",
            padx=8,
            pady=4,
        )
        self.chip_drive.pack(side=tk.RIGHT)

        # --- Tabs (Notebook) ---
        notebook = ttk.Notebook(main_frame)
        notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Virtual Drive (Z:)
        self.tab_vfs = tk.Frame(notebook, bg=BG_WINDOW, padx=12, pady=12)
        notebook.add(self.tab_vfs, text="   📁 Virtual Drive (Z:)   ")
        self._build_vfs_tab(self.tab_vfs)

        # Tab 2: Discovered Peers
        self.tab_peers = tk.Frame(notebook, bg=BG_WINDOW, padx=12, pady=12)
        notebook.add(self.tab_peers, text="   👥 Connected Peers   ")
        self._build_peers_tab(self.tab_peers)

        # Tab 3: Network & Share Settings
        self.tab_share = tk.Frame(notebook, bg=BG_WINDOW, padx=12, pady=12)
        notebook.add(self.tab_share, text="   ⚙️ Network Settings   ")
        self._build_share_tab(self.tab_share)

        # Tab 4: How It Works Guide
        self.tab_guide = tk.Frame(notebook, bg=BG_WINDOW, padx=12, pady=12)
        notebook.add(self.tab_guide, text="   💡 How It Works   ")
        self._build_guide_tab(self.tab_guide)

        # --- Footer / Status bar ---
        footer_frame = tk.Frame(main_frame, bg=BG_WINDOW)
        footer_frame.pack(fill=tk.X, pady=(10, 0))

        self.status_dot = tk.Label(footer_frame, text="●", font=("Segoe UI", 8), bg=BG_WINDOW, fg=COLOR_SUCCESS)
        self.status_dot.pack(side=tk.LEFT, padx=(0, 4))

        self.status_text = tk.Label(footer_frame, text="Ready", font=("Segoe UI", 8), bg=BG_WINDOW, fg=COLOR_TEXT_MUTED)
        self.status_text.pack(side=tk.LEFT)

        btn_box = tk.Frame(footer_frame, bg=BG_WINDOW)
        btn_box.pack(side=tk.RIGHT)

        ttk.Button(btn_box, text="Minimize to Tray", command=self.hide_window).pack(side=tk.LEFT, padx=4)
        ttk.Button(btn_box, text="Exit LanDrop", command=self.on_exit_requested).pack(side=tk.LEFT)

    def _build_vfs_tab(self, parent: tk.Widget) -> None:
        # --- Card 1: Drive Hero Banner ---
        hero_card = self._create_card(parent, padding=14, pady=(0, 10))

        top_row = tk.Frame(hero_card, bg=BG_CARD)
        top_row.pack(fill=tk.X)

        title_col = tk.Frame(top_row, bg=BG_CARD)
        title_col.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.lbl_hero_drive = tk.Label(
            title_col,
            text="LanDrop Drive (Z:)",
            font=("Segoe UI", 13, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        )
        self.lbl_hero_drive.pack(anchor="w")

        lbl_hero_sub = tk.Label(
            title_col,
            text="Mounted directly in Windows File Explorer. Accessible by all LanDrop peers.",
            font=("Segoe UI", 8),
            bg=BG_CARD,
            fg=COLOR_TEXT_MUTED,
        )
        lbl_hero_sub.pack(anchor="w", pady=(1, 0))

        # Hero Action Button (Large)
        self.btn_open_drive = ttk.Button(
            top_row,
            text="📂  Open LanDrop in Explorer",
            style="Hero.TButton",
            command=self._open_virtual_drive,
        )
        self.btn_open_drive.pack(side=tk.RIGHT, padx=(8, 0))

        # Mount Control Strip
        ctrl_strip = tk.Frame(hero_card, bg="#F8FAFC", padx=10, pady=8, highlightbackground="#F1F5F9", highlightthickness=1)
        ctrl_strip.pack(fill=tk.X, pady=(10, 0))

        tk.Label(ctrl_strip, text="Drive Letter:", font=("Segoe UI", 8, "bold"), bg="#F8FAFC", fg=COLOR_TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 6))

        self.var_drive_letter = tk.StringVar(value=self.config.get("mount_drive_letter", "Z:"))
        self.combo_drive = ttk.Combobox(
            ctrl_strip,
            textvariable=self.var_drive_letter,
            values=["Z:", "Y:", "X:", "W:", "V:"],
            width=5,
            state="readonly",
        )
        self.combo_drive.pack(side=tk.LEFT, padx=(0, 12))

        self.lbl_mount_status = tk.Label(
            ctrl_strip,
            text="● Checking mount...",
            font=("Segoe UI", 8, "bold"),
            bg="#F8FAFC",
            fg=COLOR_SUCCESS_TEXT,
        )
        self.lbl_mount_status.pack(side=tk.LEFT, padx=(0, 16))

        self.btn_toggle_mount = ttk.Button(ctrl_strip, text="Remount", command=self._toggle_mount_drive)
        self.btn_toggle_mount.pack(side=tk.LEFT, padx=(0, 12))

        self.btn_fix_limit = ttk.Button(ctrl_strip, text="⚡ Fix Windows 47MB Limit", command=self._apply_webclient_fix)
        self.btn_fix_limit.pack(side=tk.RIGHT)

        # --- Card 2: Storage Quota & Capacity Dashboard ---
        quota_card = self._create_card(parent, padding=14, pady=(0, 10))

        q_head = tk.Frame(quota_card, bg=BG_CARD)
        q_head.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            q_head,
            text="Virtual Storage Capacity & Quota",
            font=("Segoe UI", 10, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(side=tk.LEFT)

        self.lbl_pct_badge = tk.Label(
            q_head,
            text="0.0% Used",
            font=("Segoe UI", 8, "bold"),
            bg=COLOR_PRIMARY_BG,
            fg=COLOR_PRIMARY_TEXT,
            padx=6,
            pady=2,
        )
        self.lbl_pct_badge.pack(side=tk.RIGHT)

        # Progress bar
        self.progress_quota = ttk.Progressbar(quota_card, orient="horizontal", mode="determinate")
        self.progress_quota.pack(fill=tk.X, pady=(2, 6))

        # Stats Row
        stats_row = tk.Frame(quota_card, bg=BG_CARD)
        stats_row.pack(fill=tk.X, pady=(0, 8))

        self.lbl_quota_usage = tk.Label(
            stats_row,
            text="Used: 0 B  •  Available: 20.0 GB  •  Allocated Quota: 20.0 GB",
            font=("Segoe UI", 8),
            bg=BG_CARD,
            fg=COLOR_TEXT_MUTED,
        )
        self.lbl_quota_usage.pack(side=tk.LEFT)

        # Quota Configuration Controls & Presets
        q_ctrls = tk.Frame(quota_card, bg=BG_CARD)
        q_ctrls.pack(fill=tk.X, pady=(4, 0))

        tk.Label(q_ctrls, text="Set Quota:", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT, padx=(0, 6))

        self.var_quota_gb = tk.StringVar(value=str(self.config.get("virtual_storage_gb", 20)))
        spin_quota = ttk.Spinbox(q_ctrls, from_=1, to=1000, textvariable=self.var_quota_gb, width=5)
        spin_quota.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(q_ctrls, text="GB", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(q_ctrls, text="Update", command=self._update_quota).pack(side=tk.LEFT, padx=(0, 16))

        tk.Label(q_ctrls, text="Presets:", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED).pack(side=tk.LEFT, padx=(0, 4))
        for gb_val in [10, 20, 50, 100]:
            btn_pre = ttk.Button(
                q_ctrls,
                text=f"{gb_val}GB",
                width=5,
                command=lambda val=gb_val: self._set_quota_preset(val),
            )
            btn_pre.pack(side=tk.LEFT, padx=2)

        # --- Card 3: Local Physical Storage Pool ---
        pool_card = self._create_card(parent, padding=14, pady=(0, 10))

        p_head = tk.Frame(pool_card, bg=BG_CARD)
        p_head.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            p_head,
            text="Local Physical Storage Pool",
            font=("Segoe UI", 10, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(side=tk.LEFT)

        self.lbl_pool_stats = tk.Label(
            p_head,
            text="Physical disk backend",
            font=("Segoe UI", 8),
            bg=BG_CARD,
            fg=COLOR_TEXT_MUTED,
        )
        self.lbl_pool_stats.pack(side=tk.RIGHT)

        p_row = tk.Frame(pool_card, bg=BG_CARD)
        p_row.pack(fill=tk.X, pady=2)

        self.lbl_pool_path = tk.Label(
            p_row,
            text=self.vfs_provider.local_storage_dir,
            font=("Consolas", 8),
            bg="#F1F5F9",
            fg=COLOR_TEXT_MAIN,
            padx=8,
            pady=4,
            anchor="w",
        )
        self.lbl_pool_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        ttk.Button(p_row, text="Open Folder", command=self._open_physical_pool).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(p_row, text="Change...", command=self._change_physical_pool).pack(side=tk.LEFT)

        # --- Card 4: Seamless Drive Experience Guide ---
        guide_card = self._create_card(parent, padding=14, pady=(0, 0), fill=tk.BOTH)

        tk.Label(
            guide_card,
            text="✨ How to Use Your LanDrop Drive",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(anchor="w", pady=(0, 6))

        bullets = [
            ("📁 Native Windows Experience", "Your LanDrop drive behaves like an internal SSD. Browse, organize, and rename files freely."),
            ("🚀 Drag & Drop to Share", "Drop any file or folder directly into Z: in File Explorer. It instantly appears across all peers."),
            ("🎬 Instant Streaming", "Double-click videos, audio, or PDFs hosted on other PCs to stream immediately without waiting to download."),
            ("🌐 Zero Configuration Mesh", "Peers automatically sniff and connect over local Wi-Fi or secure Tailscale mesh."),
        ]

        for b_title, b_desc in bullets:
            b_row = tk.Frame(guide_card, bg=BG_CARD)
            b_row.pack(anchor="w", pady=2)
            tk.Label(b_row, text=f"• {b_title}:", font=("Segoe UI", 8, "bold"), bg=BG_CARD, fg=COLOR_PRIMARY).pack(side=tk.LEFT)
            tk.Label(b_row, text=f" {b_desc}", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED).pack(side=tk.LEFT)

    def _build_peers_tab(self, parent: tk.Widget) -> None:
        # --- Card 1: Network Mesh Overview ---
        mesh_card = self._create_card(parent, padding=12, pady=(0, 10))

        m_row = tk.Frame(mesh_card, bg=BG_CARD)
        m_row.pack(fill=tk.X)

        self.lbl_mesh_info = tk.Label(
            m_row,
            text=f"🖥️ This Device: {self.config.get('device_name', 'LanDrop-PC')}  •  Node ID: {self.config.get('node_id', 'unknown')}",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        )
        self.lbl_mesh_info.pack(side=tk.LEFT)

        ttk.Button(m_row, text="🔄  Rescan Peers", command=self._rescan_peers).pack(side=tk.RIGHT)

        # --- Card 2: Discovered Peers Table ---
        table_card = self._create_card(parent, padding=12, pady=(0, 10), fill=tk.BOTH)

        t_head = tk.Frame(table_card, bg=BG_CARD)
        t_head.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            t_head,
            text="Connected Mesh Computers Sharing This Virtual Storage:",
            font=("Segoe UI", 9, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(side=tk.LEFT)

        cols = ("device", "network", "ip", "port", "access", "url")
        self.peer_tree = ttk.Treeview(
            table_card,
            columns=cols,
            show="headings",
            height=10,
            selectmode="browse",
            style="Custom.Treeview",
        )
        self.peer_tree.heading("device", text="Device Name")
        self.peer_tree.heading("network", text="Network")
        self.peer_tree.heading("ip", text="IP Address")
        self.peer_tree.heading("port", text="Port")
        self.peer_tree.heading("access", text="Access Mode")
        self.peer_tree.heading("url", text="WebDAV URL")

        self.peer_tree.column("device", width=140)
        self.peer_tree.column("network", width=95)
        self.peer_tree.column("ip", width=115)
        self.peer_tree.column("port", width=60)
        self.peer_tree.column("access", width=85)
        self.peer_tree.column("url", width=220)

        scrollbar = ttk.Scrollbar(table_card, orient=tk.VERTICAL, command=self.peer_tree.yview)
        self.peer_tree.configure(yscrollcommand=scrollbar.set)

        self.peer_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.peer_tree.bind("<Double-1>", lambda event: self._open_selected_peer())

        # --- Card 3: Peer Action Toolbar ---
        act_card = self._create_card(parent, padding=10, pady=(0, 0))

        ttk.Button(act_card, text="⚡  Sync Mapping Tables", style="Accent.TButton", command=self._sync_selected_peer).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(act_card, text="📂  Open Remote in Explorer", command=self._open_selected_peer).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(act_card, text="📋  Copy WebDAV URL", command=self._copy_selected_peer_url).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(act_card, text="➕  Connect by IP / Tailscale...", command=self._manual_connect_dialog).pack(side=tk.RIGHT)

    def _build_share_tab(self, parent: tk.Widget) -> None:
        # --- Card 1: Device Configuration ---
        dev_card = self._create_card(parent, padding=14, pady=(0, 10))

        tk.Label(
            dev_card,
            text="Device Identity & Port Configuration",
            font=("Segoe UI", 10, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(anchor="w", pady=(0, 8))

        row1 = tk.Frame(dev_card, bg=BG_CARD)
        row1.pack(fill=tk.X, pady=3)

        tk.Label(row1, text="Device Name:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_device = tk.StringVar(value=self.config.get("device_name", ""))
        ttk.Entry(row1, textvariable=self.var_device, width=26).pack(side=tk.LEFT, padx=(0, 20))

        tk.Label(row1, text="WebDAV Port:", width=12, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_port = tk.StringVar(value=str(self.config.get("webdav_port", 8080)))
        ttk.Entry(row1, textvariable=self.var_port, width=8).pack(side=tk.LEFT)

        row2 = tk.Frame(dev_card, bg=BG_CARD)
        row2.pack(fill=tk.X, pady=(8, 2))

        self.var_readonly = tk.BooleanVar(value=self.config.get("read_only", False))
        ttk.Checkbutton(
            row2,
            text="Read-Only Mode (prevent peers from modifying files stored on this machine)",
            variable=self.var_readonly,
        ).pack(anchor="w")

        # --- Card 2: Network Access Endpoints ---
        net_card = self._create_card(parent, padding=14, pady=(0, 10))

        tk.Label(
            net_card,
            text="Live Network Endpoints",
            font=("Segoe UI", 10, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(anchor="w", pady=(0, 8))

        # Local LAN URL
        url_row = tk.Frame(net_card, bg=BG_CARD)
        url_row.pack(fill=tk.X, pady=3)

        tk.Label(url_row, text="Local LAN URL:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_url = tk.StringVar(value="")
        entry_url = ttk.Entry(url_row, textvariable=self.var_url, state="readonly")
        entry_url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        ttk.Button(url_row, text="Copy", command=self._copy_url).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(url_row, text="Open", command=self._open_webdav_url).pack(side=tk.LEFT)

        # Tailscale VPN URL
        ts_row = tk.Frame(net_card, bg=BG_CARD)
        ts_row.pack(fill=tk.X, pady=(10, 2))

        tk.Label(ts_row, text="Tailscale URL:", width=14, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        self.var_ts_url = tk.StringVar(value="")
        entry_ts_url = ttk.Entry(ts_row, textvariable=self.var_ts_url, state="readonly")
        entry_ts_url.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.btn_copy_ts = ttk.Button(ts_row, text="Copy", command=self._copy_ts_url)
        self.btn_copy_ts.pack(side=tk.LEFT, padx=(0, 4))

        ts_status_row = tk.Frame(net_card, bg=BG_CARD)
        ts_status_row.pack(fill=tk.X, pady=(4, 0))
        self.lbl_ts_status = tk.Label(ts_status_row, text="Checking Tailscale...", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED)
        self.lbl_ts_status.pack(side=tk.LEFT)

        # --- Card 3: Master Control Switch ---
        ctrl_card = self._create_card(parent, padding=12, pady=(0, 0))

        self.btn_toggle_share = ttk.Button(ctrl_card, text="Pause Sharing", command=self._toggle_share)
        self.btn_toggle_share.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(ctrl_card, text="Apply Changes & Restart Services", style="Accent.TButton", command=self._apply_settings).pack(side=tk.LEFT)

    def _build_guide_tab(self, parent: tk.Widget) -> None:
        guide_card = self._create_card(parent, padding=16, pady=(0, 0), fill=tk.BOTH)

        tk.Label(
            guide_card,
            text="LanDrop Distributed Virtual Drive Architecture",
            font=("Segoe UI", 12, "bold"),
            bg=BG_CARD,
            fg=COLOR_TEXT_MAIN,
        ).pack(anchor="w", pady=(0, 10))

        cards_info = [
            ("1. Unified Mount Letter (Z:)",
             "Both computers mount their local WebDAV bridge to the same drive letter (Z:). Opening Z: on any connected PC presents the exact same seamless directory tree."),
            ("2. Local Physical Pool & Distributed Catalog",
             "When you drop a file into Z:, it is stored safely in your local storage directory. LanDrop computes a fast hash and announces the update to all connected peers over UDP and HTTP."),
            ("3. Seekable Streaming via RemoteStreamReader",
             "Peers can open large video clips, music, or high-res photos instantly. LanDrop intercepts byte requests and streams chunks via HTTP Range headers without pre-downloading."),
            ("4. Multi-Network Mesh (LAN + Tailscale)",
             "If you are on the same Wi-Fi, LanDrop communicates via fast local subnet broadcast. When away from home, Tailscale mesh automatically routes transfers securely across the internet."),
        ]

        for num_title, body in cards_info:
            b_box = tk.Frame(guide_card, bg="#F8FAFC", padx=12, pady=10, highlightbackground=BORDER_CARD, highlightthickness=1)
            b_box.pack(fill=tk.X, pady=(0, 8))
            tk.Label(b_box, text=num_title, font=("Segoe UI", 9, "bold"), bg="#F8FAFC", fg=COLOR_PRIMARY).pack(anchor="w")
            tk.Label(b_box, text=body, font=("Segoe UI", 8), bg="#F8FAFC", fg=COLOR_TEXT_MAIN, justify=tk.LEFT, wraplength=640).pack(anchor="w", pady=(2, 0))

    # --- Actions & Handlers ---

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
            self.lbl_hero_drive.configure(text=f"LanDrop Drive ({letter})")
            self.lbl_mount_status.configure(text=f"● Mounted at {letter}", fg=COLOR_SUCCESS_TEXT)
            self.chip_drive.configure(text=f"{letter}\\ Mounted", bg="#E0F2FE", fg="#0369A1")
            self.btn_open_drive.configure(text=f"📂  Open LanDrop ({letter})", state="normal")
            self.btn_toggle_mount.configure(text="Remount")
        else:
            self.lbl_hero_drive.configure(text=f"LanDrop Drive ({letter}) [Offline]")
            self.lbl_mount_status.configure(text="○ Not Mounted", fg=COLOR_TEXT_MUTED)
            self.chip_drive.configure(text=f"{letter}\\ Offline", bg="#F1F5F9", fg=COLOR_TEXT_MUTED)
            self.btn_open_drive.configure(text="Mount Drive", state="normal")
            self.btn_toggle_mount.configure(text="Mount Drive")

    def _apply_webclient_fix(self) -> None:
        ok = optimize_webclient_registry()
        if ok:
            messagebox.showinfo("Optimization Applied", "Windows WebClient registry settings successfully optimized for 4GB transfers and loopback access.")
            self.set_status("Windows WebClient optimized.")
        else:
            messagebox.showinfo(
                "Windows Optimization",
                "WebClient optimization attempted.\nIf copying files larger than 47MB is restricted by Windows, run LanDrop once as Administrator to persist the 4GB registry key."
            )

    def _set_quota_preset(self, gb: int) -> None:
        self.var_quota_gb.set(str(gb))
        self._update_quota()

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

    def _change_physical_pool(self) -> None:
        new_dir = filedialog.askdirectory(
            title="Select New Storage Pool Folder",
            initialdir=self.vfs_provider.local_storage_dir,
        )
        if not new_dir:
            return

        new_abs = os.path.abspath(new_dir)
        self.config["storage_pool_dir"] = new_abs
        save_config(self.config)
        self.vfs_provider.local_storage_dir = new_abs
        self.lbl_pool_path.configure(text=new_abs)
        self.refresh_virtual_files()
        self.set_status(f"Storage pool relocated to: {new_abs}")

    def refresh_virtual_files(self) -> None:
        """Reload and refresh virtual storage files and quota."""
        # Scan local storage for any direct file additions
        self.mapping_table.scan_local_storage(
            storage_dir=self.vfs_provider.local_storage_dir,
            node_id=self.vfs_provider.node_id,
            device_name=self.vfs_provider.device_name,
            my_url=self.vfs_provider.get_my_url(),
        )

        # Update quota usage bar & labels
        total_quota = self.vfs_provider.quota_bytes
        total_used = self.mapping_table.get_total_used_bytes()
        pct = min(100.0, (total_used / total_quota) * 100.0) if total_quota > 0 else 0

        if hasattr(self, "progress_quota"):
            self.progress_quota["value"] = pct
        if hasattr(self, "lbl_pct_badge"):
            self.lbl_pct_badge.configure(text=f"{pct:.1f}% Used")
        if hasattr(self, "lbl_quota_usage"):
            free_bytes = max(0, total_quota - total_used)
            self.lbl_quota_usage.configure(
                text=f"Used: {format_bytes(total_used)}  •  Free: {format_bytes(free_bytes)}  •  Total Quota: {format_bytes(total_quota)}"
            )

        # Update local pool file counts
        active_entries = self.mapping_table.get_all_active_entries()
        local_count = sum(1 for e in active_entries if e.is_local)
        remote_count = len(active_entries) - local_count
        if hasattr(self, "lbl_pool_stats"):
            self.lbl_pool_stats.configure(
                text=f"{local_count} local item(s)  •  {remote_count} remote shared"
            )

        # Check mount status
        if hasattr(self, "var_drive_letter"):
            letter = self.var_drive_letter.get().rstrip(":") + ":"
            self._update_mount_display(is_drive_mounted(letter), letter)

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
            messagebox.showinfo("Synchronized", f"Successfully synchronized mapping tables with {peer.device_name}!")
        else:
            self.set_status(f"Failed to sync with {peer.device_name}")
            messagebox.showerror("Sync Failed", f"Could not sync with {peer.device_name} at {peer.webdav_url}.")

    def _copy_ts_url(self) -> None:
        url = self.var_ts_url.get()
        if url and url.startswith("http"):
            self.root.clipboard_clear()
            self.root.clipboard_append(url)
            self.set_status("Copied Tailscale WebDAV URL to clipboard!")

    def _manual_connect_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect to Remote Peer")
        dialog.geometry("420x210")
        dialog.configure(bg=BG_WINDOW)
        dialog.transient(self.root)
        dialog.grab_set()

        card = tk.Frame(dialog, bg=BG_CARD, highlightbackground=BORDER_CARD, highlightthickness=1, padx=16, pady=16)
        card.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)

        tk.Label(card, text="Connect to Remote Computer", font=("Segoe UI", 10, "bold"), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(anchor="w", pady=(0, 4))
        tk.Label(card, text="Enter the LAN IP Address or Tailscale MagicDNS/IP of the peer:", font=("Segoe UI", 8), bg=BG_CARD, fg=COLOR_TEXT_MUTED).pack(anchor="w", pady=(0, 10))

        row = tk.Frame(card, bg=BG_CARD)
        row.pack(fill=tk.X, pady=4)
        tk.Label(row, text="Address / IP:", width=12, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        var_addr = tk.StringVar(value="")
        entry = ttk.Entry(row, textvariable=var_addr)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus()

        row2 = tk.Frame(card, bg=BG_CARD)
        row2.pack(fill=tk.X, pady=4)
        tk.Label(row2, text="Port:", width=12, font=("Segoe UI", 9), bg=BG_CARD, fg=COLOR_TEXT_MAIN).pack(side=tk.LEFT)
        var_port = tk.StringVar(value=str(self.server.port))
        ttk.Entry(row2, textvariable=var_port, width=8).pack(side=tk.LEFT)

        btn_row = tk.Frame(card, bg=BG_CARD)
        btn_row.pack(fill=tk.X, pady=(14, 0))

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
                messagebox.showinfo("Connected", f"Connected and synchronized with {url}")
            else:
                messagebox.showerror("Connection Error", f"Failed to reach {url}")

        def _do_open():
            addr = var_addr.get().strip()
            port = var_port.get().strip()
            if not addr:
                return
            url = f"http://{addr}:{port}/"
            open_path_or_url(url)
            dialog.destroy()

        ttk.Button(btn_row, text="Sync Now", style="Accent.TButton", command=_do_sync).pack(side=tk.LEFT, padx=(0, 6))
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

        # Update header chips
        if hasattr(self, "chip_peers"):
            self.chip_peers.configure(text=f"👥 {len(peers)} Peer{'s' if len(peers) != 1 else ''}")

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
            self.chip_status.configure(text="● Online & Mesh Active", bg=COLOR_SUCCESS_BG, fg=COLOR_SUCCESS_TEXT)
            self.btn_toggle_share.configure(text="Pause Sharing")

            if ts_info.get("available") and ts_info.get("ip"):
                ts_url = f"http://{ts_info['ip']}:{self.server.port}/"
                self.var_ts_url.set(ts_url)
                dns_text = f" ({ts_info['magic_dns']})" if ts_info.get("magic_dns") else ""
                self.lbl_ts_status.configure(
                    text=f"● Tailscale: {ts_info['ip']}{dns_text}",
                    fg=COLOR_SUCCESS_TEXT,
                )
                self.btn_copy_ts.configure(state="normal")
            else:
                self.var_ts_url.set("Tailscale not active")
                self.lbl_ts_status.configure(
                    text=f"Tailscale: {ts_info.get('status', 'Not Detected')}",
                    fg=COLOR_TEXT_MUTED,
                )
                self.btn_copy_ts.configure(state="disabled")

            self.set_status(f"Virtual Drive active on {url}")
        else:
            self.var_url.set("")
            self.var_ts_url.set("")
            self.chip_status.configure(text="○ Sharing Paused", bg="#F1F5F9", fg=COLOR_TEXT_MUTED)
            self.btn_toggle_share.configure(text="Resume Sharing")
            self.lbl_ts_status.configure(text="Sharing paused", fg=COLOR_TEXT_MUTED)
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
