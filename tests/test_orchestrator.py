"""Tests for the governed cross-account orchestrator.

The orchestrator must never touch a real browser here: ``publisher.publish_for_account``
is patched so we exercise only the governance / dedup / accounting logic.
"""
from __future__ import annotations

import pytest

from dy_cli.dashboard.config import DashboardConfig
from dy_cli.dashboard.db import Database
from dy_cli.dashboard import orchestrator as orch_mod
from dy_cli.dashboard import publisher as publisher_mod
from dy_cli.dashboard.orchestrator import Orchestrator


@pytest.fixture
def setup(tmp_path):
    cfg = DashboardConfig.load(data_dir=tmp_path / "data")
    db = Database(cfg.database_path)
    orch = Orchestrator(db, cfg)
    return cfg, db, orch


def test_batch_publish_creates_tasks(setup):
    _, db, orch = setup
    db.create_account(alias="a1", cookie_file="a1.json")
    a2 = db.create_account(alias="a2", cookie_file="a2.json")
    db.update("accounts", a2, enabled=0)  # disabled -> excluded
    created = orch.batch_publish({"title": "T", "body": "B"}, group=None, account_ids=None)
    # only the enabled account gets a task
    assert len(created) == 1
    tasks = db.list_publish_tasks()
    assert len(tasks) == 1
    assert tasks[0]["account_id"] == 1


def test_batch_publish_respects_group(setup):
    _, db, orch = setup
    db.create_account(alias="g1", cookie_file="g1.json", group_name="beauty")
    db.create_account(alias="g2", cookie_file="g2.json", group_name="food")
    created = orch.batch_publish({"title": "T", "body": "B"}, group="beauty")
    assert len(created) == 1


def test_run_publish_success_records_history(setup, monkeypatch):
    _, db, orch = setup
    aid = db.create_account(alias="a1", cookie_file="a1.json")
    tid = orch.create_publish_task(
        aid, "标题", "正文内容",
        visibility="好友可见", schedule_at="2026-08-10T12:00", mentions=["抖音小助手"],
    )

    captured = {}
    def fake_publish(alias, **kw):
        captured.update(kw)
        return {"status": "published", "url": "https://v.douyin.com/abc", "message": "ok"}
    monkeypatch.setattr(publisher_mod, "publish_for_account", fake_publish)

    res = orch.run_publish_task(tid)
    assert res["ok"] is True
    assert res["url"] == "https://v.douyin.com/abc"
    # 阶段 B：可见范围 / 定时 / @好友 必须透传到 publisher
    assert captured["visibility"] == "好友可见"
    assert captured["schedule"] == "2026-08-10T12:00"
    assert captured["mentions"] == ["抖音小助手"]
    task = db.get_publish_task(tid)
    assert task["status"] == "published"
    assert task["result_url"] == "https://v.douyin.com/abc"
    # dedup history recorded for this account
    assert orch._recent(aid) == ["正文内容"]


def test_run_publish_governance_block(setup, monkeypatch):
    _, db, orch = setup
    aid = db.create_account(alias="a1", cookie_file="a1.json")
    tid = db.create_publish_task(aid, "标题", "加我微信 wx_abc123")

    called = {"n": 0}
    def fake_publish(alias, **kw):
        called["n"] += 1
        return {"success": True}
    monkeypatch.setattr(publisher_mod, "publish_for_account", fake_publish)

    res = orch.run_publish_task(tid)
    assert res["ok"] is False
    assert res["reason"] == "governance_block"
    assert db.get_publish_task(tid)["status"] == "blocked"
    assert called["n"] == 0  # publisher never invoked when blocked


def test_run_publish_dedup_blocks_duplicate(setup, monkeypatch):
    _, db, orch = setup
    aid = db.create_account(alias="a1", cookie_file="a1.json")

    def fake_publish(alias, **kw):
        return {"status": "published", "url": "https://v.douyin.com/x", "message": "ok"}
    monkeypatch.setattr(publisher_mod, "publish_for_account", fake_publish)

    t1 = db.create_publish_task(aid, "标题", "同一段推广文案")
    assert orch.run_publish_task(t1)["ok"] is True

    t2 = db.create_publish_task(aid, "标题", "同一段推广文案")
    res = orch.run_publish_task(t2)
    assert res["ok"] is False
    assert res["reason"] == "governance_block"
    assert db.get_publish_task(t2)["status"] == "blocked"


def test_daily_publish_limit_skips_task(setup):
    _, db, orch = setup
    orch.daily_publish_limit = 0  # nothing allowed today
    aid = db.create_account(alias="a1", cookie_file="a1.json")
    created = orch.batch_publish({"title": "T", "body": "B"})
    assert created == []
