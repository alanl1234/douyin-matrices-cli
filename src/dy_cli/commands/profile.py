"""
dy me / profile — 用户信息命令。
"""
from __future__ import annotations

import click

from dy_cli.account_bridge import effective_account
from dy_cli.engines.api_client import DouyinAPIClient, DouyinAPIError
from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError
from dy_cli.utils.output import (
    console,
    error,
    info,
    print_json,
    print_user_profile,
    print_videos,
    success,
)


@click.command("me", help="查看自己的账号信息")
@click.option("--account", default=None, help="账号名（局部覆盖全局 --account）")
@click.option("--json-output", "as_json", is_flag=True, help="输出 JSON")
@click.pass_context
def me(ctx, account, as_json):
    """查看当前登录账号信息。"""
    account = account or effective_account(ctx)
    client = PlaywrightClient(account=account, headless=True)

    if not client.cookie_exists():
        error("未登录，请先运行: dy login")
        raise SystemExit(1)

    info("正在检查登录状态...")
    try:
        logged_in = client.check_login()
        if logged_in:
            success("已登录抖音 ✅")
            console.print(f"  [bold]Cookie:[/] {client.cookie_file}")
        else:
            error("Cookie 已失效，请重新登录: dy login")
            raise SystemExit(1)
    except PlaywrightError as e:
        error(f"检查失败: {e}")
        raise SystemExit(1)


@click.command("profile", help="查看用户主页")
@click.argument("sec_user_id")
@click.option("--posts", is_flag=True, help="同时加载作品列表")
@click.option("--post-count", type=int, default=20, help="作品数量 (默认 20)")
@click.option("--account", default=None, help="使用指定账号（局部覆盖全局 --account）")
@click.option("--json-output", "as_json", is_flag=True, help="输出 JSON")
@click.pass_context
def profile(ctx, sec_user_id, posts, post_count, account, as_json):
    """查看用户主页信息和作品。"""
    account = account or effective_account(ctx)
    client = DouyinAPIClient.from_config(account)

    try:
        info("正在获取用户资料...")
        user = client.get_user_profile(sec_user_id)

        if as_json and not posts:
            print_json(user)
            return

        print_user_profile(user)

        # Load posts
        if posts:
            info("正在获取作品列表...")
            post_data = client.get_user_posts(sec_user_id, count=post_count)
            aweme_list = post_data.get("aweme_list", [])

            if as_json:
                print_json({"user": user, "posts": aweme_list})
            else:
                nickname = user.get("nickname", "")
                print_videos(aweme_list, keyword=f"{nickname} 的作品")

    except DouyinAPIError as e:
        error(f"获取用户资料失败: {e}")
        raise SystemExit(1)
    finally:
        client.close()
