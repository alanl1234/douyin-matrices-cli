"""
dy account — 账号矩阵管理命令。

在 dy 原有「每账号 cookie 文件」之上叠加 SQLite 账号矩阵：别名、抖音号、
昵称、人设(persona)、分组与启用状态。所有 cookie 仍存于 ~/.dy/cookies，
矩阵层只登记元数据，绝不改写全局 cookie。
"""
from __future__ import annotations

import os

import click
from rich import box
from rich.table import Table

from dy_cli.account_bridge import (
    legacy_cookie_file,
    list_matrix_accounts,
    register_or_update_account,
    resolve_account_row,
    verify_account_cookies,
)
from dy_cli.engines.playwright_client import PlaywrightClient
from dy_cli.utils import config as dy_config
from dy_cli.utils.output import console, error, info, success, warning


@click.group("account", help="账号矩阵管理（多账号 + 人设 + 分组）")
def account_group():
    pass


def _legacy_cookie_names() -> list[str]:
    d = dy_config.COOKIES_DIR
    if not os.path.isdir(d):
        return []
    return sorted(f[:-5] for f in os.listdir(d) if f.endswith(".json"))


@account_group.command("list", help="列出矩阵账号（含人设/分组/状态）")
def list_accounts():
    """列出矩阵账号；若后台未初始化则回退到旧版 cookie 列表。"""
    accounts = list_matrix_accounts()
    default_account = dy_config.load_config()["default"]["account"]

    # 合并尚未登记进矩阵的旧 cookie 文件
    known = {a["alias"] for a in accounts}
    legacy_only = [n for n in _legacy_cookie_names() if n not in known]

    if not accounts and not legacy_only:
        info("暂无账号。使用 [bold]dy account add <名称>[/] 添加并登录。")
        return

    table = Table(title="🎬 账号矩阵", box=box.ROUNDED)
    table.add_column("ID", justify="right")
    table.add_column("别名", style="bold")
    table.add_column("抖音号")
    table.add_column("昵称")
    table.add_column("人设")
    table.add_column("分组")
    table.add_column("状态")
    table.add_column("默认", justify="center")

    # persona 名称映射
    persona_names: dict[int, str] = {}
    try:
        from dy_cli.dashboard.config import DashboardConfig
        from dy_cli.dashboard.db import Database

        cfg = DashboardConfig.load()
        if cfg.database_path.exists():
            db = Database(cfg.database_path)
            for p in db.list_personas():
                persona_names[int(p["id"])] = p["name"]
    except Exception:
        pass

    for a in accounts:
        pid = a.get("persona_id")
        persona = persona_names.get(int(pid)) if pid else "—"
        status = a.get("login_status") or "?"
        marker = "✓" if status in ("ready",) else "·"
        is_default = "⭐" if a.get("alias") == default_account else ""
        table.add_row(
            str(a.get("id") or ""),
            a.get("alias") or "",
            a.get("douyin_user_id") or "—",
            a.get("nickname") or "—",
            persona,
            a.get("group_name") or "—",
            f"{marker} {status}",
            is_default,
        )
    for n in legacy_only:
        table.add_row("", n, "—", "—", "—", "—", "· legacy", "⭐" if n == default_account else "")

    console.print(table)


@account_group.command("add", help="添加账号并扫码登录，登记进矩阵")
@click.argument("name")
@click.option("--uid", "douyin_user_id", default="", help="抖音号（可稍后用 update 补）")
@click.option("--nickname", default="", help="昵称（可稍后用 update 补）")
@click.option("--group", "group_name", default="", help="账号分组（矩阵分组）")
def add_account(name, douyin_user_id, nickname, group_name):
    """添加新账号、打开浏览器登录并登记进矩阵。"""
    cookie_file = dy_config.get_cookie_file(name)
    if os.path.isfile(cookie_file):
        if not click.confirm(f"账号 '{name}' 已存在，是否重新登录?", default=False):
            return

    info(f"正在为账号 '{name}' 打开登录页面...")
    client = PlaywrightClient(account=name, headless=False)
    try:
        ok = client.login()
    except Exception as e:
        error(f"登录失败: {e}")
        raise SystemExit(1)
    if not ok:
        error("登录失败")
        raise SystemExit(1)

    register_or_update_account(
        name,
        cookie_file=cookie_file,
        douyin_user_id=douyin_user_id,
        nickname=nickname,
        login_status="ready",
        group_name=group_name,
    )
    success(f"账号 '{name}' 已添加并登记进矩阵")


