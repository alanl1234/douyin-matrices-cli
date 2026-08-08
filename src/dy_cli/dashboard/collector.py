"""抖音爆款采集层（对齐 xiaohongshu-matrices-cli collector / reading）。

提供按关键词/话题搜索抖音视频笔记、抓取评论并写入本地素材库的能力，支持断点恢复
（任务状态机 + 笔记 upsert 去重）。网络来源通过 ``search_fn`` / ``comment_fn`` 注入，
便于单元测试；真实抖音搜索端点需在验证后接入，此处不绑定任何未经验证的接口。

真实接线示例（待验证）：

    def douyin_search(keyword, page):
        client = PlaywrightClient(account=alias, headless=True)
        return client.search_aweme(keyword, page)

    collector = DouyinCollector(db, search_fn=douyin_search, comment_fn=douyin_comments)
"""
from __future__ import annotations

from typing import Any, Callable

from .utils import json_dumps, json_loads, now_iso

# search_fn(keyword: str, page: int) -> list[dict]  (raw aweme items)
# comment_fn(aweme_id: str, limit: int) -> list[dict]
SearchFn = Callable[[str, int], list[dict[str, Any]]]
CommentFn = Callable[[str, int], list[dict[str, Any]]]


class DouyinCollector:
    def __init__(
        self,
        db: Any,
        search_fn: SearchFn | None = None,
        comment_fn: CommentFn | None = None,
    ) -> None:
        self.db = db
        self.search_fn = search_fn
        self.comment_fn = comment_fn

    # ── 任务执行（供持久队列 kind='search' 调用）────────────────────────
    def run(self, job_id: int) -> str:
        job = self.db.get_search_job(job_id)
        if not job or job["status"] in ("cancelled", "paused", "complete"):
            return str(job["status"] if job else "failed")
        self.db.set_search_job_status(job_id, "running")
        try:
            if self.search_fn is None:
                raise RuntimeError("未配置搜索来源（search_fn），无法采集")
            keywords = json_loads(job["keywords_json"], [])
            topics = json_loads(job["topics_json"], [])
            seen = self.db.count_job_notes(job_id)
            collected = 0
            for kw in keywords or topics:
                for page in range(1, int(job["max_pages"]) + 1):
                    raw = self.search_fn(str(kw), page) or []
                    if not raw:
                        break
                    for aweme in raw:
                        note = self._normalize(aweme)
                        if not self._passes_filters(note, job):
                            continue
                        nid = self.db.upsert_note(note)
                        self.db.link_job_note(job_id, nid)
                        collected += 1
                        if int(job["include_comments"]):
                            self._attach_comments(note["aweme_id"], int(job["comment_limit"]))
                    self.db.execute(
                        "UPDATE search_jobs SET progress_current=?,progress_total=?,result_count=? WHERE id=?",
                        (collected + seen, collected + seen, collected + seen, job_id),
                    )
            self.db.set_search_job_status(job_id, "complete")
            return "complete"
        except Exception as exc:  # 失败状态机：便于恢复重试
            self.db.set_search_job_status(job_id, "failed", error=str(exc)[:500])
            return "failed"

    # ── 归一化 / 过滤 ──────────────────────────────────────────────────
    @staticmethod
    def _normalize(aweme: dict[str, Any]) -> dict[str, Any]:
        stats = aweme.get("statistics") or {}
        author = aweme.get("author") or {}
        covers = aweme.get("covers") or aweme.get("cover") or []
        return {
            "aweme_id": str(aweme.get("aweme_id") or aweme.get("awemeId") or aweme.get("id") or ""),
            "author_id": str(author.get("uid") or author.get("id") or aweme.get("author_id") or ""),
            "author_name": str(author.get("nickname") or author.get("name") or aweme.get("author_name") or ""),
            "desc": str(aweme.get("desc") or aweme.get("description") or ""),
            "publish_time": str(aweme.get("create_time") or aweme.get("publish_time") or ""),
            "media_type": "video" if not aweme.get("image_post_info") else "image",
            "original_url": str(aweme.get("share_url") or aweme.get("original_url") or ""),
            "likes": int(stats.get("dwz_liked_count") or stats.get("liked_count") or aweme.get("likes") or 0),
            "shares": int(stats.get("share_count") or aweme.get("shares") or 0),
            "comments": int(stats.get("comment_count") or aweme.get("comments") or 0),
            "collects": int(stats.get("collect_count") or aweme.get("collects") or 0),
            "topics_json": json_dumps(aweme.get("topics") or []),
            "covers_json": json_dumps(covers if isinstance(covers, list) else [covers]),
            "comments_json": "[]",
            "raw_json": json_dumps(aweme),
        }

    @staticmethod
    def _passes_filters(note: dict[str, Any], job: dict[str, Any]) -> bool:
        if not note["aweme_id"]:
            return False
        if int(job["min_likes"]) and note["likes"] < int(job["min_likes"]):
            return False
        if int(job["min_shares"]) and note["shares"] < int(job["min_shares"]):
            return False
        if int(job["min_comments"]) and note["comments"] < int(job["min_comments"]):
            return False
        return True

    def _attach_comments(self, aweme_id: str, limit: int) -> None:
        if self.comment_fn is None or not aweme_id:
            return
        try:
            comments = self.comment_fn(aweme_id, limit) or []
        except Exception:
            return
        existing = self.db.fetchone("SELECT id,comments_json FROM notes WHERE aweme_id=?", (aweme_id,))
        if not existing:
            return
        prev = json_loads(existing["comments_json"], []) if existing["comments_json"] else []
        merged = (prev + comments)[: max(limit, len(prev))]
        self.db.execute(
            "UPDATE notes SET comments_json=?,updated_at=? WHERE id=?",
            (json_dumps(merged), now_iso(), existing["id"]),
        )
