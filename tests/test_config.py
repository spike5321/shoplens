"""配置模块的行为测试。

重点是「密钥不能泄露」这条真实风险：
* safe_dsn() 不能带出密码。
* Settings 的 repr / model_dump 不能带出密码明文。
* 健康接口只返回布尔值。
"""

from __future__ import annotations

from app.config import Settings


def _settings(**overrides) -> Settings:
    base = {
        "pg_host": "127.0.0.1",
        "pg_port": 55432,
        "pg_db": "shoplens",
        "pg_admin_user": "postgres",
        "pg_admin_password": "super-secret-admin-pw",
        "pg_app_user": "shoplens_app",
        "pg_app_password": "app-pw",
        "pg_reader_user": "analytics_reader",
        "pg_reader_password": "reader-pw",
        "deepseek_api_key": "sk-should-never-leak",
    }
    base.update(overrides)
    # _env_file=None 保证测试不读取磁盘上的 .env，结果只由本函数决定
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


def test_safe_dsn_hides_passwords():
    s = _settings()
    for role in ("admin", "app", "reader"):
        safe = s.safe_dsn(role)  # type: ignore[arg-type]
        for pw in ("super-secret-admin-pw", "app-pw", "reader-pw"):
            assert pw not in safe


def test_real_dsn_encodes_password_and_targets_configured_port():
    s = _settings(pg_admin_password="p@ss/w:rd")
    dsn = s.dsn("admin")
    # 密码里的特殊字符必须被 URL 编码，否则连接串会被解析错
    assert "p%40ss%2Fw%3Ard" in dsn
    assert dsn.endswith("/shoplens")
    assert ":55432/" in dsn


def test_secrets_are_masked_in_representations():
    s = _settings()
    dumped = str(s.model_dump())
    assert "sk-should-never-leak" not in dumped
    assert "super-secret-admin-pw" not in repr(s)
    assert "app-pw" not in repr(s)
    assert "reader-pw" not in repr(s)


def test_api_key_configured_flag_is_boolean_only():
    assert _settings().deepseek_api_key_configured is True
    assert _settings(deepseek_api_key="").deepseek_api_key_configured is False
    assert _settings(deepseek_api_key="   ").deepseek_api_key_configured is False


def test_default_provider_is_offline():
    """默认必须是离线假模型，这样没有 Key 也能跑完整套测试。"""
    assert Settings(_env_file=None).model_provider == "offline"  # type: ignore[call-arg]


def test_server_side_limits_cannot_be_set_above_plan(monkeypatch):
    """repair_count 上限被 pydantic 约束在 <= 1，配置写坏也进不来。"""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _settings(max_repair_count=5)


def test_env_file_is_anchored_to_repo_root_not_cwd():
    """.env 必须按绝对路径解析。

    回归测试：最早写成 env_file=".env" 时，从 backend/ 目录启动 uvicorn
    会读不到仓库根目录的 .env，表现是 /readyz 报「no password supplied」——
    配置明明填了却连不上库。改成基于模块位置反推仓库根目录后修好。
    """
    from pathlib import Path

    from app.config import _ENV_FILE, _REPO_ROOT

    assert _ENV_FILE.is_absolute(), "相对路径会随启动目录漂移"
    assert _ENV_FILE.parent == _REPO_ROOT
    assert _ENV_FILE.name == ".env"

    configured = Settings.model_config["env_file"]
    assert Path(str(configured)).is_absolute()
    assert Path(str(configured)) == _ENV_FILE
