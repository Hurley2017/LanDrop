"""System tray integration for LanDrop."""

import logging
from typing import Callable, Optional
import pystray
from pystray import MenuItem as item, Menu
from .utils import create_app_icon

logger = logging.getLogger("landrop.tray")


class TrayIconManager:
    """Manages system tray icon, context menu, and background lifecycle."""

    def __init__(
        self,
        on_open_ui: Callable[[], None],
        on_open_folder: Callable[[], None],
        on_toggle_share: Callable[[], None],
        on_quit: Callable[[], None],
        is_sharing_active: Callable[[], bool],
    ):
        self._on_open_ui = on_open_ui
        self._on_open_folder = on_open_folder
        self._on_toggle_share = on_toggle_share
        self._on_quit = on_quit
        self._is_sharing_active = is_sharing_active
        self._icon: Optional[pystray.Icon] = None

    def start(self) -> None:
        """Create and launch system tray icon in detached thread."""
        image = create_app_icon(size=64)

        def _toggle_text(item_obj):
            return "Pause Sharing" if self._is_sharing_active() else "Resume Sharing"

        menu = Menu(
            item("Open LanDrop", lambda: self._on_open_ui(), default=True),
            item("Open Shared Folder", lambda: self._on_open_folder()),
            item(_toggle_text, lambda: self._on_toggle_share()),
            Menu.SEPARATOR,
            item("Quit LanDrop", lambda: self._on_quit()),
        )

        self._icon = pystray.Icon(
            name="LanDrop",
            icon=image,
            title="LanDrop - LAN File Sharing",
            menu=menu,
        )

        try:
            self._icon.run_detached()
            logger.info("System tray icon started")
        except Exception as e:
            logger.error("Failed to start tray icon: %s", e)

    def notify(self, title: str, message: str) -> None:
        """Display a system tray balloon notification."""
        if self._icon:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def stop(self) -> None:
        """Remove icon from system tray."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception as e:
                logger.debug("Error stopping tray icon: %s", e)
            self._icon = None
