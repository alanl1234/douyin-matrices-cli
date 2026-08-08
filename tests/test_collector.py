"""Tests for the Douyin collector (search + comments into the local material DB).

Network sources are injected via ``search_fn`` / ``comment_fn`` so no real
Douyin endpoint is touched. We verify normalization, filtering, upsert dedup,
comment attachment, and crash-safe resume.
"""
from __future__ import annotations

import json

from dy_cli.dashboard.config import DashboardConfig
from dy_cli.dashboard.collector import DouyinCollector
from dy_cli.dashboard.db import Database
from dy_cli.dashboard.utils import json_dumps


def _db(tmp_path) -> Database:
    cfg = DashboardConfig.load(data_dir=tmp_path / "data")
    return Database(cfg.database_path)


def _raw_aweme(aid: str, likes: int = 10, has_image: bool = False) -> dict:
    aweme = {
        "aweme_id": aid,
        "desc": f"desc-{aid}",
        "statistics": {"liked_count": likes, "comment_count": 1},
        "author": {"uid": f"u-{aid}", "nickname": f"作者-{aid}"},
        "share_url": f"https://douyin.com/{aid}",
    }
    if has_image:
        aweme["image_post_info"] = {"images": []}
    return aweme


def test_normalize_maps_fields():
    note = DouyinCollector._normalize(_raw_aweme("777", likes=500))
    assert note["aweme_id"] == "777"
    assert note["author_name"] == "作者-777"
    assert note["likes"] == 500
    assert note["desc"] == "desc-777"
    assert note["media_type"] == "video"


def test_normalize_image_post():
    note = DouyinCollector._normalize(_raw_aweme("8", has_image=True))
    assert note["media_type"] == "image"


def test_passes_filters_min_likes():
    job = {"min_likes": 100, "min_shares": 0, "min_comments": 0}
    good = {"aweme_id": "x", "likes": 200, "shares": 0, "comments": 0}
    bad = {"aweme_id": "x", "likes": 10, "shares": 0, "comments": 0}
    assert DouyinCollector._passes_filters(good, job) is True
    assert DouyinCollector._passes_filters(bad, job) is False


def test_passes_filters_missing_id():
    job = {"min_likes": 0, "min_shares": 0, "min_comments": 0}
    assert DouyinCollector._passes_filters({"aweme_id": "", "likes": 5}, job) is False


def test_run_collects_and_links(tmp_path):
    db = _db(tmp_path)
    jid = db.create_search_job(
        {"name": "美食", "keywords_json": json_dumps(["美食"]), "max_pages": 2, "min_likes": 0}
    )

    def search_fn(kw, page):
        if page != 1:
            return []
        return [_raw_aweme("A"), _raw_aweme("B", likes=50)]

    c = DouyinCollector(db, search_fn=search_fn)
    status = c.run(jid)
    assert status == "complete"
    job = db.get_search_job(jid)
    assert job["status"] == "complete"
    # notes persisted + linked to the job
    notes = db.list_job_notes(jid)
    assert {n["aweme_id"] for n in notes} == {"A", "B"}
    assert db.count_job_notes(jid) == 2


def test_run_respects_no_search_fn(tmp_path):
    db = _db(tmp_path)
    jid = db.create_search_job({"name": "j", "keywords_json": json_dumps(["x"])})
    c = DouyinCollector(db)  # no search_fn
    status = c.run(jid)
    assert status == "failed"
    assert db.get_search_job(jid)["error"]


def test_run_attaches_comments(tmp_path):
    db = _db(tmp_path)
    jid = db.create_search_job(
        {"name": "j", "keywords_json": json_dumps(["k"]), "include_comments": 1, "comment_limit": 10}
    )

    def search_fn(kw, page):
        return [_raw_aweme("C")] if page == 1 else []

    def comment_fn(aweme_id, limit):
        return [{"text": "太好了"}, {"text": "求链接"}]

    c = DouyinCollector(db, search_fn=search_fn, comment_fn=comment_fn)
    assert c.run(jid) == "complete"

    note = db.fetchone("SELECT comments_json FROM notes WHERE aweme_id=?", ("C",))
    loaded = json.loads(note["comments_json"])
    texts = {c.get("text") for c in loaded}
    assert {"太好了", "求链接"}.issubset(texts)


def test_resume_after_failure_dedups(tmp_path):
    db = _db(tmp_path)
    jid = db.create_search_job(
        {"name": "j", "keywords_json": json_dumps(["美食"]), "max_pages": 3}
    )

    state = {"calls": 0, "fail": True}

    def failing_search(kw, page):
        state["calls"] += 1
        if state["calls"] >= 2 and state["fail"]:
            raise RuntimeError("network blip")
        return [_raw_aweme("A")]

    c = DouyinCollector(db, search_fn=failing_search)
    assert c.run(jid) == "failed"
    assert db.count_job_notes(jid) == 1  # A was upserted before the blip

    # resume: stop failing, re-run (status 'failed' is re-executable)
    state["fail"] = False

    def good_search(kw, page):
        if page != 1:
            return []
        return [_raw_aweme("A"), _raw_aweme("B", likes=30)]

    c.search_fn = good_search
    assert c.run(jid) == "complete"
    # A is deduped, B is new -> 2 linked notes total
    assert db.count_job_notes(jid) == 2
    assert {n["aweme_id"] for n in db.list_job_notes(jid)} == {"A", "B"}


def test_run_skips_terminal_jobs(tmp_path):
    db = _db(tmp_path)
    jid = db.create_search_job({"name": "j", "keywords_json": json_dumps(["k"])})
    db.set_search_job_status(jid, "complete")

    calls = {"n": 0}

    def search_fn(kw, page):
        calls["n"] += 1
        return []

    c = DouyinCollector(db, search_fn=search_fn)
    assert c.run(jid) == "complete"
    assert calls["n"] == 0  # complete jobs are not re-collected