@account_group.command("remove", help="删除账号（cookie 文件 + 矩阵记录）")
@click.argument("name")
@click.confirmation_option(prompt="确认删除此账号?")
def remove_account(name):
    """删除账号（Cookie 文件 + 矩阵记录）。"""
    cookie_file = dy_config.get_cookie_file(name)
    removed_file = os.path.isfile(cookie_file)
    if removed_file:
        os.remove(cookie_file)

    try:
        from dy_cli.dashboard.config import DashboardConfig
        from dy_cli.dashboard.db import Database

        cfg = DashboardConfig.load()
        if cfg.database_path.exists():
            db = Database(cfg.database_path)
            row = db.get_account_by_alias(name)
            if row:
                db.delete_account(int(row["id"]))
    except Exception:
        pass

    if removed_file:
        success(f"账号 '{name}' 已删除")
    else:
        error(f"账号 '{name}' 不存在")


@account_group.command("default", help="设置默认账号")
@click.argument("name")
def set_default(name):
    """设置默认账号（旧版机制，未指定 --account 时使用）。"""
    cookie_file = dy_config.get_cookie_file(name)
    if not os.path.isfile(cookie_file):
        warning(f"账号 '{name}' 尚未登录")
        if not click.confirm("仍要设为默认?", default=False):
            return
    dy_config.set_value("default.account", name)
    success(f"默认账号已设为: {name}")


@account_group.command("update", help="更新账号元数据（抖音号/昵称/分组/启停）")
@click.argument("name")
@click.option("--uid", "douyin_user_id", default=None, help="抖音号")
@click.option("--nickname", default=None, help="昵称")
@click.option("--group", "group_name", default=None, help="分组")
@click.option("--enable/--disable", "enabled", default=None, help="启用/停用该账号（编排时跳过停用账号）")
def update_account(name, douyin_user_id, nickname, group_name, enabled):
    """更新矩阵的账号元数据。"""
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    row = db.get_account_by_alias(name)
    if not row:
        # 若旧 cookie 存在但不在矩阵，先登记
        if os.path.isfile(legacy_cookie_file(name)):
            register_or_update_account(name, login_status="ready")
            row = db.get_account_by_alias(name)
        else:
            error(f"账号 '{name}' 不存在（请先 dy account add）")
            raise SystemExit(1)

    changes: dict = {}
    if douyin_user_id is not None:
        changes["douyin_user_id"] = douyin_user_id
    if nickname is not None:
        changes["nickname"] = nickname
    if group_name is not None:
        changes["group_name"] = group_name
    if enabled is not None:
        changes["enabled"] = 1 if enabled else 0
    if changes:
        db.update("accounts", int(row["id"]), **changes)
        success(f"账号 '{name}' 已更新: {changes}")
    else:
        info("未提供任何更新项")


@account_group.command("import-legacy", help="把 ~/.dy/cookies 下未登记的旧账号导入矩阵")
def import_legacy():
    """扫描旧 cookie 目录并登记进矩阵。"""
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    imported = 0
    for n in _legacy_cookie_names():
        if not db.get_account_by_alias(n):
            db.create_account(alias=n, cookie_file=legacy_cookie_file(n), login_status="ready")
            imported += 1
    if imported:
        success(f"已导入 {imported} 个旧账号")
    else:
        info("没有需要导入的旧账号")


@account_group.command("verify", help="校验账号登录态（检查 cookie 文件是否有效）")
@click.argument("name")
def verify_account(name):
    """校验账号的 cookie 登录态，并更新矩阵记录。"""
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    row = db.get_account_by_alias(name)
    if not row:
        error(f"账号 '{name}' 不存在（请先 dy account add）")
        raise SystemExit(1)
    ok, reason = verify_account_cookies(name)
    if ok:
        db.mark_verified(int(row["id"]))
        success(f"账号 '{name}' 登录态有效")
    else:
        db.set_login_status(int(row["id"]), "unbound", error=reason)
        warning(f"账号 '{name}' 登录态异常: {reason}")


