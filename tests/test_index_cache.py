"""Tests for dy_cli.utils.index_cache"""
import os

import pytest

from dy_cli.utils import config, index_cache


@pytest.fixture(autouse=True)
def tmp_cache(tmp_path, monkeypatch):
    """Redirect index cache to temp dir."""
    cfg_dir = str(tmp_path / ".dy")
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(index_cache, "INDEX_FILE", os.path.join(cfg_dir, "index_cache.json"))
    os.makedirs(cfg_dir, exist_ok=True)


class TestSaveAndGet:
    def test_save_and_get(self):
        items = [
            {"aweme_id": "111", "desc": "video one", "author": {"nickname": "alice", "sec_uid": "s1"}},
            {"aweme_id": "222", "desc": "video two", "author": {"nickname": "bob", "sec_uid": "s2"}},
        ]
        index_cache.save_index(items)

        entry = index_cache.get_by_index(1)
        assert entry is not None
        assert entry["aweme_id"] == "111"

        entry2 = index_cache.get_by_index(2)
        assert entry2["aweme_id"] == "222"

    def test_get_out_of_range(self):
        index_cache.save_index([{"aweme_id": "111", "desc": "x", "author": {"nickname": "a"}}])
        assert index_cache.get_by_index(5) is None

    def test_get_zero(self):
        assert index_cache.get_by_index(0) is None

    def test_get_negative(self):
        assert index_cache.get_by_index(-1) is None

    def test_empty_cache(self):
        assert index_cache.get_by_index(1) is None
        assert index_cache.get_index_count() == 0

    def test_count(self):
        items = [{"aweme_id": str(i), "desc": "", "author": {}} for i in range(5)]
        index_cache.save_index(items)
        assert index_cache.get_index_count() == 5

    def test_skips_empty_aweme_id(self):
        items = [
            {"aweme_id": "", "desc": "no id"},
            {"aweme_id": "123", "desc": "has id", "author": {"nickname": "x"}},
        ]
        index_cache.save_index(items)
        assert index_cache.get_index_count() == 1
        assert index_cache.get_by_index(1)["aweme_id"] == "123"


class TestResolveId:
    def test_long_number_is_aweme_id(self):
        assert index_cache.resolve_id("7616133082231938235") == "7616133082231938235"

    def test_url_passthrough(self):
        url = "https://v.douyin.com/xxx/"
        assert index_cache.resolve_id(url) == url

    def test_short_index_resolved(self):
        index_cache.save_index([{"aweme_id": "999888777", "desc": "x", "author": {}}])
        assert index_cache.resolve_id("1") == "999888777"

    def test_short_index_empty_raises(self):
        with pytest.raises(ValueError, match="请先执行"):
            index_cache.resolve_id("1")

    def test_short_index_out_of_range_raises(self):
        index_cache.save_index([{"aweme_id": "111", "desc": "", "author": {}}])
        with pytest.raises(ValueError, match="超出范围"):
            index_cache.resolve_id("5")
