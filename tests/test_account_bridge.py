"""Tests for the matrix <-> CLI account bridge (identifier -> alias resolution)."""
from __future__ import annotations

from types import SimpleNamespace

from dy_cli import account_bridge as ab


def test_register_then_update(tmp_path):
    cookie = tmp_path / "acct1.json"
    cookie.write_text("{}")
    aid = ab.register_or_update_account(
        "acct1",
        cookie_file=str(cookie),
        douyin_user_id="uid-9",
        nickname="昵称A",
    )
    # second call with same alias updates instead of inserting
    aid2 = ab.register_or_update_account("acct1", douyin_user_id="uid-9", nickname="改名B")
    assert aid == aid2
    assert ab.resolve_account_row("acct1")["nickname"] == "改名B"


def test_resolve_by_id_alias_uid(tmp_path):
    ab.register_or_update_account("acct2", douyin_user_id="uid-2")
    assert ab.resolve_account_row("acct2")["alias"] == "acct2"
    row = ab.resolve_account_row("uid-2")
    assert row["alias"] == "acct2"
    # resolve by numeric id
    aid = ab.resolve_account_row("acct2")["id"]
    assert ab.resolve_account_row(aid)["alias"] == "acct2"


def test_resolve_cookie_file(tmp_path, monkeypatch):
    cookie = tmp_path / "acct3.json"
    cookie.write_text("{}")
    ab.register_or_update_account("acct3", cookie_file=str(cookie))
    # DB row with an existing cookie file -> returned directly
    assert ab.resolve_cookie_file("acct3") == str(cookie)

    # legacy account (no DB row) with an existing ~/.dy/cookies/<id>.json file
    legacy_dir = tmp_path / "legacy_cookies"
    legacy_dir.mkdir()
    monkeypatch.setattr(ab, "LEGACY_COOKIES_DIR", str(legacy_dir))
    legacy_file = legacy_dir / "ghost.json"
    legacy_file.write_text("{}")
    assert ab.resolve_cookie_file("ghost") == str(legacy_file)

    # unknown account with no cookie file anywhere -> None
    assert ab.resolve_cookie_file("nowhere") is None


def test_list_matrix_accounts(tmp_path):
    ab.register_or_update_account("acct4", nickname="N4")
    rows = ab.list_matrix_accounts()
    assert any(r["alias"] == "acct4" for r in rows)
    assert all("has_cookie_file" in r for r in rows)


def test_effective_account_none_when_no_ctx():
    assert ab.effective_account(None) is None
    ctx = SimpleNamespace(obj={})
    assert ab.effective_account(ctx) is None


def test_effective_account_resolves_alias(tmp_path):
    ab.register_or_update_account("acct5", douyin_user_id="uid-5")
    ctx = SimpleNamespace(obj={"account": "uid-5"})
    assert ab.effective_account(ctx) == "acct5"
