"""WebDAV Server engine for LanDrop."""

import os
import threading
import logging
from typing import Optional, Callable
from cheroot import wsgi
from wsgidav.wsgidav_app import WsgiDAVApp
from wsgidav.dav_provider import DAVProvider
from wsgidav.fs_dav_provider import FilesystemProvider
from .utils import get_local_ip

logger = logging.getLogger("landrop.webdav")


class WebDAVServer:
    """Manages the lifecycle of the local WebDAV server and P2P Virtual Drive."""

    def __init__(self, on_status_change: Optional[Callable[[bool, Optional[str]], None]] = None):
        self._server: Optional[wsgi.Server] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._port: int = 8080
        self._shared_dir: str = ""
        self._read_only: bool = False
        self._on_status_change = on_status_change

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def port(self) -> int:
        return self._port

    @property
    def shared_dir(self) -> str:
        return self._shared_dir

    def get_url(self) -> str:
        """Returns the network WebDAV URL for clients to connect."""
        return f"http://{get_local_ip()}:{self._port}/"

    def start(
        self,
        shared_dir: str,
        port: int = 8080,
        read_only: bool = False,
        provider: Optional[DAVProvider] = None,
        sync_engine: Optional[object] = None,
    ) -> bool:
        """Start the WebDAV server on a background thread."""
        if self._running:
            self.stop()

        if not os.path.isdir(shared_dir):
            try:
                os.makedirs(shared_dir, exist_ok=True)
            except Exception as e:
                if self._on_status_change:
                    self._on_status_change(False, f"Cannot create directory: {e}")
                return False

        self._shared_dir = os.path.abspath(shared_dir)
        self._port = port
        self._read_only = read_only

        if provider is not None:
            active_provider = provider
        else:
            active_provider = FilesystemProvider(self._shared_dir, readonly=self._read_only)

        dav_config = {
            "host": "0.0.0.0",
            "port": self._port,
            "provider_mapping": {
                "/": active_provider
            },
            # Allow open access without credentials on the local network
            "simple_dc": {
                "user_mapping": {
                    "*": True
                }
            },
            "dir_browser": {
                "enable": True,
                "response_trailer": "LanDrop Virtual Drive WebDAV Server"
            },
            "verbose": 1,
            "logging": {
                "enable": False
            }
        }

        try:
            wsgi_app = WsgiDAVApp(dav_config)
            if sync_engine is not None:
                from .sync import P2PSyncMiddleware
                app_to_serve = P2PSyncMiddleware(wsgi_app, sync_engine)
            else:
                app_to_serve = wsgi_app

            self._server = wsgi.Server(
                ("0.0.0.0", self._port),
                app_to_serve,
                server_name=f"LanDrop/{self._port}"
            )
        except Exception as e:
            logger.error("Failed to configure WebDAV server: %s", e)
            if self._on_status_change:
                self._on_status_change(False, str(e))
            return False

        def _serve():
            try:
                self._running = True
                if self._on_status_change:
                    self._on_status_change(True, None)
                logger.info("WebDAV server listening on port %d serving %s", self._port, self._shared_dir)
                self._server.start()
            except Exception as e:
                logger.warning("WebDAV server stopped with error: %s", e)
            finally:
                self._running = False
                if self._on_status_change:
                    self._on_status_change(False, None)

        self._thread = threading.Thread(target=_serve, daemon=True, name="LanDrop-WebDAV")
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the running WebDAV server."""
        if self._server and self._running:
            try:
                self._server.stop()
            except Exception as e:
                logger.warning("Error stopping WebDAV server: %s", e)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._server = None
        self._thread = None
        self._running = False
        if self._on_status_change:
            self._on_status_change(False, None)
