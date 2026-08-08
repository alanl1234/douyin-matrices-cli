"""Entry point for the dy-dashboard command."""
from __future__ import annotations

import uvicorn

from .app import create_app


def main() -> None:
    app = create_app()
    host = "127.0.0.1"
    port = 8765
    print(f"[douyin-matrices] 后台已启动: http://{host}:{port}")
    print("[douyin-matrices] 按 Ctrl+C 停止")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
