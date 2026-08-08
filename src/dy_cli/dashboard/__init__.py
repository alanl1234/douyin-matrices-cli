"""douyin-matrices dashboard — 账号矩阵后台（对齐 xiaohongshu-matrices-cli）。

本包提供：SQLite 账号/人设仓库、治理引擎、统一限流、跨账号编排与 FastAPI 后台。
"""
from __future__ import annotations

__all__ = ["config", "db", "governance", "rate_limit", "persistence", "orchestrator", "app"]
