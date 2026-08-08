"""Tests for the deterministic governance engine (对齐 xiaohongshu-matrices-cli)."""
from __future__ import annotations

import pytest

from dy_cli.dashboard.governance import (
    contains_high_risk_claim,
    contains_opt_out,
    contains_sensitive_information,
    evaluate_content,
    is_warm_lead,
    max_similarity,
    normalized_similarity,
)


def test_pii_phone():
    assert contains_sensitive_information("联系我手机号13812345678")
    assert not contains_sensitive_information("这是个普通视频没有联系方式")


def test_pii_wechat():
    assert contains_sensitive_information("加我微信 wxid_abc123xyz")
    assert contains_sensitive_information("微信号: TiktokFan2024")


def test_pii_email_and_idcard():
    assert contains_sensitive_information("邮箱 a.b@example.com")
    assert contains_sensitive_information("身份证 11010519900307201X")


def test_pii_qq_and_address():
    assert contains_sensitive_information("QQ: 12345678")
    assert contains_sensitive_information("地址: 北京市朝阳区幸福路1号")


def test_opt_out():
    assert contains_opt_out("不要再联系我了")
    assert contains_opt_out("我要退订")
    assert not contains_opt_out("这个视频拍得不错")


def test_high_risk_douyin_claims():
    assert contains_high_risk_claim("保证收益稳赚不赔")
    assert contains_high_risk_claim("专业刷量买粉上热门")
    assert contains_high_risk_claim("导流微信私域引流")
    assert not contains_high_risk_claim("今天天气真好")


def test_normalized_similarity_basics():
    assert normalized_similarity("你好世界", "你好世界") == 1.0
    assert normalized_similarity("", "x") == 0.0
    assert normalized_similarity("x", "") == 0.0
    # whitespace / punctuation should not change similarity
    assert normalized_similarity("你好，世界！", "你好世界") == 1.0
    assert normalized_similarity("今天吃苹果", "昨天买香蕉") < 0.5


def test_max_similarity():
    prev = ["苹果香蕉水果很好吃", "跑步运动身体更健康"]
    sim = max_similarity("苹果香蕉水果真好吃", prev)
    assert sim > 0.85
    assert max_similarity("火箭卫星宇宙飞船", prev) < 0.3


def test_evaluate_allows_clean_content():
    res = evaluate_content("分享一个旅行小技巧，记得带充电宝")
    assert res.decision == "allow"
    assert res.reasons == ()


def test_evaluate_blocks_pii():
    res = evaluate_content("私聊我微信 vx_abc123")
    assert res.decision == "block"
    assert res.sensitive is True
    assert any("微信" in r or "敏感" in r for r in res.reasons)


def test_evaluate_blocks_high_risk():
    res = evaluate_content("跟着买保证收益稳赚")
    assert res.decision == "block"
    assert res.sensitive is False


def test_evaluate_blocks_similar():
    res = evaluate_content("一模一样的推广文案", previous=["一模一样的推广文案"])
    assert res.decision == "block"
    assert res.similarity > 0.85


def test_evaluate_threshold_param():
    res = evaluate_content("差不多的文案内容", previous=["差不多的文案内容呀"], threshold=0.99)
    assert res.decision == "allow"  # below the raised threshold


def test_is_warm_lead():
    assert is_warm_lead("inbound_dm")
    assert is_warm_lead("brand_mention")
    assert not is_warm_lead("random_reason")
