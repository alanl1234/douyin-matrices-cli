"""Smoke tests for the new dashboard routes (accounts / engagement / searches).

Uses FastAPI's TestClient against a temp data dir. No real browser or queue is
started because ``DY_ORCHESTRATOR`` is unset.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dy_cli.dashboard.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DY_MATRICES_DATA", str(tmp_path / "web_data"))
    app = create_app()
    return TestClient(app)


def test_dashboard_index(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "douyin-matrices" in r.text.lower() or "账号" in r.text


def test_accounts_page_renders(client):
    r = client.get("/accounts")
    assert r.status_code == 200


def test_engagement_page_renders(client):
    r = client.get("/engagement")
    assert r.status_code == 200
    assert "shadow" in r.text or "inbound" in r.text or "reviewed" in r.text


def test_searches_page_renders(client):
    r = client.get("/searches")
    assert r.status_code == 200


def test_api_health_and_accounts(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    r = client.get("/api/accounts")
    assert r.status_code == 200
    assert "accounts" in r.json()


def test_api_searches_empty(client):
    r = client.get("/api/searches")
    assert r.status_code == 200
    assert r.json()["jobs"] == []


def test_create_search_job_redirects(client):
    r = client.post(
        "/searches",
        data={"name": "测试采集", "keywords": "美食, 探店", "max_pages": "2", "min_likes": "0"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)

    r2 = client.get("/api/searches")
    jobs = r2.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["name"] == "测试采集"
