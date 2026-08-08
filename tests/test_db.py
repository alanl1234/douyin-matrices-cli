"""Tests for the dashboard SQLite repository (accounts / personas / tasks / markers)."""
from __future__ import annotations

from dy_cli.dashboard.config import DashboardConfig
from dy_cli.dashboard.db import Database


def _db(tmp_path) -> Database:
    cfg = DashboardConfig.load(data_dir=tmp_path / "data")
    return Database(cfg.database_path)


def test_account_crud(tmp_path):
    db = _db(tmp_path)
    aid = db.create_account(alias="acct1", cookie_file="/c/a.json", douyin_user_id="uid-9", nickname="昵称A")
    assert db.get_account(aid)["alias"] == "acct1"
    assert db.get_account_by_alias("acct1")["id"] == aid
    # resolve by id / alias / douyin_user_id
    assert db.resolve_account(aid)["id"] == aid
    assert db.resolve_account("acct1")["id"] == aid
    assert db.resolve_account("uid-9")["id"] == aid
    assert db.resolve_account("nonexistent") is None
    assert len(db.list_accounts()) == 1
    db.delete_account(aid)
    assert db.get_account(aid) is None


def test_persona_crud(tmp_path):
    db = _db(tmp_path)
    pid = db.create_persona(name="探店达人", tone="活泼", topics=["美食", "探店"], forbidden_words=["禁词"])
    assert db.get_persona(pid)["name"] == "探店达人"
    assert db.get_persona_by_name("探店达人")["id"] == pid
    assert db.get_persona_by_name("不存在") is None
    assert db.list_personas()[0]["topics_json"] == '["美食", "探店"]'
    db.delete_persona(pid)
    assert db.get_persona(pid) is None


def test_persona_delete_detaches_accounts(tmp_path):
    db = _db(tmp_path)
    pid = db.create_persona(name="p")
    aid = db.create_account(alias="a", cookie_file="a.json", persona_id=pid)
    db.delete_persona(pid)
    assert db.get_account(aid)["persona_id"] is None


def test_publish_task_crud(tmp_path):
    db = _db(tmp_path)
    aid = db.create_account(alias="a", cookie_file="a.json")
    tid = db.create_publish_task(aid, "标题", "正文", topics=["x"], media_type="video", media_paths=["/p.mp4"])
    task = db.get_publish_task(tid)
    assert task["title"] == "标题"
    assert task["topics_json"] == '["x"]'
    assert task["media_paths_json"] == '["/p.mp4"]'
    assert task["status"] == "pending_review"
    assert len(db.list_publish_tasks()) == 1
    assert len(db.list_publish_tasks(aid)) == 1


def test_markers(tmp_path):
    db = _db(tmp_path)
    assert not db.marker_exists("k")
    db.set_marker("k", "v")
    assert db.marker_exists("k")
    assert db.get_marker("k") == "v"
    db.set_marker("k", "v2")
    assert db.get_marker("k") == "v2"


def test_list_accounts_enabled_only(tmp_path):
    db = _db(tmp_path)
    db.create_account(alias="on", cookie_file="on.json")
    off_id = db.create_account(alias="off", cookie_file="off.json")
    db.update("accounts", off_id, enabled=0)
    assert len(db.list_accounts(enabled_only=True)) == 1
