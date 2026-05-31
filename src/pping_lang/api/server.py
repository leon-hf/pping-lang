"""ApiServer — uvicorn 跑在 daemon 线程，不阻塞 vLLM 主流程。

启动后会打印 dashboard URL 供 demo / 用户访问 (design §4.2)。
"""
from __future__ import annotations

import logging
import time
from threading import Thread
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import FastAPI


class ApiServer:
    """FastAPI app + uvicorn lifecycle on a daemon thread."""

    def __init__(
        self,
        app: FastAPI,
        host: str = "127.0.0.1",
        port: int = 8765,
    ) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._server = None
        self._thread: Thread | None = None
        self._actual_port: int | None = None

    @property
    def url(self) -> str:
        port = self._actual_port if self._actual_port is not None else self._port
        return f"http://{self._host}:{port}"

    def start(self) -> None:
        if self._thread is not None:
            return
        import uvicorn  # local import to avoid uvicorn cost when API disabled

        config = uvicorn.Config(
            self._app,
            host=self._host,
            port=self._port,
            log_level="warning",  # Quieter — vLLM logs already noisy
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = Thread(
            target=self._server.run,
            daemon=True,
            name="ApiServer",
        )
        self._thread.start()

        # Block briefly for server to bind, capture actual port (handles port=0)
        for _ in range(50):  # up to ~500ms
            if self._server.started:
                self._actual_port = self._extract_port()
                break
            time.sleep(0.01)

        logger.info("[pping-lang] dashboard at %s", self.url)
        # User-facing one-liner per design §4.2
        print(f"[pping-lang] dashboard at {self.url}", flush=True)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._server = None

    def _extract_port(self) -> int | None:
        """Read the actual bound port (relevant when port=0 auto-assign)."""
        if self._server is None:
            return None
        for sock_list in (getattr(self._server, "servers", None) or []):
            sockets = getattr(sock_list, "sockets", None)
            if sockets:
                try:
                    return sockets[0].getsockname()[1]
                except Exception:
                    pass
        return self._port
