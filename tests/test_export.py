"""Tests for dy_cli.utils.export"""
import csv
import json

import pytest

from dy_cli.utils.export import export_data


@pytest.fixture
def sample_data():
    return [
        {"aweme_id": "111", "desc": "视频一", "stats": {"likes": 100, "comments": 20}},
        {"aweme_id": "222", "desc": "视频二", "stats": {"likes": 200, "comments": 40}},
    ]


class TestExportJson:
    def test_json(self, tmp_path, sample_data):
        path = str(tmp_path / "out.json")
        export_data(sample_data, path)

        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert len(loaded) == 2
        assert loaded[0]["aweme_id"] == "111"

    def test_unicode(self, tmp_path, sample_data):
        path = str(tmp_path / "out.json")
        export_data(sample_data, path)

        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "视频一" in text  # ensure_ascii=False


class TestExportCsv:
    def test_csv(self, tmp_path, sample_data):
        path = str(tmp_path / "out.csv")
        export_data(sample_data, path)

        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["aweme_id"] == "111"

    def test_csv_flattens_nested(self, tmp_path, sample_data):
        path = str(tmp_path / "out.csv")
        export_data(sample_data, path)

        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert "stats.likes" in rows[0]
        assert rows[0]["stats.likes"] == "100"

    def test_empty_data(self, tmp_path):
        path = str(tmp_path / "empty.csv")
        export_data([], path)
        # Should not crash


class TestExportYaml:
    def test_yaml(self, tmp_path, sample_data):
        path = str(tmp_path / "out.yaml")
        export_data(sample_data, path)

        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "aweme_id" in text


class TestExportAutoDetect:
    def test_unknown_ext_defaults_to_json(self, tmp_path, sample_data):
        path = str(tmp_path / "out.txt")
        export_data(sample_data, path)

        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert len(loaded) == 2
