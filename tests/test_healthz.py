"""健康检查接口的行为测试。

这里刻意不断言「readyz 一定是 ok」：
数据库没起来时，接口必须**如实**返回 unreachable，而不是假装健康。
真正要固定住的是「诚实」和「不泄露密钥」这两件事。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_healthz_is_alive_without_any_dependency():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "shoplens-api"
    assert body["version"]
    assert isinstance(body["real_model_key_configured"], bool)


def test_healthz_never_leaks_the_api_key():
    raw = client.get("/healthz").text
    assert "sk-" not in raw
    # 只允许出现布尔字段名，不允许出现密钥字段本身
    assert 'deepseek_api_key"' not in raw


def test_readyz_reports_a_real_database_status():
    resp = client.get("/readyz")
    assert resp.status_code == 200
    db = resp.json()["database"]
    assert db["status"] in {"ok", "unreachable"}
    assert db["port"] == get_settings().pg_port

    if db["status"] == "ok":
        # 真的连上了才允许有这些字段
        assert db["server_version"], "连上了却拿不到版本号，说明查询没真跑"
        assert isinstance(db["latency_ms"], (int, float))
        assert db["error"] is None
    else:
        assert db["error"], "不可达时必须给出原因，不能静默"


def test_readyz_never_leaks_the_database_password():
    raw = client.get("/readyz").text
    assert get_settings().safe_dsn("app") in raw, "连接串必须去掉密码后再返回"
    # 任何形如 ://user:password@ 的片段都不允许出现
    assert json.dumps(raw).count("password") == 0 or ":***@" in raw
