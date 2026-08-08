"""Tests for dy_cli.utils.envelope"""
from dy_cli.utils.envelope import SCHEMA_VERSION, error_envelope, success_envelope


class TestEnvelope:
    def test_success(self):
        env = success_envelope({"items": [1, 2, 3]})
        assert env["ok"] is True
        assert env["schema_version"] == SCHEMA_VERSION
        assert env["data"] == {"items": [1, 2, 3]}

    def test_error(self):
        env = error_envelope("not_authenticated", "need login")
        assert env["ok"] is False
        assert env["schema_version"] == SCHEMA_VERSION
        assert env["error"]["code"] == "not_authenticated"
        assert env["error"]["message"] == "need login"

    def test_success_none_data(self):
        env = success_envelope(None)
        assert env["ok"] is True
        assert env["data"] is None

    def test_success_list_data(self):
        env = success_envelope([1, 2, 3])
        assert env["data"] == [1, 2, 3]