@account_group.command("toggle", help="启用/停用账号（编排时跳过停用账号）")
@click.argument("name")
def toggle_account(name):
    """切换账号的启用状态。"""
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    row = db.get_account_by_alias(name)
    if not row:
        error(f"账号 '{name}' 不存在（请先 dy account add）")
        raise SystemExit(1)
    new_enabled = 0 if row.get("enabled") else 1
    db.update("accounts", int(row["id"]), enabled=new_enabled)
    success(f"账号 '{name}' 已{'启用' if new_enabled else '停用'}")


# ── 人设（persona）管理 ────────────────────────────────────────────────────
@account_group.group("persona", help="人设库管理")
def persona_group():
    pass


@persona_group.command("create", help="创建人设")
@click.argument("name")
@click.option("--tone", default="", help="语气风格")
@click.option("--bio", default="", help="简介模板")
@click.option("--topics", default="", help="擅长话题，逗号分隔")
@click.option("--forbidden", default="", help="禁词，逗号分隔")
def persona_create(name, tone, bio, topics, forbidden):
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database
    from dy_cli.dashboard.utils import split_terms

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    if db.get_persona_by_name(name):
        error(f"人设 '{name}' 已存在")
        raise SystemExit(1)
    db.create_persona(
        name=name,
        tone=tone,
        bio=bio,
        topics=split_terms(topics),
        forbidden_words=split_terms(forbidden),
    )
    success(f"人设 '{name}' 已创建")


@persona_group.command("list", help="列出人设")
def persona_list():
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    if not cfg.database_path.exists():
        info("尚未创建任何人设")
        return
    db = Database(cfg.database_path)
    rows = db.list_personas()
    if not rows:
        info("尚未创建任何人设")
        return
    table = Table(title="🎭 人设库", box=box.ROUNDED)
    table.add_column("ID", justify="right")
    table.add_column("名称", style="bold")
    table.add_column("语气")
    table.add_column("擅长话题")
    table.add_column("禁词")
    for p in rows:
        from dy_cli.dashboard.utils import json_loads

        topics = json_loads(p["topics_json"], [])
        forbidden = json_loads(p["forbidden_words_json"], [])
        table.add_row(
            str(p["id"]), p["name"], p.get("tone") or "—",
            ", ".join(topics) or "—", ", ".join(forbidden) or "—",
        )
    console.print(table)


@persona_group.command("bind", help="把人设绑定到账号")
@click.argument("alias")
@click.argument("persona_name")
def persona_bind(alias, persona_name):
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    acc = db.get_account_by_alias(alias)
    if not acc:
        error(f"账号 '{alias}' 不存在，请先 dy account add")
        raise SystemExit(1)
    persona = db.get_persona_by_name(persona_name)
    if not persona:
        error(f"人设 '{persona_name}' 不存在，请先 dy account persona create")
        raise SystemExit(1)
    db.update("accounts", int(acc["id"]), persona_id=int(persona["id"]))
    success(f"账号 '{alias}' 已绑定人设 '{persona_name}'")


@persona_group.command("unbind", help="解绑账号的人设")
@click.argument("alias")
def persona_unbind(alias):
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    acc = db.get_account_by_alias(alias)
    if not acc:
        error(f"账号 '{alias}' 不存在")
        raise SystemExit(1)
    db.update("accounts", int(acc["id"]), persona_id=None)
    success(f"账号 '{alias}' 已解绑人设")


@persona_group.command("delete", help="删除人设（同时解绑相关账号）")
@click.argument("name")
@click.confirmation_option(prompt="确认删除此人设?")
def persona_delete(name):
    from dy_cli.dashboard.config import DashboardConfig
    from dy_cli.dashboard.db import Database

    cfg = DashboardConfig.load()
    db = Database(cfg.database_path)
    persona = db.get_persona_by_name(name)
    if not persona:
        error(f"人设 '{name}' 不存在")
        raise SystemExit(1)
    db.delete_persona(int(persona["id"]))
    success(f"人设 '{name}' 已删除")
