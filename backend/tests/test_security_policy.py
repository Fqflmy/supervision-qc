# -*- coding: utf-8 -*-
"""安全启动校验的回归测试。

对应漏洞：JWT 占位密钥 `please-change-this-secret-in-production` 曾随公开仓库分发
（backend/.env.example、deploy/.env.example 与 compose 默认值都是它），
任何拿到仓库的人都能用它伪造令牌、绕过鉴权。

修复策略（默认安全）：
- 开发环境：占位/过短/空值 → 自动生成随机密钥并告警，保证开箱即用；
- 生产环境：占位/过短/自动生成 → **拒绝启动**。
"""
from __future__ import annotations

import pytest

from app.core.security_policy import (
    GENERATED_SECRET_SENTINEL,
    INSECURE_JWT_SECRETS,
    MIN_JWT_SECRET_LENGTH,
    InsecureConfigurationError,
    assert_production_safe,
    auth_security_warnings,
    validate_jwt_secret,
)


class _FakeSettings:
    """最小化的配置替身，避免触碰真实 .env 与目录创建。"""

    def __init__(self, **kwargs):
        self.environment = kwargs.pop("environment", "dev")
        self.jwt_secret = kwargs.pop("jwt_secret", "")
        self.jwt_secret_is_generated = kwargs.pop("jwt_secret_is_generated", False)
        self.access_token_expire_minutes = kwargs.pop("access_token_expire_minutes", 120)
        self.refresh_token_enabled = kwargs.pop("refresh_token_enabled", False)
        self.seed_default_admin = kwargs.pop("seed_default_admin", True)
        self.seed_default_admin_password = kwargs.pop("seed_default_admin_password", "Admin@12345")
        self.force_password_change_on_first_login = kwargs.pop(
            "force_password_change_on_first_login", False
        )
        for key, value in kwargs.items():
            setattr(self, key, value)


def test_placeholder_secret_blocks_production_startup():
    """生产环境使用占位密钥必须拒绝启动（本次修复的核心）。"""
    for placeholder in INSECURE_JWT_SECRETS:
        settings = _FakeSettings(environment="production", jwt_secret=placeholder)
        with pytest.raises(InsecureConfigurationError) as excinfo:
            validate_jwt_secret(settings, production=True)
        assert "拒绝启动" in str(excinfo.value)
        # 报错信息里要给出可执行的修复办法
        assert "token_urlsafe" in str(excinfo.value)


def test_short_secret_blocks_production_startup():
    """生产环境密钥长度不足也必须拒绝启动。"""
    short = "a" * (MIN_JWT_SECRET_LENGTH - 1)
    settings = _FakeSettings(environment="production", jwt_secret=short)
    with pytest.raises(InsecureConfigurationError) as excinfo:
        validate_jwt_secret(settings, production=True)
    assert "长度不足" in str(excinfo.value)


def test_empty_secret_blocks_production_startup():
    """生产环境密钥为空同样拒绝启动。"""
    settings = _FakeSettings(environment="production", jwt_secret="")
    with pytest.raises(InsecureConfigurationError):
        validate_jwt_secret(settings, production=True)


def test_strong_secret_passes_production():
    """足够强的密钥在生产环境应通过校验且不被改写。"""
    strong = "k" * 64
    settings = _FakeSettings(environment="production", jwt_secret=strong)
    assert validate_jwt_secret(settings, production=True) == strong
    assert settings.jwt_secret == strong


def test_development_auto_generates_secret():
    """开发环境遇到占位值应自动生成随机密钥，而不是直接崩溃。"""
    settings = _FakeSettings(environment="dev", jwt_secret="please-change-this-secret")
    generated = validate_jwt_secret(settings)
    assert generated != "please-change-this-secret"
    assert len(generated) >= MIN_JWT_SECRET_LENGTH
    assert settings.jwt_secret == generated


def test_development_generation_is_random_per_call():
    """每次生成的密钥必须不同（防止退化成固定值）。"""
    first = validate_jwt_secret(_FakeSettings(environment="dev", jwt_secret=""))
    second = validate_jwt_secret(_FakeSettings(environment="dev", jwt_secret=""))
    assert first != second


def test_production_rejects_auto_generated_secret():
    """生产环境即便密钥够长，只要是自动生成的临时值也拒绝启动。"""
    settings = _FakeSettings(
        environment="production",
        jwt_secret=GENERATED_SECRET_SENTINEL,
        jwt_secret_is_generated=True,
    )
    with pytest.raises(InsecureConfigurationError) as excinfo:
        assert_production_safe(settings)
    assert "自动生成" in str(excinfo.value)


def test_assert_production_safe_is_noop_in_dev():
    """非生产环境不应因为弱密钥而阻断启动。"""
    settings = _FakeSettings(environment="dev", jwt_secret="weak")
    assert_production_safe(settings)  # 不抛异常即可


def test_warnings_flag_default_admin_password():
    """默认管理员密码已公开在文档中，应产生告警。"""
    settings = _FakeSettings(seed_default_admin=True, seed_default_admin_password="Admin@12345")
    tips = auth_security_warnings(settings)
    assert any("Admin@12345" in t for t in tips), tips


def test_warnings_flag_disabled_refresh_token():
    settings = _FakeSettings(refresh_token_enabled=False)
    tips = auth_security_warnings(settings)
    assert any("刷新令牌" in t for t in tips), tips


def test_templates_do_not_ship_placeholder_secret():
    """配置模板不得再分发占位密钥（首次修复的直接目标）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in ("backend/.env.example", "deploy/.env.example", "deploy/docker-compose.yml"):
        text = (root / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if stripped.startswith(("JWT_SECRET=", "SUPERVISION_JWT_SECRET=")):
                value = stripped.split("=", 1)[1].strip()
                assert value == "" or value not in INSECURE_JWT_SECRETS, (
                    f"{rel} 仍分发占位密钥：{stripped}"
                )
