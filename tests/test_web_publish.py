"""End-to-end smoke for the Phase B web publish closure.

Exercises the full HTTP path WITHOUT a real browser:
  - multipart upload of a fake media file
  - visibility / schedule / @mentions passed through to the task row
  - the "approve / publish now" endpoint runs the task and writes back the result URL

``publisher.publish_for_account`` is patched so we never touch Douyin.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dy_cli.dashboard import publisher as publisher_mod
from dy_cli.dashboard.app import create_app
from dy_cli.dashboard.config import DashboardConfig
from dy_cli.dashboard.db import Database


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    data_dir = tmp_path / "web_publish_data"
    monkeypatch.setenv("DY_MATRICES_DATA", str(data_dir))
    cfg = DashboardConfig.load(data_dir=data_dir)
    db = Database(cfg.database_path)
    app = create_app()
    client = TestClient(app)
    return client, db, cfg


def test_publish_upload_and_approve_closure(ctx, monkeypatch):
    client, db, cfg = ctx
    aid = db.create_account(alias="web_a", cookie_file="web_a.json", login_status="ready")

    # 1) multipart 创建发布任务（带可见范围 / 定时 / @好友 + 上传文件）
    fake_video = b"\x00\x01dummy-video-bytes"
    resp = client.post(
        "/api/publish",
        files={"files": ("clip.mp4", fake_video, "video/mp4")},
        data={
            "title": "测试标题",
            "body": "测试正文",
            "media_type": "video",
            "topics": "旅行,vlog",
            "mentions": "抖音小助手",
            "visibility": "好友可见",
            "schedule_at": "2026-08-10T12:00",
            "account_ids": str(aid),
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["created_task_ids"]

    # 2) 文件已落 uploads_dir
    uploads = list(cfg.uploads_dir.glob("*.mp4"))
    assert uploads, "上传文件应保存到 uploads_dir"

    # 3) 任务落库：可见范围 / 定时 / @好友 透传
    tasks_resp = client.get("/api/publish-tasks")
    tasks = tasks_resp.json()["tasks"]
    task = next(t for t in tasks if t["id"] in body["created_task_ids"])
    assert task["visibility"] == "好友可见"
    assert task["schedule_at"] == "2026-08-10T12:00"
    assert "抖音小助手" in task["mentions_json"]
    assert "好友可见" == task["visibility"]
    import json
    assert json.loads(task["media_paths_json"])[0].endswith("clip.mp4")
    assert task["status"] == "pending_review"

    # 4) 批准 / 立即发布：patch 掉真实发布，验证闭环与作品链接回写
    def fake_publish(alias, **kw):
        assert kw["visibility"] == "好友可见"
        assert kw["schedule"] == "2026-08-10T12:00"
        assert kw["mentions"] == ["抖音小助手"]
        return {"status": "published", "url": "https://v.douyin.com/webxyz", "message": "ok"}
    monkeypatch.setattr(publisher_mod, "publish_for_account", fake_publish)

    approve = client.post(f"/api/publish/{task['id']}/approve")
    assert approve.status_code == 200
    aresp = approve.json()
    assert aresp["ok"] is True
    assert aresp["status"] == "published"
    assert aresp["url"] == "https://v.douyin.com/webxyz"

    # 5) 任务列表回显作品链接
    after = client.get("/api/publish-tasks").json()["tasks"]
    upd = next(t for t in after if t["id"] == task["id"])
    assert upd["status"] == "published"
    assert upd["result_url"] == "https://v.douyin.com/webxyz"
