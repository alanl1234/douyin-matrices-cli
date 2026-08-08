"""Tests for dy_cli.utils.config"""
import os

import pytest

from dy_cli.utils import config


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    """Redirect config to temp dir for every test."""
    cfg_dir = str(tmp_path / ".dy")
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(config, "CONFIG_FILE", os.path.join(cfg_dir, "config.json"))
    monkeypatch.setattr(config, "COOKIES_DIR", os.path.join(cfg_dir, "cookies"))
    yield cfg_dir


class TestLoadSave:
    def test_default_config(self):
        cfg = config.load_config()
        assert cfg["default"]["engine"] == "auto"
        assert cfg["api"]["timeout"] == 30

    def test_save_and_load(self):
        cfg = config.load_config()
        cfg["api"]["proxy"] = "http://test:8080"
        config.save_config(cfg)

        loaded = config.load_config()
        assert loaded["api"]["proxy"] == "http://test:8080"

    def test_save_creates_dir(self, tmp_config):
        assert not os.path.isdir(tmp_config)
        config.save_config(config.DEFAULT_CONFIG)
        assert os.path.isfile(os.path.join(tmp_config, "config.json"))


class TestGetSet:
    def test_get_nested(self):
        config.save_config(config.DEFAULT_CONFIG)
        assert config.get("default.engine") == "auto"
        assert config.get("api.timeout") == 30

    def test_get_missing_returns_default(self):
        assert config.get("nonexistent.key", "fallback") == "fallback"

    def test_set_value(self):
        config.save_config(config.DEFAULT_CONFIG)
        config.set_value("api.timeout", 60)
        assert config.get("api.timeout") == 60

    def test_set_creates_nested(self):
        config.save_config(config.DEFAULT_CONFIG)
        config.set_value("custom.nested.key", "value")
        assert config.get("custom.nested.key") == "value"


class TestDeepMerge:
    def test_merge_preserves_defaults(self):
        base = {"a": 1, "b": {"x": 10, "y": 20}}
        override = {"b": {"y": 99}}
        result = config._deep_merge(base, override)
        assert result["a"] == 1
        assert result["b"]["x"] == 10
        assert result["b"]["y"] == 99

    def test_merge_adds_new_keys(self):
        base = {"a": 1}
        override = {"b": 2}
        result = config._deep_merge(base, override)
        assert result == {"a": 1, "b": 2}


class TestCookieFile:
    def test_get_cookie_file_default(self):
        path = config.get_cookie_file()
        assert "default.json" in path

    def test_get_cookie_file_named(self):
        path = config.get_cookie_file("work")
        assert "work.json" in path
