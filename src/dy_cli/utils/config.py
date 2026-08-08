"""
全局配置管理 — ~/.dy/config.json
"""
from __future__ import annotations

import json
import os
from typing import Any

CONFIG_DIR = os.path.expanduser("~/.dy")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
COOKIES_DIR = os.path.join(CONFIG_DIR, "cookies")

DEFAULT_CONFIG: dict[str, Any] = {
    "api": {
        "cookie_file": os.path.join(COOKIES_DIR, "default.json"),
        "proxy": "",
        "timeout": 30,
    },
    "playwright": {
        "headless": False,
        "chromium_path": "",
        "slow_mo": 0,
    },
    "default": {
        "account": "default",
        "engine": "auto",       # auto | api | playwright
        "output": "table",      # table | json
        "download_dir": os.path.expanduser("~/Downloads/douyin"),
    },
}


def _ensure_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(COOKIES_DIR, exist_ok=True)


def load_config() -> dict[str, Any]:
    """加载配置文件，不存在则返回默认配置。"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                user_cfg = json.load(f)
            return _deep_merge(DEFAULT_CONFIG, user_cfg)
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg: dict[str, Any]):
    """保存配置到文件。"""
    _ensure_dir()
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def get(key_path: str, default: Any = None) -> Any:
    """获取嵌套配置值。key_path 格式: 'api.proxy', 'default.engine'"""
    cfg = load_config()
    keys = key_path.split(".")
    current = cfg
    for k in keys:
        if isinstance(current, dict) and k in current:
            current = current[k]
        else:
            return default
    return current


def set_value(key_path: str, value: Any):
    """设置嵌套配置值。"""
    cfg = load_config()
    keys = key_path.split(".")
    current = cfg
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value
    save_config(cfg)


def get_cookie_file(account: str | None = None) -> str:
    """获取指定账号的 Cookie 文件路径。"""
    _ensure_dir()
    account = account or load_config()["default"]["account"]
    return os.path.join(COOKIES_DIR, f"{account}.json")


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典。"""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
