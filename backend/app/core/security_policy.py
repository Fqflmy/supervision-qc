# -*- coding: utf-8 -*-
"""安全启动校验：拒绝以占位/弱配置启动生产环境。

背景
----
``JWT_SECRET`` 的占位值曾原样出现在公开仓库（``backend/.env.example``、
``deploy/.env.example`` 与 compose 的默认值都是
``please-change-this-secret-in-production``）。这意味着任何拿到仓库的人都能
**伪造任意用户的令牌**，直接绕过鉴权——属于最严重的配置类漏洞。

本模块采用「默认安全」策略：

- 开发环境（``environment != production``）：检测到占位值或过短密钥时
  **自动生成随机密钥**并告警，保证开箱即用且本地不残留弱密钥；
- 生产环境（``environment == production``）：检测到占位值、过短密钥或
  自动生成的临时密钥时 **直接拒绝启动**，避免带病上线。

另外提供 ``auth_security_warnings()``，在非生产环境把「无刷新令牌、令牌有效期
过长、默认管理员密码、登录未强制改密」等隐患显式打印出来，避免静默带病运行。
"""
from __future__ import annotations

import secrets
from typing import Any

from app.core.logging_conf import get_logger

logger = get_logger(__name__)

#: 判定为「占位值」的已知字符串（历史模板、文档示例、compose 默认值）
INSECURE_JWT_SECRETS = frozenset(
    {
        "please-change-this-secret-in-production",
        "please-change-this-secret",
        "change-me-in-production-please-32bytes-min",
        "change-me",
        "changeme",
        "secret",
        "your-secret-key",
        "test",
    }
)

#: 生产环境要求的最小长度（HS256 建议至少 32 字节熵）
MIN_JWT_SECRET_LENGTH = 32

#: 开发环境随机生成密钥时写入的哨兵值，供生产环境识别并拒绝
GENERATED_SECRET_SENTINEL = "__auto_generated_for_development__"


class InsecureConfigurationError(RuntimeError):
    """生产环境存在不安全的配置，拒绝启动。"""


def _is_placeholder(value: str) -> bool:
    return value.strip().lower() in INSECURE_JWT_SECRETS


def validate_jwt_secret(settings: Any, *, production: bool | None = None) -> str:
    """校验 ``jwt_secret``；必要时生成开发用随机密钥。

    返回最终应使用的密钥（会写回 ``settings.jwt_secret``）。
    生产环境不满足要求时抛 :class:`InsecureConfigurationError`。
    """
    is_production = (
        production
        if production is not None
        else str(getattr(settings, "environment", "dev")).strip().lower() == "production"
    )
    current = str(getattr(settings, "jwt_secret", "") or "")

    problems: list[str] = []
    if not current.strip():
        problems.append("为空")
    elif _is_placeholder(current):
        problems.append("使用了模板中的占位值")
    elif len(current) < MIN_JWT_SECRET_LENGTH:
        problems.append(f"长度不足（当前 {len(current)}，要求至少 {MIN_JWT_SECRET_LENGTH}）")

    if not problems:
        return current

    detail = "；".join(problems)

    if is_production:
        raise InsecureConfigurationError(
            "SUPERVISION_JWT_SECRET 配置不安全，已拒绝启动："
            f"{detail}。\n"
            "  该密钥用于签发/校验登录令牌，泄漏或过弱会导致任何人都能伪造令牌、绕过鉴权。\n"
            "  修复方式：生成一个足够长的随机串并写入 deploy/.env，例如：\n"
            '      python -c "import secrets;print(secrets.token_urlsafe(48))"\n'
            "      然后设置 SUPERVISION_JWT_SECRET=<生成值>（或 deploy/.env 的 JWT_SECRET）"
        )

    generated = secrets.token_urlsafe(48)
    settings.jwt_secret = generated
    logger.warning(
        "检测到不安全的 JWT 密钥，已自动生成随机密钥（仅限开发环境，重启后令牌失效）",
        extra={
            "problem": detail,
            "environment": getattr(settings, "environment", "dev"),
            "hint": '生产环境请显式设置 SUPERVISION_JWT_SECRET（python -c "import secrets;print(secrets.token_urlsafe(48))"）',
        },
    )
    return generated


def mark_auto_generated(settings: Any, generated_value: str) -> bool:
    """标记当前密钥是否为开发期自动生成（生产环境据此拒绝启动）。"""
    is_generated = str(getattr(settings, "jwt_secret", "")) == generated_value
    if is_generated:
        settings.jwt_secret_is_generated = True
    return is_generated


def assert_production_safe(settings: Any) -> None:
    """生产环境的强制校验入口（启动时调用，不通过则抛异常阻止启动）。"""
    if str(getattr(settings, "environment", "dev")).strip().lower() != "production":
        return
    if getattr(settings, "jwt_secret_is_generated", False):
        raise InsecureConfigurationError(
            "检测到 JWT 密钥是开发期自动生成的临时值，生产环境拒绝启动。\n"
            "  请显式设置 SUPERVISION_JWT_SECRET（长度 ≥ 32 的随机串）。"
        )
    validate_jwt_secret(settings, production=True)


def auth_security_warnings(settings: Any) -> list[str]:
    """返回非生产环境下值得关注的安全隐患提示（仅告警，不阻断）。"""
    tips: list[str] = []

    if getattr(settings, "access_token_expire_minutes", 0) > 60:
        tips.append(
            f"访问令牌有效期较长（{settings.access_token_expire_minutes} 分钟），"
            "无刷新令牌时泄漏窗口偏大，生产建议缩短"
        )
    if not getattr(settings, "refresh_token_enabled", False):
        tips.append("未启用刷新令牌（SUPERVISION_REFRESH_TOKEN_ENABLED=false），令牌过期需重新登录")
    if getattr(settings, "seed_default_admin", False):
        default_pwd = str(getattr(settings, "seed_default_admin_password", "") or "")
        if default_pwd == "Admin@12345":
            tips.append(
                "默认管理员密码仍是 Admin@12345（公开在仓库文档中），上线前必须修改，"
                "并建议设置 SUPERVISION_SEED_DEFAULT_ADMIN=false 关闭自动写入"
            )
        else:
            tips.append("允许写入默认管理员账号，请确认其密码已非默认值")
    if not getattr(settings, "force_password_change_on_first_login", False):
        tips.append("未强制首次登录修改密码")
    return tips
