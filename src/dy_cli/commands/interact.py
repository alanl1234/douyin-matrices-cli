"""
dy like / comment / favorite / follow — 互动命令 (Playwright)。

支持全局或局部 --account 指定矩阵账号（id / 别名 / douyin_user_id）。
"""
from __future__ import annotations

import click

from dy_cli.account_bridge import effective_account
from dy_cli.engines.playwright_client import PlaywrightClient, PlaywrightError
from dy_cli.utils.index_cache import resolve_id
from dy_cli.utils.output import error, info, print_comments, success


def _resolve(id_str: str) -> str:
    try:
        return resolve_id(id_str)
    except ValueError as e:
        error(str(e))
        raise SystemExit(1)


def _pw(account=None) -> PlaywrightClient:
    return PlaywrightClient(account=account, headless=True)


@click.command("like", help="点赞视频 (支持短索引: dy like 1)")
@click.argument("aweme_id")
@click.option("--unlike", is_flag=True, help="取消点赞")
@click.option("--account", default=None, help="指定矩阵账号（局部覆盖全局 --account）")
@click.pass_context
def like(ctx, aweme_id, unlike, account):
    """点赞或取消点赞。"""
    account = account or effective_account(ctx)
    aweme_id = _resolve(aweme_id)
    action = "unlike" if unlike else "like"
    action_cn = "取消点赞" if unlike else "点赞"
    info(f"正在{action_cn}: {aweme_id}")

    try:
        result = _pw(account).interact(aweme_id, action)
        if result.get("success"):
            success(f"{action_cn}成功 👍")
        else:
            error(f"{action_cn}失败: 未找到按钮")
            raise SystemExit(1)
    except PlaywrightError as e:
        error(f"{action_cn}失败: {e}")
        raise SystemExit(1)


@click.command("favorite", help="收藏视频 (支持短索引: dy fav 1)")
@click.argument("aweme_id")
@click.option("--unfavorite", is_flag=True, help="取消收藏")
@click.option("--account", default=None, help="指定矩阵账号（局部覆盖全局 --account）")
@click.pass_context
def favorite(ctx, aweme_id, unfavorite, account):
    """收藏或取消收藏。"""
    account = account or effective_account(ctx)
    aweme_id = _resolve(aweme_id)
    action = "unfavorite" if unfavorite else "favorite"
    action_cn = "取消收藏" if unfavorite else "收藏"
    info(f"正在{action_cn}: {aweme_id}")

    try:
        result = _pw(account).interact(aweme_id, action)
        if result.get("success"):
            success(f"{action_cn}成功 ⭐")
        else:
            error(f"{action_cn}失败: 未找到按钮")
            raise SystemExit(1)
    except PlaywrightError as e:
        error(f"{action_cn}失败: {e}")
        raise SystemExit(1)


@click.command("comment", help="评论视频 (支持短索引: dy comment 1 -c '好看')")
@click.argument("aweme_id")
@click.option("--content", "-c", required=True, help="评论内容")
@click.option("--account", default=None, help="指定矩阵账号（局部覆盖全局 --account）")
@click.pass_context
def comment(ctx, aweme_id, content, account):
    """发表评论。"""
    account = account or effective_account(ctx)
    aweme_id = _resolve(aweme_id)
    info(f"正在评论: {aweme_id}")

    try:
        result = _pw(account).interact(aweme_id, "comment", content=content)
        if result.get("success"):
            success("评论成功 💬")
        else:
            error("评论失败: 未找到输入框")
            raise SystemExit(1)
    except PlaywrightError as e:
        error(f"评论失败: {e}")
        raise SystemExit(1)


@click.command("comments", help="查看视频评论 (支持短索引)")
@click.argument("aweme_id")
@click.option("--count", type=int, default=20, help="评论数量")
@click.option("--account", default=None, help="指定矩阵账号（局部覆盖全局 --account）")
@click.option("--json-output", "as_json", is_flag=True, help="输出 JSON")
@click.pass_context
def comments(ctx, aweme_id, count, account, as_json):
    """查看视频评论列表 (Playwright 抓取)。"""
    account = account or effective_account(ctx)
    aweme_id = _resolve(aweme_id)
    info(f"正在获取评论: {aweme_id}")

    try:
        comment_list = _pw(account).get_comments(aweme_id, count=count)

        if as_json:
            from dy_cli.utils.output import print_json
            print_json(comment_list)
        else:
            print_comments(comment_list)

    except PlaywrightError as e:
        error(f"获取评论失败: {e}")
        raise SystemExit(1)


@click.command("follow", help="关注用户")
@click.argument("sec_user_id")
@click.option("--unfollow", is_flag=True, help="取消关注")
@click.option("--account", default=None, help="指定矩阵账号（局部覆盖全局 --account）")
@click.pass_context
def follow(ctx, sec_user_id, unfollow, account):
    """关注或取消关注用户。"""
    account = account or effective_account(ctx)
    action = "unfollow" if unfollow else "follow"
    action_cn = "取消关注" if unfollow else "关注"
    info(f"正在{action_cn}用户")

    try:
        result = _pw(account).interact("", action, sec_user_id=sec_user_id)
        if result.get("success"):
            success(f"{action_cn}成功 👥")
        else:
            error(f"{action_cn}失败: 未找到按钮")
            raise SystemExit(1)
    except PlaywrightError as e:
        error(f"{action_cn}失败: {e}")
        raise SystemExit(1)
