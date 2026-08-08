"""Tests for the engagement governance layer (灰度 + 暖线索 + 预算 + 停止名单)."""
from __future__ import annotations

from dy_cli.dashboard.db import Database
from dy_cli.dashboard.config import DashboardConfig
from dy_cli.dashboard.engagement import EngagementBlocked, EngagementGovernor, EngagementRule


def _db():
    cfg = DashboardConfig.load()
    return Database(cfg.database_path)


def _ready_account(db, alias="a1"):
    return db.create_account(alias=alias, cookie_file=f"{alias}.json", login_status="ready", enabled=1)


def test_shadow_mode_blocks_everything():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="shadow")
    for kind in ("comment", "comment_reply", "dm_outbound", "dm_reply"):
        try:
            g.preflight(aid, kind)
            assert False, f"shadow should block {kind}"
        except EngagementBlocked as e:
            assert "影子" in str(e)


def test_inbound_allows_replies_blocks_outbound():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="inbound")
    # replies / inbound allowed
    g.preflight(aid, "comment_reply")
    g.preflight(aid, "dm_reply")
    # active outbound blocked
    for kind in ("comment", "dm_outbound"):
        try:
            g.preflight(aid, kind)
            assert False
        except EngagementBlocked as e:
            assert "入站" in str(e)


def test_reviewed_allows_and_checks_account_state():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="reviewed")
    g.preflight(aid, "comment")  # ok

    disabled = db.create_account(alias="off", cookie_file="off.json", enabled=0)
    try:
        g.preflight(disabled, "comment")
        assert False
    except EngagementBlocked as e:
        assert "停用" in str(e)

    unbound = db.create_account(alias="ub", cookie_file="ub.json", login_status="unbound")
    try:
        g.preflight(unbound, "comment")
        assert False
    except EngagementBlocked as e:
        assert "登录" in str(e)


def test_opt_out_blocks_and_blacklists_target():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="reviewed")
    try:
        g.preflight(aid, "dm_reply", target_user_id="u123", content="不要再联系我了，退订")
        assert False
    except EngagementBlocked as e:
        assert "opt-out" in str(e)
    assert db.get_target_contact("u123")["blocked"] == 1


def test_sensitive_content_blocked():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="reviewed")
    try:
        g.preflight(aid, "comment", content="加我微信 wx_abc12345 详聊", target_user_id="u9")
        assert False
    except EngagementBlocked as e:
        assert "敏感" in str(e)


def test_dm_outbound_requires_warm_lead():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="reviewed")
    try:
        g.preflight(aid, "dm_outbound", target_user_id="u1")
        assert False
    except EngagementBlocked as e:
        assert "暖线索" in str(e)
    # with warm lead -> allowed
    g.preflight(aid, "dm_outbound", target_user_id="u1", warm_lead=True)


def test_target_blocked_and_cooldown():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="reviewed")
    db.block_target("blocked_u", "manual")
    try:
        g.preflight(aid, "comment", target_user_id="blocked_u")
        assert False
    except EngagementBlocked as e:
        assert "停止名单" in str(e)

    # cooldown: a recent contact within cooldown window blocks
    db.upsert_target_contact("cool_u", last_account_id=aid)
    try:
        g.preflight(aid, "comment", target_user_id="cool_u")
        assert False
    except EngagementBlocked as e:
        assert "冷却" in str(e)


def test_daily_budget_enforced():
    db = _db()
    aid = _ready_account(db)
    rule = EngagementRule(comment_daily=1)
    g = EngagementGovernor(db, rule=rule, mode="reviewed")
    g.record(aid, "comment", "t1", "hi")
    try:
        g.preflight(aid, "comment")
        assert False
    except EngagementBlocked as e:
        assert "日限额" in str(e)


def test_record_persists_action_and_contact():
    db = _db()
    aid = _ready_account(db)
    g = EngagementGovernor(db, mode="reviewed")
    g.record(aid, "comment", "t2", "hello")
    assert db.count_engagement_since(aid, "comment", "2000-01-01T00:00:00+00:00") == 1
    assert db.get_target_contact("t2")["last_account_id"] == aid
